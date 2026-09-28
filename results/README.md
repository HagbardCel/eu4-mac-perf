# Run outputs (`results/`)

This directory holds timestamped benchmark captures. Git tracks **derived evidence** only; bulky raw captures stay local.

## Tracked in git (Tier B)

- `metadata.json`, `manifest.json` — provenance (paths use `$HOME/` placeholders)
- `summary.md`, `summary.json`, `validation.md`, `validation.json`, `root-cause.md`, `root-cause.json`
- `*.csv` except `telemetry.csv`
- `events.jsonl`, small `*.bin` control files, `cpu-sample.txt`, `sample.stderr`
- `autonomous-reproducibility.json`

## Local only (gitignored)

- `**/*.pliststream`, `**/powermetrics.stderr` — raw `powermetrics` streams
- `**/*.png` — scene screenshots and diagnostic images
- `**/telemetry.csv` — high-volume diagnostic series (summaries remain tracked)
- `**/trace.bin` — high-volume draw records (derived `draw-screening.*` remains tracked)
- `_save_backups/` — save-file recovery copies

Regenerate ignored artifacts with the commands in the project [README](../README.md) (`record`, `diagnostic`, `autonomous_runner`, etc.).

## Fixtures

The [`fixtures/`](../fixtures/) tree is also **local only**. Use GOG disposable saves and `register-scene` as documented in the README.

## Path sanitization

Before publishing JSON from a new machine, run:

```sh
python3 benchmark/sanitize_paths.py results/
```

See also [INDEX.md](INDEX.md) for landmark runs.
