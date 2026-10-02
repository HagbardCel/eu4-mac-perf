# Held-out Tier-1 recipe fixture

Stage 2 requires a **hash-frozen** held-out structural recipe (`terrain_surrogate`) that is
chosen and committed **before** its first performance measurement.

## Tracked paths

```
analysis/held-out/
  frame-model-held-out-terrain-surrogate.recipe
  frame-model-held-out-terrain-surrogate.json
```

Legacy copies under `analysis/fixtures/` are **not** accepted for admission (that path is
gitignored and would bypass the clean-tree invariant).

## Freeze workflow (Mac + passive trace)

Select a structural slice that was **not** used to tune thresholds. Commit exact recipe
bytes and metadata, then run preflight:

```bash
python3 - <<'PY'
import hashlib, json, shutil, tempfile
from pathlib import Path
import sys
sys.path.insert(0, "benchmark")
import frame_model_workload as workload

dest_dir = Path("analysis/held-out")
dest_dir.mkdir(parents=True, exist_ok=True)
# Build candidate offline (development only — do not archive as validation until frozen):
with tempfile.TemporaryDirectory() as tmp:
    raise SystemExit(
        "Choose a fresh held-out slice offline, write .recipe bytes, then set sha256 in .json"
    )
PY
```

After the `.recipe` and `.json` (with matching `sha256`) are committed, preflight may set
`include_held_out=True` and measure the held-out role once.

## Exploratory evidence

The archive `frame-model-offline-ab00679-20261002T182324.368965Z-4f253c80.json` recorded a
`terrain_surrogate` held-out workload **before** a repository fixture existed. Preserve it
as honest failed admission / exploratory evidence; do not treat it as the mandatory
no-peeking held-out validation.

Dynamic trace-derived held-out recipes are **disabled** for admission: without a frozen
fixture, causal admission reports `held-out validation absent` (`unavailable`).
