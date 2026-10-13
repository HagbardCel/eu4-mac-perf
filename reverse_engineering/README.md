# Reverse engineering

Recover and understand the original EU IV macOS implementation. Phase 1 (PR D) provides Ghidra headless export, a local SQLite searchable corpus, and renderer-size census tooling.

## Local corpus (not in Git)

```text
.local/eu4/<binary-sha256>/
  ghidra_project.json
  ghidra-project/
  exports/<baseline-revision>/
    inventory/
    decompiled/
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

## Pipeline (D1–D5)

```bash
export GHIDRA_INSTALL=/path/to/ghidra

python3 -m reverse_engineering.corpus preflight
python3 -m reverse_engineering.corpus import
python3 -m reverse_engineering.corpus inventory
python3 -m reverse_engineering.corpus manifest
python3 -m reverse_engineering.corpus decompile --pilot 250
python3 -m reverse_engineering.corpus decompile
python3 -m reverse_engineering.corpus index
python3 -m reverse_engineering.corpus reconcile
python3 -m reverse_engineering.corpus check-anchors
python3 -m reverse_engineering.census --write-reports
```

### Quality gates

- **Export completeness** \(C_{\text{export}}\): 100% of manifest RVAs have a terminal batch record.
- **Decompilation success** \(C_{\text{success,bytes}}\): reported in `logs/decompile-report.json`; threshold set after pilot (default 0.85). Mandatory renderer anchors must decompile with `success`.
- Do not describe the corpus as fully usable if success bytes fall below the declared threshold.

### Search CLI

```bash
python3 -m reverse_engineering.search symbol SShaderOpenGL
python3 -m reverse_engineering.search text glDrawElements
python3 -m reverse_engineering.search function 0xRVA
python3 -m reverse_engineering.search callers 0xRVA
python3 -m reverse_engineering.search callees 0xRVA
```

## Analysis revisions

Each `exports/<baseline-revision>/` tree is immutable for batch export. If Ghidra analysis changes, create a new revision directory. `inventory_digest` detects inventory drift; it does not capture every Ghidra type change.

## Reports (Git)

Authoritative census files are generated only after `corpus qualify` — see [`reports/README.md`](reports/README.md). Bounded classification QC: [`reports/classification-qc.md`](reports/classification-qc.md).

`corpus import` creates the Ghidra project; `corpus smoke` is retryable and does not delete the project on failure.

## Tests

Portable unit tests use [`tests/re_fixtures/`](../tests/re_fixtures/). Full Ghidra integration is a local maintainer step before merging measured EU IV census numbers (`measurement_source: local_ghidra_export`).
