# Live Simple draw evidence

Status: **authentic live capture succeeded** on the pinned GOG EU4 1.37.5
x86_64 executable, through the Metal replacement, without a synthetic scene.

Artifacts live under `.local/gfx-experiments/` (gitignored). This note pins the
provenance; the raw bytes and PNG previews stay local.

## Successful checkpoint

| Field | Value |
| --- | --- |
| Experiment | `.local/gfx-experiments/simple-02/draw-03` |
| Checkpoint | `after-draw`, occurrence 1 |
| Caller | `0xedfcd1` (`CEU3Graphics::ShowBasicLoadText` call site) |
| Executable | `/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4` |
| Executable SHA-256 | `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` |
| Replacement library SHA-256 | `e3582b1aa6fd28b5847bce144dd4b99b3a916cb4b2fd4b0efa717a011ac16333` |
| Game PID | 86305 |
| Target | 3456×2234, row stride 13824, `BGRA8Unorm`, generation 1 |
| GPU command status | `MTLCommandBufferStatusCompleted` (4) |
| Shader identity | `simple-lua-Simple/asset-maincode-v0` |
| Vertex state | slot 0, offset 0, stride 24, 96 bytes |
| Texture | 2048×1536 (closest content match: `load_0.dds`) |
| Logical viewport | 0, 0, 1728, 1117 (physical drawable is 2×) |
| Constant bytes | `0000003b…224b463b…0000803f…0000803f` = `diag(1/512, 2/661, 1, 1)` |
| Raw BGRA SHA-256 | `8b8eed11e86f84b70e58e1ee58f4a2ccc31e52285add7cee63ebd9cc3fa4e524` |
| Raw size | 30,882,816 bytes |

Status levels from `probe-result.json`:

```json
{
  "draw_reached": true,
  "draw_encoded": true,
  "gpu_completed": true,
  "pixels_captured": true,
  "presented": false,
  "reference_matched": null
}
```

The PNG preview (`checkpoint-after-draw-1.png`) is the authentic EU4 loading
screen with the correct orientation and full-window coverage. A second,
independent launch (`simple-03/matrix-01`, library
`99c4a38680e366661b07147b6dc049912e793d58c16ee3e802d47d6e48dfcd25`) produced
the **same raw SHA-256** `8b8eed…`, so the live capture is deterministic across
launches.

## Reuse with a second checkpoint

The same `probe` implementation, without a checkpoint-specific code path,
captured an after-clear frame of the same engine target:

| Field | Value |
| --- | --- |
| Experiment | `.local/gfx-experiments/simple-03/clear-02` |
| Checkpoint | `after-clear` |
| Caller | `0xedfbef` |
| Target | 3456×2234 `BGRA8Unorm` |
| GPU status | completed |
| Raw SHA-256 | `4e2442de5e0aa9635533c68be5000e6d001b28b5412adb90b47d8db34246928b` |
| Pixels | all `(0,0,0,255)` — the authentic left clear |

The after-clear checkpoint records `draw_reached=false`, `draw_encoded=false`,
`gpu_completed=true`, `pixels_captured=true`. The levels are not collapsed.

## Iteration history and corrections

| Run | Finding | Correction |
| --- | --- | --- |
| 1 (sandbox) | `CFileException` before init | the workspace-write sandbox blocks the game's user-data writes; a one-shot host-file-access approval was used for the already-bounded probe |
| 2 | `ps` denied by the sandbox | replaced process inspection with libproc in `eu4_benchmark` and `launch_lease` |
| 3 | `_GfxClear` refused with `flags=7` | colour-only target accepts the colour bit and records ignored depth/stencil bits in `clear-semantics.json` |
| 4 | `_GfxDraw` reached, +0x70 shader null | live shader lifecycle diagnostics showed `_GfxAdd*Shader` rejecting the exact source hashes |
| 5 | shader admission still rejected | promoted the two exact live source SHA-256 values into the qualification manifest; no marker admission |
| 6 | matrix slot was null | live context showed constant slot 2 at master +0xd0, not +0xf0 |
| 7 | **live draw captured** | same raw hash across two launches |

## What is still open

- No original OpenGL reference image was captured for this checkpoint. The
  Metal output is not claimed as `reference_matched`. The independent CPU
  reference from the offline packet's geometry does not align exactly with the
  live capture; the live vertex/UV bytes were added to the checkpoint schema
  after the last authorized draw launch. The next draw probe will record
  `vertex_hex` and settle the mapping.
- `presented` is false because the checkpoint intentionally stops at the draw,
  before any later font/UI draw or present. A pre-present capture is valid
  evidence for this milestone.
- The target has no depth attachment. Depth/stencil clear bits and depth state
  are recorded but cannot affect the captured colour pixels.
- The sampler is the `simple.shader` asset contract, not a decoded engine
  sampler object.

## Independent assessment

**Real engine execution.** The capture came from PID 86305 running the pinned
`/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4` (SHA-256
`b3d388…141d`), not from a host. `stop.json` records `_GfxDraw` at
`0xedfcd1`, phase `after_init`, and the checkpoint caller record agrees.

**State fidelity.** The admitted shader identity, vertex slot/offset/stride,
the exact 64-byte constant matrix, the 2048×1536 engine texture, the logical
1728×1117 viewport against the 3456×2234 physical target, the geometry
generation, blend/raster snapshots and the GPU completion status are all
recorded from original engine state. The sampler is still the asset contract and
the target has no depth attachment; those limitations are explicit, not
papered over.

**GPU output and provenance.** The raw BGRA bytes are 30,882,816 and hash to
`8b8eed…e524`; a second independent launch produced the same hash. The PNG is
only a preview of those bytes. The image is visually the authentic EU4 loading
screen. No original OpenGL frame exists for this checkpoint, so the output is
not labelled `reference_matched`.

**Iterations and self-corrections.** Six live launches across three operator
budgets: a sandbox abort, `ps` denial, `_GfxClear` flags 7, live shader-source
admission, the `master+0xd0` matrix slot, and finally the captured draw. Every
failure produced a machine-readable diagnostic before the next step; the
successful launch required no manual function-by-function direction.

**Reuse.** The same `probe`, checkpoint selector and readback path produced the
after-clear frame. The offline host exercises after-clear, after-draw and
before-present through the same encoder. A later graphics function should add a
supported case to the semantic route and its encoder; the launch/checkpoint/
evidence path does not need to change.

**Remaining blockers.** (1) no authentic OpenGL reference image, so
`reference_matched` is open; (2) `presented` is false because the checkpoint
stops at the draw; (3) the successful checkpoint predates `vertex_hex`, so the
live UV/vertex bytes are not yet compared to the offline packet; (4) no depth
attachment and no decoded engine sampler object. These do not invalidate the
captured draw; they define the next bounded experiments.

**One diagnostic-order correction from the live run.** The first successful
checkpoint terminated inside the route before the capture layer could write
`draw-route.json`. The route now writes that record before the checkpoint can
stop the process; the checkpoint JSON was already sufficient for the status,
but the diagnostic is now complete for the next run.
