# Reverse engineering

Recover and understand the original EU IV macOS implementation. Phase 1 (PR D) provides Ghidra headless export, a local SQLite searchable corpus, and renderer-size census tooling.

## Local corpus (not in Git)

```text
.local/eu4/<binary-sha256>/          # corpus (inventory, exports, index)
  ghidra_project.json                 # pointer to external Ghidra project
  exports/<baseline-revision>/
    logs/program_metadata.json        # language_id / compiler_spec_id after import
    inventory/
    inventory.staging/   # Ghidra export scratch; promoted to inventory/ once validated
    inventory.backup/    # transient during inventory regen; removed after provenance update
    decompiled/
      .archive/<iso>/    # superseded batch-*.jsonl after manifest inventory/partition change
    index.sqlite
    provenance.json
    logs/

~/GhidraEU4/<binary-sha256>/        # Ghidra project (headless rejects dot-prefixed paths)
  ghidra-project/
```

Pin identity: [`target.json`](target.json) (SHA-256, default GOG executable path). Tooling does **not** import `benchmark/` at runtime; CI cross-checks the SHA against `benchmark/eu4_engine_inventory.py`.

## Requirements

- GOG EU IV v1.37.5 x86-64 macOS executable (see `target.json`)
- Ghidra with headless analyzer (`GHIDRA_INSTALL` → Ghidra root containing `support/analyzeHeadless`)
- Java runtime compatible with your Ghidra build (on Apple Silicon, verify the **decompiler** runs in preflight)

## Environment

| Variable | Purpose |
|----------|---------|
| `GHIDRA_INSTALL` | Ghidra installation root |
| `EU4_EXE` | Override path to `eu4` binary |
| `EU4_RE_EXPORT` | Override corpus root; Ghidra `ghidra-project/` is co-located under this path (no `.local`) |
| `EU4_GHIDRA_WORK` | Optional parent of `~/GhidraEU4/<sha>/` when corpus stays in `.local` |

Ghidra headless **cannot** open a project under paths with a component starting with `.` (e.g. `.local`). Default layout keeps corpus in-repo under `.local` and the Ghidra database under `~/GhidraEU4/<sha>/ghidra-project/`.

**Workaround (no code change):** `export EU4_RE_EXPORT="$HOME/GhidraEU4"` and `mkdir -p "$EU4_RE_EXPORT"` relocates corpus and Ghidra together outside `.local`. Old failed projects under `.local/.../ghidra-project` are not migrated automatically.

## Ghidra lifecycle

- **Never** use `-deleteProject` in automation.
- **D1 `import`**: `-import` with full analysis (no `-readOnly`); Mach-O loader picks processor and `gcc` compiler spec (no `-processor` override). Fails if the external `ghidra-project/` already exists.
- **D2/D3**: `-process <program_name> -noanalysis -readOnly` with `-scriptPath reverse_engineering/ghidra`.
- `-process` uses the **program name** inside the project (usually `eu4`), not the Ghidra project name (`EU4_<sha8>`). Recorded in `ghidra_project.json` after import.
- Reset: delete `~/GhidraEU4/<sha>/ghidra-project/` (or under `EU4_RE_EXPORT`) and corpus `exports/` if a full re-analysis is required.
- If `corpus inventory` fails, inspect `exports/<revision>/inventory.staging/` (the error lists each expected file as MISSING or byte size) and Ghidra `~/Library/ghidra/ghidra_*/application.log` for `ImportAndInventory` before re-running inventory (re-run deletes staging).

## Pipeline

### Essential (explore renderer code)

```bash
export GHIDRA_INSTALL=/path/to/ghidra

python3 -m reverse_engineering.corpus preflight
python3 -m reverse_engineering.corpus import
python3 -m reverse_engineering.corpus inventory
python3 -m reverse_engineering.corpus manifest
python3 -m reverse_engineering.corpus decompile    # exploratory; per-function failed/timeout OK
python3 -m reverse_engineering.corpus index
python3 -m reverse_engineering.search symbol SShaderOpenGL
```

`corpus index` always writes `exports/<rev>/index.sqlite`. It indexes manifest-listed batches when present and valid; missing or invalid batches are skipped with warnings. If `batch_manifest.json` is stale vs current `functions.jsonl`, the index is **inventory-only** until you run `corpus manifest`.

`corpus decompile` writes `logs/decompile-report.json` only (no automatic `qualification` in provenance). Exit code **0** when headless orchestration completes; use `corpus qualify` before git census reports.

### Optional

| Command | Purpose |
|---------|---------|
| `smoke` | Retryable anchor decompile check (`SmokeDecompile.java`; no batch export) |
| `qualify` | Persist `qualification.passed` in provenance for census gates |
| `decompile --pilot N` | Stratified sample under `logs/pilot/` |
| `reconcile` | Gfx draw-site vs inventory |
| `check-anchors` | Mandatory renderer symbols in batch export |
| `census --write-reports` | Git reports (requires qualified export or `--allow-unqualified` → `.local/`) |

### Search CLI

```bash
python3 -m reverse_engineering.search symbol SShaderOpenGL
python3 -m reverse_engineering.search text glDrawElements
python3 -m reverse_engineering.search function 0xRVA
python3 -m reverse_engineering.search callers 0xRVA
python3 -m reverse_engineering.search callees 0xRVA
```

## Graph analysis (static inventory)

After `corpus inventory` / `corpus index`, analyze the recovered call graph without re-running Ghidra:

```bash
python3 -m reverse_engineering.graph_analysis audit --baseline-revision <rev>
python3 -m reverse_engineering.graph_analysis extract --baseline-revision <rev> --write-report
```

Outputs land under `exports/<rev>/logs/` (`call-graph-audit.json`, `graphics-subgraph.json`, optional `graphics-architecture-report.md`). Review before copying anything into git.

Graph evidence uses a recomputed `inventory_bundle_digest` (must match `provenance.json` for authoritative boundary conclusions unless `--allow-digest-mismatch`). Seed definitions: [`graphics_seeds.json`](graphics_seeds.json).

Exporter improvements (tail jumps, direct thunk RVAs) are deferred until an audit shows a material graphics gap.

## Inventory notes

[`ImportAndInventory.java`](ghidra/ImportAndInventory.java) records call instructions from decoded code; **tail-call jumps may be missing** from `call_edges.jsonl` until a later fix.

Regenerating inventory replaces `inventory/` via a short-lived `inventory.backup/` rollback path. Changing only call edges or coverage updates the bundle digest without requiring a new `baseline_revision`.

Regenerating the batch manifest **archives** existing `batch-*.jsonl` under `decompiled/.archive/<iso>/` when the functions digest or batch partition changes, so stale pseudocode is not indexed as current.

## Callsite audit (instruction coverage)

[`ExportCallsites.java`](ghidra/ExportCallsites.java) walks **all decoded instructions in executable memory** (including outside recognized functions), classifies call sites via P-code `CALL`/`CALLIND` and flow-type, and does **not** resolve call targets. [`callsite_audit.py`](callsite_audit.py) reconciles that census against existing `inventory/call_edges.jsonl` without modifying inventory, manifests, or `provenance.json`.

```bash
python3 -m reverse_engineering.callsite_audit --baseline-revision <rev>
```

Each run uses a fresh temporary staging directory (removed in `finally`). Ghidra writes `callsites-v1.jsonl` and `scan-meta.json` there; only these are published under the export tree:

```
exports/<baseline-revision>/audits/
  callsites-v1.jsonl
  callsite-audit.json
```

**Identity gates:** pinned executable SHA on disk, `inventory_bundle_digest` must match `provenance.json`, and Ghidra `executable_sha256` from `scan-meta.json` must match the pinned binary.

**Coverage partition** (every census callsite exactly one bucket): `matched`, `missing_direct`, `missing_indirect`, `missing_ambiguous`. Classifier disagreement and calls outside functions are separate diagnostics. Orphan graph edges (edge RVA not in census) are listed separately.

**Publication:** if `audits/` is absent, both files are written atomically via a same-filesystem directory rename. If `audits/` exists with identical bytes, the command succeeds without rewriting. If `audits/` is incomplete or differs, the command fails; remove or rename `audits/` explicitly before replacing.

**Out of scope:** indirect target resolution, tail-call `jmp` recovery, decompilation, EU IV execution, changes to qualification or graphics graph-cut verdicts.

**Tests:** `python3 -m unittest tests.test_callsite_audit` (portable). With `GHIDRA_INSTALL` set: build [`tests/re_fixtures/callsite_mini`](../tests/re_fixtures/callsite_mini) via `build_callsite_mini.sh`, then `python3 -m unittest tests.test_ghidra_export_callsites`.

**Pre-merge:** run one full offline audit on the qualified EU IV Ghidra project (e.g. `auto-v1-20261013`) and attach summary metrics to the PR.

## Analysis revisions

Each `exports/<baseline-revision>/` tree is the working export root. `inventory_digest` (functions only) drives batch partitioning; `inventory_bundle_digest` includes call edges and coverage.

`provenance.json` records `ghidra_version` and script hashes from import for diagnostics; resumed work on the same revision does not block on toolchain equality.

## Reports (Git)

Authoritative census files are generated only after `corpus qualify` — see [`reports/README.md`](reports/README.md). Bounded classification QC: [`reports/classification-qc.md`](reports/classification-qc.md).

## Tests

Portable unit tests use [`tests/re_fixtures/`](../tests/re_fixtures/). CI runs `python -m compileall` on Python only; **Java Ghidra scripts are not compiled in CI**.

### Pre-merge EU IV integration checklist (maintainer)

No EU IV game process required. Summarize results in PR #48 before merge.

1. **`corpus import`** — Ghidra imports the pinned EU IV binary (`target.json` SHA). Unset `EU4_RE_EXPORT` / `EU4_GHIDRA_WORK` when testing the split default. After success:
   - `cat .local/eu4/<sha>/ghidra_project.json` — `project_dir` must not contain a dot-prefixed path component.
   - `cat .local/eu4/<sha>/exports/<baseline-revision>/logs/program_metadata.json` — expect `"language_id": "x86:LE:64:default"` and `"compiler_spec_id": "gcc"` (among other fields).
2. **`corpus smoke`** — all three renderer anchors decompile successfully (`SmokeDecompile.java`).
3. **`corpus inventory`** — real `functions.jsonl`, `call_edges.jsonl`, `coverage.json`.
4. **Anchor pseudocode (manual headless)** — for the three anchor **entry RVAs** from inventory, run `analyzeHeadless` with `-scriptPath reverse_engineering/ghidra` and `ExportDecompilation.java` (same argv layout as [`build_analyze_headless_argv`](corpus.py): project dir/name, `-process <program_name> -noanalysis -readOnly`, RVA list file, output jsonl path, timeout seconds).
5. **One manifest batch + index** — `corpus manifest`, then pick one batch id (e.g. `batch-00001`) and its RVA list from `decompiled/batch_manifest.json`. Run headless `ExportDecompilation.java` (same argv as step 4: RVA list file, output path, timeout). Write the validated export to `decompiled/<batch-id>.jsonl` (use a `*.jsonl.staging` file and `publish_batch_staging` from Python, or match manifest RVAs exactly). **Do not** use `corpus decompile` for a single batch—it processes the full manifest. Then `corpus index` → `search symbol` / `callers` / `callees` / `text` on anchor symbols (reuse a batch that contains an anchor if convenient).

Do **not** expect the index to load ad-hoc RVA exports without a manifest batch — `index` reads manifest-listed `batch-*.jsonl` only.

### Maintainer Ghidra smoke (toy binary)

Do **not** use `corpus import` with a toy binary — import enforces the pinned EU IV SHA.

To validate Java script schemas locally: build a tiny x86-64 Mach-O, run headless with `ImportAndInventory.java`, confirm the three inventory files are written.

Full measured EU IV census (`measurement_source: local_ghidra_export`) is a follow-up PR after qualified export.
