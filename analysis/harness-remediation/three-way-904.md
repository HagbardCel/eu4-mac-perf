# Same-seal three-way comparison — present 904

Dated offline recomputation from stored evidence. No EU4 launch and no new
capture. This report establishes what the stored control does and does not
show; it does **not** establish renderer parity.

## Identity

| Field | Value |
|---|---|
| Session | `afd444f2-6478-459f-8db6-e565c49dbd63` |
| Sealed frame | `.local/m7/goal-round1/capture-c/frame` |
| Stream | `8801f037a06ed618e973f081fa5a20dca5a705124f92fca50c9d12554e3dd547` |
| Present | 904 |
| Operations | 327 |
| Size | 1728 × 1117, BGRA8, lower-left |
| Checkpoint request | `.local/m7/goal-round1/capture-c/shadow/checkpoint-request.json` |
| Metal replay | `.local/m7/goal-round1/capture-c/shadow` |

## Inputs

| Role | File | SHA-256 |
|---|---|---|
| Live GL, checkpoint 15 | `capture-c/frame/blobs/default-checkpoint-3f7126…bin` | `3f71265b4e0c7afe9d92213bc679cd32c7625f39dc94a5ea1778abce3eae2232` |
| Metal, checkpoint 15 | `capture-c/shadow/intermediate-event15-tex0.bgra` | `dc2602847b06c23d068eaa599d4c38d99e747b9f4eebfaad69aad4750c798c11` |
| Offline GL control, draw 14 | `experiments/gl-c-comp-event14/result.bgra` | `dfe9c8bcdde58b30f72759863c3a46106364dd6c1d2a5f72f121e2a16a1e5dc8` |
| Live GL, checkpoint 16 | `capture-c/frame/blobs/default-checkpoint-698e92…bin` | `698e928b92bcb915e37366ca507f4a60bec8150e8ffada8d7200bbc1e046a671` |
| Metal, checkpoint 16 | `capture-c/shadow/intermediate-event16-tex0.bgra` | `42e8ab2ae30dcbc75199ade4224691097ecc94869e421e4533eec01300035613` |
| Offline GL control, draw 15 | `experiments/gl-c-comp-event15/result.bgra` | `ae2b63ff07ac9a4af932b43e5b050a55cb94e0729cd1ddf81477d1400ddc20e8` |

All three images are exactly 1728×1117×4 bytes. The control manifests name the
same capture blobs as seed/reference; the control was executed against the
compressed-input experiment for this capture, not a DDS substitution.

## Results (all four BGRA channels)

Counts are pixels with any unequal byte; alpha is the number with an unequal
alpha byte; max is over all bytes.

| Checkpoint | Offline GL vs live GL | Offline GL vs Metal | Metal vs live GL |
|---|---:|---:|---:|
| 15 | **5 / 1**, alpha 0 | **7 / 1**, alpha 0 | **4 / 1**, alpha 0 |
| 16 | **4,645 / 86**, alpha 0 | **5,770 / 119**, alpha 0 | **3,602 / 119**, alpha 0 |

These agree with the review's stored-control figures for 904/checkpoint 16 and
with the candidate Metal result (4/1 at 15, 3,602/119 at 16). They do not agree
with the earlier 916 numbers; different present, draw count and content.

## Interpretation

- **Verified:** this stored offline GL control does not reproduce live GL
  exactly at either checkpoint (5/1 and 4,645/86).
- **Verified:** the Metal result is not exactly equal to live GL (4/1 and
  3,602/119) and not equal to this control.
- **Not established:** whether the remaining difference is input
  representation, depth/sample/drawable provenance, shader translation or
  another Metal state. The control has no authentic pre-draw depth image and
  uses a color-seeded RGBA8 FBO; a live drawable with different depth precision
  or samples would produce a different result.
- **Not established:** parity, event-16 equivalence, a contiguous verified
  prefix, or an accepted correction. Do not claim any of these from this table.

## Reproduce

```sh
python3.14 - <<'PY'
import json, numpy as np
from pathlib import Path
base = Path('.local/m7/goal-round1')
req = json.loads((base/'capture-c/shadow/checkpoint-request.json').read_text())
by = {c['event']: c for c in req['checkpoints']}
def load(path): return np.fromfile(path, dtype=np.uint8).reshape(1117, 1728, 4)
for event, control in ((15, base/'experiments/gl-c-comp-event14/result.bgra'),
                       (16, base/'experiments/gl-c-comp-event15/result.bgra')):
    live = load(base/'capture-c/frame/blobs'/by[event]['reference_file'])
    metal = load(base/'capture-c/shadow'/f'intermediate-event{event}-tex0.bgra')
    gl = load(control)
    for name, left, right in (('GL-live', gl, live),
                              ('GL-Metal', gl, metal),
                              ('Metal-live', metal, live)):
        delta = np.abs(left.astype(np.int16) - right.astype(np.int16))
        print(event, name, int(np.any(delta != 0, axis=2).sum()),
              int(delta.max()), int(np.count_nonzero(delta[:, :, 3])))
PY
```

## Smallest next discriminator

Obtain one authentic pre-draw depth/sample/drawable observation for draw 14 on
this seal and reconstruct the control with that state. If the control then
matches live GL at checkpoint 15 exactly, the remaining Metal residual is a
renderer-side candidate; if it still differs, the missing observation is the
next input to identify. Do not rerun the broad DDS search or a window drawable
control: the window control already showed 1.4M alpha mismatches.
