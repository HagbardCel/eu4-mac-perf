# M5 qualification report — second authentic draw and composed comparison

Status: **M5 complete by the fail-closed checker** (`campaigns/eu4-m5/accept.py`,
verdict `pass`, `m5_complete=true` on implementation commit `d5a9432`). The
checker separates `sprite_replay_qualified=true`, `live_engine_qualified=true`,
`live_composition_qualified=true`, and the explicitly **false** separate
`live_pixels_qualified` result caused by the unreconstructed loading-screen
background producer. The live engine now reaches and GPU-completes the selected
draw; the exact seeded replay remains the pixel qualification.

| Gate | Result |
| --- | --- |
| Authentic original-OpenGL reference | **pass** |
| Additional distinct rendering operation | **pass** (`_GfxDraw`, caller `0x14f6c1b`, sprite effect) |
| Matching selected event and inputs | **pass** (mode 5, count 4, 80-byte stride-20 VBO, 112-byte constants, 1235×175 texture) |
| Valid prior framebuffer contents | **pass** (diagnostic target seeded with the authentic pre-draw payload) |
| Live Metal GPU execution | **pass** (command status 4, shared-target readback) |
| Composed pixel equivalence (seeded replay) | **pass** (0 differing / 1,930,176) |
| Live composed pixel equivalence | **not qualified** — live prefix pre-draw differs; separate `live_pixels_qualified=false` |
| Nonzero contribution | **pass** (157,254 pixels changed) |
| Unsupported state refusal | **pass** |
| Corrupted-input negative control | **pass** (max channel error 243, 42,284 mismatched) |
| Preserved M4 route and regressions | **pass** (M4 checker verdict `pass`; 2473 tracked tests OK, 22 skipped in a clean worktree) |
| Live engine reaches the draw | **pass** (`metal-live-final-02`, caller `0x14f6c1b`) |
| Live engine GPU completion and backbuffer readback | **pass** (`command_status=4`, 3456×2234) |
| Live prefix composition | **pass** (Simple + sprite in order; contribution 157,254) |

## Selected operation

The next distinct `_GfxDraw` after the M4 Simple loading text is the
loading-screen sprite at caller RVA `0x14f6c1b` (`CSpriteType::RenderInternal +
2271`). It is a triangle strip of four vertices, stride 20 (`float3 position` +
`float2 texcoord`), with a 7×`vec4` constant block, a 1235×175 `GL_RGBA8`
texture, alpha blending (`SRC_ALPHA`/`ONE_MINUS_SRC_ALPHA`) and the backend
default raster snapshot. The original GLSL is the `sprite-map` effect:
`mul(Matrix, position)`, `texcoord.x += vXOffset`,
`texture(MapTexture, texcoord) * Color`.

The detailed selection and implications are in [target.md](target.md).

## Original-OpenGL reference

Captured with the existing passive observer, extended only to record the live
VBO range, the constant block, the attached shader sources, the bound texture
image and the effective sampler:

| Field | Value |
| --- | --- |
| Run | `.local/m5/experiments/sprite-gl-ref-09` |
| Selected event | `_GfxDraw`, caller `0x14f6c1b`, call_id 6, mode 5, count 4 |
| Post-draw | 1728×1117 BGRA8, `GL_BACK`, origin `lower_left`, SHA-256 `9cff01ca…` |
| Pre-draw | 1728×1117 BGRA8, SHA-256 `c25baa68…` |
| Vertex | 80 bytes, stride 20, SHA-256 `7f631749…` (live GL VBO) |
| Constants | 112 bytes (28 dwords; 7×`vec4`), SHA-256 from the capture |
| Texture | 1235×175, `GL_RGBA8`, SHA-256 `5f4940d7…` |
| Sampler | `GL_LINEAR` / `GL_LINEAR`, `GL_CLAMP_TO_EDGE` / `GL_CLAMP_TO_EDGE` (9729/33071) |
| Shader | GL program 22, GLSL digest `51868985…` |
| Compiler inputs | VS `e9422b2c…`, PS `10ca615e…` after the declared extension/define normalization |
| Pack state | restored; array-buffer binding restored |

The shader source dump is the authentic driver-visible source. Normalizing the
driver's `GL_ARB_shader_texture_lod` extension line to the compiler's
`NO_SHADER_TEXTURE_LOD` define reproduces the exact Metal boot source hashes,
which is what admission uses.

## Metal implementation and live reach

- A new `sprite-map/asset-maincode-v0` effect is admitted by exact source hash
  (VS `e9422b2c…`, PS `10ca615e…`) in
  [qualified-sources.json](../gfx-shader/v0/qualified-sources.json).
- `eu4_gfx_shader.mm` compiles the sprite MSL (matrix, `Color`, `vXOffset`,
  `MapTexture`), selects a stride-20 vertex descriptor, and keeps the Simple
  stride-24 layout as the default.
- `eu4_gfx_sprite_draw.mm` validates and encodes the operation. It fails closed
  on a non-sprite shader, wrong primitive/count, stride other than 20, a
  constant header declaring fewer than 24 dwords, texture/target mismatch,
  non-sprite blend/raster state, partial scissor, or invalid viewport.
- `eu4_gfx_draw_encode.mm` is the semantic dispatcher used by both the physical
  route and the diagnostic logical target. It never selects by caller RVA.
- `eu4_gfx_draw_route.mm` uses that dispatcher and preserves the Simple route.
- `eu4_gfx_logical_capture.mm` supports a persistent prefix target that clears at
  the frame boundary, resets at clear/present and generation changes, captures
  the live pre-draw separately, and stops at the configured event.

Three original entries were reconstructed to reach the draw:

1. **`_GfxBeginScene`** preserves the current-shader clear, applies the default
   blend state and returns 1; it skips only the GL GLEW attrib-disable branch.
2. **`_GfxClearTexture`** releases the backend texture for the header and
   preserves the engine-visible field mutations instead of calling
   `glDeleteTextures`.
3. **The sprite blend descriptor** is admitted by `apply_blend` and mapped to
   the measured alpha-blend snapshot; the Simple descriptor remains the default.

The final bounded probe (`metal-live-final-02`) reaches `_GfxDraw` at caller
`0x14f6c1b`, admits `sprite-map/asset-maincode-v0` through the real engine
shader path, encodes through the shared dispatcher/sprite encoder, and completes
on the GPU (`command_status=4`) against the engine-owned 3456×2234 backbuffer.
The checkpoint records the matching 80-byte stride-20 VBO, constant prefix,
1235×175 texture, linear/clamp sampler and alpha blend.

## Exact seeded comparison

Policy: [comparison-policy.json](comparison-policy.json). Exact equality
(`max_channel_error=0`, `mismatched_fraction=0`, `mean_abs_error=0`) with only
the declared lower-left → top-left row decode.

| Field | Value |
| --- | --- |
| Reference | `sprite-gl-ref-09` |
| Candidate | `metal-replay-06` (and fresh checker replays) |
| Pixels | 1,930,176 |
| Max channel error | **0** |
| Mean absolute error | **0.0** |
| Mismatched pixels | **0** |
| Alpha differences | 0 |
| Replay determinism | identical raw SHA-256 across independent host runs |

The replay is seeded with the exact original pre-draw and uses the captured
texture, constants, vertex bytes, viewport, linear/clamp sampler, blend and
shader semantics. No resampling or tolerance is used. The loading-screen
background is animated, so each reference is paired with a replay seeded by its
own pre-draw; independent reference runs are not expected to share a raw hash.

## Live two-draw prefix and contribution

The live prefix contains the frame's Simple draw followed by the sprite. The
checker recomputes the changed-pixel counts from the actual captured images
rather than trusting the manifest numbers:

| Side | Pre-draw | Post-draw | Recomputed changed pixels |
| --- | --- | --- | --- |
| Original GL | `gl-reference-pre.bgra` | `gl-reference.bgra` | **157,254** |
| Live Metal | `metal-prefix-pre.bgra` | `metal-logical.bgra` | **157,254** |

The reported and recomputed counts agree. The spatial change masks are also
identical over the 1,930,176-pixel logical image (`mask_iou=1.0`,
`mask_equal_pixels=1,930,176`). That is a strong independent check that the
sprite's coverage and blend contribution match even though the live background
differs.

## Missing full live-scene pixel equivalence

The live prefix pre-draw differs from the original-GL pre-draw because the
loading-screen background producer is not reconstructed. The live final is
therefore **not** claimed as pixel-equivalent to the cross-run GL capture;
`live_pixels_qualified=false` is reported separately and is not part of
`m5_complete`. The exact seeded replay is the isolated pixel qualification, and
the live contribution/mask comparison above is the live composition evidence.
The full Metal loading-screen framebuffer is not claimed to be complete or
pixel-equivalent.

## Negative controls

- **Unsupported state.** Forcing the Simple blend snapshot makes the sprite
  encoder refuse with `blend state is not the measured sprite alpha blend`.
- **Corrupted texture.** Inverting a texture band produces a meaningful
  comparison failure: status `fail`, max channel error 243, 42,284 mismatched
  pixels. The policy was not relaxed.
- **Missing live artifacts.** A missing descriptor under otherwise present live
  evidence produces a controlled failed live gate, not an exception.
- **Synthetic replay descriptor.** A replay descriptor cannot satisfy the live
  engine gate without the live checkpoint, caller identity, sprite identity and
  engine-owned backbuffer readback.

## Regressions

`tests.test_gfx_m5_sprite` covers the exact replay, determinism, sampler,
constant-word bounds, corrupted-input failure and unsupported-state refusal.
`tests.test_m5_accept` covers policy integrity, independent contribution
recomputation, missing artifacts and synthetic replay rejection. The checker
also runs the comparator, GL-reference, live-draw, Simple-draw and M4 tests.

Final results:

- Clean detached worktree at `c8d5ab5`: **2480 tracked tests OK, 26 skipped**
  (live-only tests skip without operator-local evidence).
- Checker offline regression subset: **67 tests pass**.
- M4 checker: **pass** on its recorded evidence.
- Final M5 checker on the final tree: **pass**, `m5_complete=true`, 23 gates
  (22 required pass + the separate live-pixels result), with
  `sprite_replay_qualified=true`, `live_engine_qualified=true`,
  `live_composition_qualified=true`, `live_pixels_qualified=false` (separate).

## Evidence and reproduction

Large raw artifacts remain under `.local/m5/experiments/` (gitignored). The
committed [evidence-manifest.json](evidence-manifest.json) pins every artifact's
SHA-256, the policy digest, the reference/candidate identities, the live
evidence digest and the remaining limitation. The campaign pin is in
[campaign.json](../../campaigns/eu4-m5/campaign.json).

```sh
# Original-GL reference (bounded authorized launch)
python3 -m automation.live_gfx capture --target _GfxDraw --until-target-draw \
  --caller-rva 0x14f6c1b --gl-reference --timeout 100 \
  --output .local/m5/experiments/sprite-gl-ref-09 \
  --campaign-run-root .local/m5/live

# Exact seeded offline GPU replay
python3 -m automation.m5_replay \
  --reference .local/m5/experiments/sprite-gl-ref-09 \
  --output .local/m5/experiments/metal-replay-06

# Independent comparison
python3 -m automation.gfx_compare compare \
  --reference .local/m5/experiments/sprite-gl-ref-09 \
  --candidate .local/m5/experiments/metal-replay-06 \
  --policy analysis/m5/comparison-policy.json \
  --out .local/m5/experiments/compare-final-m5-11

# Live engine probe (bounded authorized launch)
python3 -m automation.live_gfx probe --backend metal --checkpoint after-draw \
  --caller-rva 0x14f6c1b --logical-prefix --draw-sequence --timeout 100 \
  --output .local/m5/experiments/metal-live-final-02 \
  --campaign-run-root .local/m5/live

# Acceptance
python3 campaigns/eu4-m5/accept.py .
```

## Known limitations

- The live prefix cannot yet reproduce the loading-screen background producer.
  The live sprite is reached, GPU-completed and independently contribution-
  checked, but full live-scene pixel equivalence is not claimed.
- The engine sampler object is not decoded; the captured original-GL sampler is
  `GL_LINEAR`/`GL_CLAMP_TO_EDGE` and the implemented Metal sampler matches it.
- Only the Simple and sprite operations are reconstructed. Title/map/font draws
  remain outside the admitted route.
