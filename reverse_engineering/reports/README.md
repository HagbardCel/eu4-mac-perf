# Renderer census reports (generated)

`renderer-size-census.json` and `renderer-size-census.md` are **not** checked into git until a
qualified Ghidra export exists (`corpus qualify` with `qualification.passed`).

Generate after a full local export:

```bash
python3 -m reverse_engineering.census --write-reports
```

For unqualified diagnostic output only:

```bash
python3 -m reverse_engineering.census --write-reports --allow-unqualified
```

That writes under `.local/` inside the export tree and does not overwrite this directory.
