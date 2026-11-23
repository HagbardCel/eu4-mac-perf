# Event-1 uv2 diagnostic probe (M7)

Date: 23 November 2026 (Europe/Berlin). This is a narrow diagnostic addition to
the existing capture and replay paths. It does **not** reconstruct an OpenGL
renderer, change the trusted checker, alter the sealed reference or make a new
Metal attribution claim.

## What was added

One opt-in observation for the authenticated event-1 draw (event 1, program
310, framebuffer 6, output `tex:433`): the interpolated `uv2.xy` values from a
diagnostic execution of that draw in original EU4's OpenGL process, together
with directly comparable values from the existing Metal replay.

### OpenGL side

`benchmark/eu4_gfx_frame_trace.c` gains a disabled-by-default probe:

| Step | Behaviour |
| --- | --- |
| Entry hook (`uv2_probe_prepare`) | Selects exactly the configured event and captures the engine target's pre-draw BGRA8 colour and f32 depth. The engine's own draw follows unchanged. |
| Exit hook (`uv2_probe_finish`) | Uses the applied draw state (the deferred uniform/attribute uploads inside `_GfxDrawIndexed` have run) and executes **two** diagnostic draws into isolated targets, before any engine state is changed further. |
| Control draw | Recompiles the live original vertex **and** fragment sources into a new program (original attribute locations bound), seeds an RGB8 target with the captured pre-draw colour and depth, and reproduces the draw. This tests whether the diagnostic execution reproduces the authentic event output. |
| Probe draw | Recompiles the same live vertex source and links it with a substitute fragment shader that writes `vec4(uv2, 0.0, 1.0)`, draws into an RGBA32F target seeded with the captured pre-draw depth. |
| Readback | Colour control: BGRA8, RGB scope, lower-left. Probe: `glGetTexImage(GL_RGBA, GL_FLOAT)`, lower-left, row-major, `row_stride = width*16`. |
| Restore | Program, framebuffers, read buffer, viewport, scissor, cull/front/polygon, blend, depth, stencil-enable, colour mask, alpha-test, logic-op, clear colour, active unit, texture binding, array/element buffers, every live attribute pointer/enable and pack state. |

Refused explicitly (never normalized): a non-RGB8 control target, a
multisampled draw target, an enabled stencil test, an enabled alpha test,
non-zero `base_vertex`, instancing, a vertex stage that does not declare the
configured varying, or a state readback that cannot be reproduced. The engine's
pending GL error queue is never consumed.

The probe never writes the engine render target and never feeds the sealed
reference. `benchmark/eu4_gfx_frame_trace.h` exposes the
`eu4_gfx_uv2_probe_*` API and the host-test entry points.

### Metal side

`benchmark/eu4_gfx_frame_replay.mm` gains the equivalent disabled-by-default
pass. For the selected event only, immediately after the normal draw encoder is
closed, a second render pass is encoded in the same command buffer:

* **the same compiled vertex `MTLFunction`** as the normal pass (recorded MSL
  digest and function identity in the descriptor), the same vertex buffers,
  vertex uniform bytes, viewport, scissor, culling and index buffer;
* a fragment function built from the translation report's
  `varying_locations["v_TEXCOORD1"]` that writes `float4(v_TEXCOORD1, 0, 1)`;
* an isolated `RGBA32Float` target, cleared to zero, with no depth attachment
  (the probe does not need occlusion);
* a lower-left RGBA32F readback after the command buffer completes.

The normal render pass, the translated shaders and the normal frame readback are
unchanged. The focused test verifies the normal `metal-frame.bgra` is
bit-identical with and without the probe.

### Output contract (`gfx-uv2-probe/v1`)

One directory per side:

| File | Content |
| --- | --- |
| `uv2-probe.json` | Side, event, draw/target identity, varying and location, source/uniform/attribute/indices digests, explicit layout, status and every failure |
| `uv2-probe.rgba32f` | `width*height*16` bytes: little-endian float32 components `uv2.x, uv2.y, 0.0, 1.0`, row 0 at the lower-left |
| `uv2-control.bgra` | OpenGL only: the original-shader control, BGRA8, RGB scope, lower-left |

### Comparison

`automation/gfx_uv2_probe.py compare` consumes both descriptors, the captured
frame and the existing divergent-pixel mask. It reports, for every mask pixel
and a small neighbourhood, the raw float32 bits of each component, the numeric
values, signed differences, ULP differences, pixel coordinates and orientation,
and per-component bit equality. There is no aggregate similarity score.

It also reports:

* **input identity** against the captured event: program, framebuffer, colour
  texture, draw parameters, vertex/fragment source hashes, every uniform value
  hash, every live vertex-range hash and the index hash;
* **original-shader control correspondence** against the capture's own
  post-event colour observation: exact RGB mismatch count, maximum channel
  error and the first differences.

A difference is attributed to original-OpenGL numerical behaviour only when
both checks pass.

```sh
python3.14 -m automation.gfx_uv2_probe compare \
    --gl <capture>/uv2-probe \
    --metal <capture>/metal/uv2-probe \
    --frame <capture>/frame \
    --mask-gl <existing seal>/frame/blobs/prefix-color-1af2bbb3e6123aa2f1fb7e9c07dbc24ae6ea9013c61737fd15cfe13cc4886d6f.bin \
    --mask-metal <existing seal>/metal/prefix-event1-433.bgra \
    --neighborhood 1 \
    --out <capture>/uv2-probe
```

## Fidelity controls and limits

1. **Instrumented draw identity.** The OpenGL descriptor records the live
   program, framebuffer, colour texture, extent, draw parameters, the exact
   vertex/fragment source SHA-256, each uniform's exact bytes and each consumed
   vertex range's SHA-256. The offline comparator requires all of them to equal
   the captured event's records.
2. **Ordinary rendering unaffected.** The probe restores all changed GL state
   and writes only its own targets. The native host test compares a full state
   snapshot before/after. On Metal, the normal frame output is bit-identical
   with and without the probe.
3. **Original-shader control.** The control draw recompiles the original
   vertex+fragment pair and renders it through the same diagnostic path. The
   comparator reports exact RGB equality against the capture's own post-event
   observation and records any failure.
4. **Recorded limitation.** The OpenGL probe's vertex stage is recompiled into
   a new program; the driver may optimize it differently when it is linked with
   the substitute fragment shader. The control validates the diagnostic
   pipeline (state, inputs, targets, readback), not the probe program's
   instructions. This is stated in every report and is the reason the values
   are **diagnostic evidence**, not a replacement for the sealed reference.
5. The Metal probe reuses the normal pass's compiled vertex function, so the
   Metal interpolator input is the same function object as the normal pass.

## Operator commands

The capture is an operator action and uses the existing one-use launch
authorization. The agent must not run it. The currently active host-wide
campaign marker (`.local/m7/event3/state-operator-60m-04.json`) has expired;
the operator must deactivate it before this campaign starts (do not remove it
from an agent session).

Fish shell; adjust the output root to a fresh directory.

```fish
set -l REPO /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf
set -l PY /opt/homebrew/opt/python@3.14/bin/python3.14
set -l STATE $REPO/.local/m7/event1-uv2/state-operator-uv2.json
set -l OUT $REPO/.local/m7/event1-uv2/capture
set -l TOKEN (cat ~/.local/share/eu4-dsh/operator-token)
cd $REPO

# 0. Only if the previous campaign is finished: remove its host-wide marker.
$PY -m automation.campaign_guard deactivate \
    --state $REPO/.local/m7/event3/state-operator-60m-04.json

# 1. A live-capture campaign with an operator deadline.
env EU4_CAMPAIGN_ADMIN_TOKEN=$TOKEN $PY -m automation.campaign_guard ensure \
    --state $STATE --mode controlled_capture --deadline-seconds 3600

# 2. One authorization for exactly one launch.
env EU4_CAMPAIGN_ADMIN_TOKEN=$TOKEN $PY -m automation.campaign_guard authorize-launch \
    --state $STATE
# Copy the printed authorization id.

# 3. One authorized original-EU4 diagnostic capture.
env EU4_CAMPAIGN_STATE=$STATE EU4_CAMPAIGN_AUTHORIZATION=<authorization-id> \
    $PY -m automation.m6_frame capture \
      --output $OUT \
      --prefix-end 1 --prefix-max-bytes 384000000 --prefix-max-ns 10000000000 \
      --uv2-probe --uv2-probe-event 1 \
      --uv2-probe-program 310 --uv2-probe-framebuffer 6 \
      --uv2-probe-texture 433 \
      --seal --background --duration 90 --timeout 180

# 4. Corresponding Metal diagnostic (offline; no EU4 launch).
$PY -m automation.m6_frame translate --frame $OUT/frame --out $OUT/translated
$PY -m automation.m6_frame replay \
    --frame $OUT/frame --shaders $OUT/translated --out $OUT/metal \
    --uv2-probe --uv2-probe-event 1 \
    --uv2-probe-program 310 --uv2-probe-framebuffer 6 \
    --uv2-probe-texture 433 \
    --rationale 'event-1 uv2 probe: original-GL/Metal diagnostic comparison'

# 5. Numerical comparison at the existing event-1 divergent pixels and neighbours.
$PY -m automation.gfx_uv2_probe compare \
    --gl $OUT/uv2-probe --metal $OUT/metal/uv2-probe --frame $OUT/frame \
    --mask-gl $REPO/.local/m7/event20/trusted-copy/47590918/frame/blobs/prefix-color-1af2bbb3e6123aa2f1fb7e9c07dbc24ae6ea9013c61737fd15cfe13cc4886d6f.bin \
    --mask-metal $REPO/.local/m7/event20/trusted-run/47590918/metal/prefix-event1-433.bgra \
    --neighborhood 1 --out $OUT/uv2-probe

# 6. End the campaign.
$PY -m automation.campaign_guard deactivate --state $STATE
```

### Resulting paths

| Observation | Path |
| --- | --- |
| Original-GL uv2 values | `$OUT/uv2-probe/uv2-probe.rgba32f` |
| Original-GL control | `$OUT/uv2-probe/uv2-control.bgra` |
| Original-GL descriptor | `$OUT/uv2-probe/uv2-probe.json` |
| Metal uv2 values | `$OUT/metal/uv2-probe/uv2-probe.rgba32f` |
| Metal descriptor | `$OUT/metal/uv2-probe/uv2-probe.json` |
| Per-pixel comparison (bits, values, signed/ULP differences, control, identity) | `$OUT/uv2-probe/comparison.json` |
| Sealed reference for this capture (unchanged mechanism) | `$OUT/frame/frame-seal.json` |
| Authentic event-1 control reference | `$OUT/frame/prefix-observations.jsonl` |

## Focused verification

`tests/test_gfx_uv2_probe.py` (21 tests, offline):

* descriptor layout, row stride, payload digest and failed-status refusal;
* RGB-scope divergent-pixel selection;
* raw bits, numeric values, signed differences, ULP differences and bit
  equality; mask neighbourhood bounds;
* original-shader control equality and one-LSB mismatch recording;
* input-identity checks against a captured event (program, texture, uniforms,
  attributes, failed digest);
* capture/replay option wiring and the prefix-end requirement;
* native OpenGL host (`tests/gfx_uv2_probe_host.c`) with a real legacy CGL
  context: RGBA32F layout and lower-left readback, original-shader control,
  full state restoration and non-selected-event refusal;
* the Metal replay end-to-end on the canonical `menu-occ300` fixture: probe
  layout and event identity, and a bit-identical normal frame output with and
  without the probe.

Not claimed: an original-GL numerical observation (requires the authorized
capture above), a cause for the event-1 RGB difference, renderer equivalence,
or a change to the trusted verdict semantics.
