# Reverse engineering

Recover and understand the original EU IV macOS implementation. Phase 1 (PR D) provides Ghidra headless export, a local SQLite searchable corpus, and renderer-size census tooling.

## Local corpus (not in Git)

```text
.local/eu4/<binary-sha256>/
  ghidra_project.json
  ghidra-project/
  exports/<baseline-revision>/
    inventory/
    inventory.staging/   # Ghidra export scratch; promoted to inventory/ once validated
    inventory.backup/    # transient during inventory regen; removed after provenance update
    decompiled/
      .archive/<iso>/    # superseded batch-*.jsonl after manifest inventory/partition change
    index.sqlite
    provenance.json
    logs/
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
| `EU4_RE_EXPORT` | Override `.local/eu4/<sha>/` root |

## Ghidra lifecycle

- **Never** use `-deleteProject` in automation.
- **D1 `import`**: `-import` with full analysis (no `-readOnly`). Fails if `ghidra-project/` already exists.
- **D2/D3**: `-process <program_name> -noanalysis -readOnly` with `-scriptPath reverse_engineering/ghidra`.
- `-process` uses the **program name** inside the project (usually `eu4`), not the Ghidra project name (`EU4_<sha8>`). Recorded in `ghidra_project.json` after import.
- Reset: manually delete `ghidra-project/` and `exports/` if a full re-analysis is required.

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

## Inventory notes

[`ImportAndInventory.java`](ghidra/ImportAndInventory.java) records call instructions from decoded code; **tail-call jumps may be missing** from `call_edges.jsonl` until a later fix.

Regenerating inventory replaces `inventory/` via a short-lived `inventory.backup/` rollback path. Changing only call edges or coverage updates the bundle digest without requiring a new `baseline_revision`.

Regenerating the batch manifest **archives** existing `batch-*.jsonl` under `decompiled/.archive/<iso>/` when the functions digest or batch partition changes, so stale pseudocode is not indexed as current.

## Analysis revisions

Each `exports/<baseline-revision>/` tree is the working export root. `inventory_digest` (functions only) drives batch partitioning; `inventory_bundle_digest` includes call edges and coverage.

`provenance.json` records `ghidra_version` and script hashes from import for diagnostics; resumed work on the same revision does not block on toolchain equality.

## Reports (Git)

Authoritative census files are generated only after `corpus qualify` — see [`reports/README.md`](reports/README.md). Bounded classification QC: [`reports/classification-qc.md`](reports/classification-qc.md).

## Tests

Portable unit tests use [`tests/re_fixtures/`](../tests/re_fixtures/). CI runs `python -m compileall` on Python only; **Java Ghidra scripts are not compiled in CI**.

### Pre-merge EU IV integration checklist (maintainer)

No EU IV game process required. Summarize results in PR #48 before merge.

1. **`corpus import`** — Ghidra imports the pinned EU IV binary (`target.json` SHA).
2. **`corpus smoke`** — all three renderer anchors decompile successfully (`SmokeDecompile.java`).
3. **`corpus inventory`** — real `functions.jsonl`, `call_edges.jsonl`, `coverage.json`.
4. **Anchor pseudocode (manual headless)** — for the three anchor **entry RVAs** from inventory, run `analyzeHeadless` with `-scriptPath reverse_engineering/ghidra` and `ExportDecompilation.java` (same argv layout as [`build_analyze_headless_argv`](corpus.py): project dir/name, `-process <program_name> -noanalysis -readOnly`, RVA list file, output jsonl path, timeout seconds).
5. **One manifest batch + index** — `corpus manifest`, then pick one batch id (e.g. `batch-00001`) and its RVA list from `decompiled/batch_manifest.json`. Run headless `ExportDecompilation.java` (same argv as step 4: RVA list file, output path, timeout). Write the validated export to `decompiled/<batch-id>.jsonl` (use a `*.jsonl.staging` file and `publish_batch_staging` from Python, or match manifest RVAs exactly). **Do not** use `corpus decompile` for a single batch—it processes the full manifest. Then `corpus index` → `search symbol` / `callers` / `callees` / `text` on anchor symbols (reuse a batch that contains an anchor if convenient).

Do **not** expect the index to load ad-hoc RVA exports without a manifest batch — `index` reads manifest-listed `batch-*.jsonl` only.

### Maintainer Ghidra smoke (toy binary)

Do **not** use `corpus import` with a toy binary — import enforces the pinned EU IV SHA.

To validate Java script schemas locally: build a tiny x86-64 Mach-O, run headless with `ImportAndInventory.java`, confirm the three inventory files are written.

Full measured EU IV census (`measurement_source: local_ghidra_export`) is a follow-up PR after qualified export.
