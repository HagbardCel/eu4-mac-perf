# Live graphics experiments

This is the reusable path for authentic EU4 graphics reconstruction. It extends
the existing `automation.live_gfx` command, the `EU4GfxBackend`, the graphics
entry shim and the Metal test hosts. It is not a second capture system.

## 1. Host-wide launch coordination

Two independent controls exist and must not be confused:

| Control | Owner | Purpose |
| --- | --- | --- |
| `automation.launch_ledger` | workflow | How many launches are authorized, time/output limits, receipts, reconciliation |
| `automation.launch_lease` | host | Mutual exclusion for the canonical installed executable |

The lease is one `flock(2)` on
`/tmp/eu4-mac-perf-launch-lease-<uid>/eu4-<name>-<key>.lease`, keyed by the
resolved executable path plus device and inode. The selected executable is the
same from every worktree, output directory and agent. The descriptor is passed
to the game with `pass_fds`, so a launcher crash does not release the lease
while the game is alive; the kernel releases it when the last descriptor closes.
`ps` is not used: process inspection is read-only libproc, which also works in
sandboxed DSH shells.

Every existing Python path that starts the installed EU4 binary acquires this
lease immediately before `Popen` and releases the parent descriptor after the
game is terminated and reaped. A manually started game is refused before the
lease is granted.

## 2. Bounded operator authorization

The operator grants a budget once; the agent consumes it through the same
ledger the campaign runner already uses.

```sh
python3 -m automation.live_gfx authorize \
  --run-root .local/gfx-experiments/simple-01 \
  --output-root .local/gfx-experiments \
  --limit 3 \
  --deadline-seconds 180 \
  --workflow-id simple-01
```

`authorize` pins the executable SHA-256 and the experiment output root, creates
the ledger if absent and writes a pointer in the run root. It never replenishes
an existing budget. `--deadline-seconds`, when supplied, must be finite and
positive. By default the ledger lives under
`.local/launch-authority/`; the preferred location outside the worktree is not
writable under a workspace-write DSH sandbox. This is not protection against
deliberately malicious same-user code, consistent with the project's trust
model.

## 3. The probe

```sh
python3 -m automation.live_gfx probe \
  --backend metal \
  --checkpoint after-draw \
  --caller-rva 0xedfcd1 \
  --occurrence 1 \
  --output .local/gfx-experiments/simple-01/after-draw-01 \
  --campaign-run-root .local/gfx-experiments/simple-01
```

The command claims one ledger launch, builds `libeu4_metal_boot.dylib`, starts
the pinned x86_64 game with the existing Metal-init boot path, and stops at the
selected checkpoint. It requires `--campaign-run-root`; the unrestricted
`--authorize-live` switch is not a substitute for an operator budget.

Supported checkpoints use one implementation:

- `after-clear`: immediately after a submitted `_GfxClear`.
- `after-draw`: immediately after a routed `_GfxDraw`.
- `before-present`: after the pending draw completes and before the drawable is
  acquired.

Each successful checkpoint writes:

- `checkpoint-<op>-<n>.bgra`: tightly packed BGRA8, with width, height and row
  stride in the JSON (row stride is `width * 4`).
- `checkpoint-<op>-<n>.json`: target/generation, caller RVA, PID/session,
  executable and library SHA-256, GPU command status, shader identity, vertex
  stride/offset/bytes, constant-buffer bytes, texture size, logical and physical
  viewport, raw-file SHA-256 and the explicit status flags.
- `checkpoint-<op>-<n>.png` from the probe's small standard-library preview
  writer.

The probe also aggregates `probe-result.json`; `inspect --run <dir>` prints the
result or reconstructs it from the artifact files. `--reference` accepts an
operator PNG for numerical comparison only and never sets `reference_matched`.

## 4. Timeout contract

Every live timeout and every configured positive deadline must satisfy
`math.isfinite(value) and value > 0`; booleans and non-numeric values are
rejected at the ledger boundary. `probe --timeout` and
`authorize --deadline-seconds` validate before any claim, build or launch.
`launch_ledger.claim()` validates direct Python callers and persisted ledgers,
and `eu4_metal_boot.launch_session()` rejects an invalid `wait_seconds` before
spawning. A pinned Metal probe requires a positive deadline; legacy workflows
may still use `deadline_seconds=None`.

## 5. Status levels

These are separate facts, never collapsed into `pass`:

| Field | Meaning |
| --- | --- |
| `draw_reached` | the original engine invoked the intended entry |
| `draw_encoded` | supported original state produced Metal commands |
| `gpu_completed` | the command buffer finished successfully |
| `pixels_captured` | the engine-owned target was read back |
| `presented` | `_GfxPresent` reached the Metal presentation path |
| `reference_matched` | authenticated original OpenGL reference with equivalent checkpoint semantics |

`reference_matched` remains `null` unless such an authenticated original
reference exists. An operator-provided PNG is compared numerically and reported
separately in the `reference` object (`comparable`, `pixels_identical`,
`mismatched_pixels`, `maximum_channel_error`, `difference_image`,
`authenticated_opengl=false`). Pixel equality is computed over all four RGBA channels; alpha-only differences
are visible in the difference image and are not reported as identical. Pixel
equality is not OpenGL fidelity. A
pre-present image is valid evidence even when later, unrelated font/UI drawing
is not reconstructed.

## 6. Draw dispatch and submission

`_GfxDraw` still enters through `benchmark/eu4_gfx_draw_entry.S` and
`eu4_gfx_draw_capture.c`. The capture records the invocation, then a small
semantic route in `benchmark/eu4_gfx_draw_route.mm` decides:

- `_GfxDraw` plus the admitted `simple-lua-Simple/asset-maincode-v0` shader and
  valid vertex/constant/texture/target state is encoded by the shared Simple
  encoder.
- `_GfxDrawIndexed` / `_GfxDrawIndexedInstanced`, other shaders and missing
  resources stop with a precise `draw-unsupported.json` or `draw-failed.json`.
  They are never silently skipped.
- A proven empty strip is recorded as a no-op.

The caller RVA selects diagnostics/checkpoints only; it is not a renderer
input. The live path creates one command buffer on `backend.queue`, encodes the
same `eu4_gfx_simple_draw_encode_into` used by the offline host, commits without
waiting, and stores it in `backend.pendingGameCommand`. Queue order gives
clear → draw(s) → present. `_GfxPresent` drains that last command and reports
failure before declaring success. The backend records `gameClearGeneration`, so
a draw cannot run before the clear for its geometry generation.

The exact source SHA-256 values for the live Simple VS/PS are in
`analysis/gfx-shader/v0/qualified-sources.json`; the boot library loads them
only through `EU4_GFX_QUALIFIED_SOURCES`. Transitional marker admission remains
disabled on the engine path.

## 7. Known limitations

- The game backbuffer is colour-only. `_GfxClear` flags with depth/stencil bits
  are accepted for the colour clear and the ignored bits are recorded in
  `clear-semantics.json`. No depth attachment exists yet.
- The reconstructed pass does not cull. The admitted default raster snapshot is
  validated; a non-default snapshot is refused.
- The live sampler is the `simple.shader` asset contract; the engine sampler
  object is not decoded yet.
- No original OpenGL frame was captured for this checkpoint. The Metal output is
  therefore not claimed to be `reference_matched`. An independently calculated
  CPU reference using the live vertex UVs and matrix matched the captured frame
  on 23,808 samples (0 samples over 4/255, maximum channel error 0.0031). That is
  CPU fidelity evidence, not OpenGL equivalence.
- A workspace-write DSH sandbox cannot run the game: the game writes its own
  user-data files outside the workspace. The smoke runs used a one-shot
  host-file-access approval for the already-bounded probe command. `ps` is also
  denied there, which is why process inspection uses libproc.
