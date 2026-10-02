# Held-out Tier-1 recipe fixture

Stage 2 requires a **hash-frozen** held-out structural recipe (`terrain_surrogate`) that is chosen before performance measurement.

On a Mac machine with the passive draw trace available, generate and commit:

```bash
python3 - <<'PY'
import hashlib, json, tempfile
from pathlib import Path
import sys
sys.path.insert(0, "benchmark")
import frame_model_workload as workload

root = Path("analysis/fixtures")
root.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    recipe = workload.held_out_recipe(Path(tmp))
    payload = Path(recipe["path"]).read_bytes()
    dest = root / "frame-model-held-out-terrain-surrogate.recipe"
    dest.write_bytes(payload)
    meta = {k: recipe[k] for k in recipe if k != "path"}
    meta["sha256"] = hashlib.sha256(payload).hexdigest()
    (root / "frame-model-held-out-terrain-surrogate.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(meta["sha256"], meta["draws"], "draws")
PY
```

Until the fixture is committed, preflight builds the held-out recipe from the local trace at run time (same categories, non-frozen path).
