# Run outputs (`results/`)

This directory holds timestamped benchmark captures. Git tracks **derived evidence** only; bulky raw captures stay local.

## Tracked in git (Tier B)

- `metadata.json`, `manifest.json` — provenance (paths use `$HOME/` placeholders)
- `summary.md`, `summary.json`, `validation.md`, `validation.json`, `root-cause.md`, `root-cause.json`
- `*.csv` except `telemetry.csv`
- `events.jsonl`, small `*.bin` control files, `cpu-sample.txt`, `sample.stderr`
- `autonomous-reproducibility.json`
- Phase C forensic capsule ([`20261003T210202Z-intrusive-diagnostic`](20261003T210202Z-intrusive-diagnostic/)): `manifest.json`, `events.jsonl`, `auto-probe.csv`, `control.bin`, `telemetry.csv.gz`, `power.samples.json.gz`, `powermetrics.pliststream.gz`, `powermetrics.stderr`, `ready-scene.png`, `game-ready.log` (when anchored), `report.*`, `salvage-report.*`, `raw_evidence.json`

`raw_evidence.json` includes a `capture_inventory` (SHA-256 + bytes for every committed capsule file **except** `raw_evidence.json` itself) and logical-content hashes for gzip streams.

### Phase C closure order (no new live capture)

```text
intrusive-salvage → seal-evidence --profile intrusive_complete_v1 → verify-evidence
→ delete uncompressed telemetry/power/pliststream locally (optional)
→ git add updated capsule files (historical manifest.json must stay byte-identical)
```

## Local only (gitignored)

- `**/*.pliststream` (uncompressed); `**/powermetrics.stderr` except the Phase C canonical run (see `.gitignore` negation)
- `**/*.png` except Phase C `ready-scene.png` negation
- `**/telemetry.csv`, `**/power.samples.json` — uncompressed captures (gzip companions may be tracked)
- `**/trace.bin` — high-volume draw records (derived `draw-screening.*` remains tracked)
- `_save_backups/` — save-file recovery copies

Regenerate ignored artifacts with the commands in the project [README](../README.md) (`record`, `diagnostic`, `autonomous_runner`, etc.).

## Fixtures

The [`fixtures/`](../fixtures/) tree is also **local only**. Use GOG disposable saves and `register-scene` as documented in the README. Phase C `raw_evidence.json` records reference-scene SHA-256 identity from the fixture manifest at seal time.

## Path sanitization

Before publishing JSON from a new machine, run:

```sh
python3 benchmark/sanitize_paths.py results/
```

See also [INDEX.md](INDEX.md) for landmark runs.
