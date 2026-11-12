# M5 qualification report — second authentic draw and composed comparison

Status: **qualified by the M5 checker** (`campaigns/eu4-m5/accept.py`, 18/18 gates, verdict `pass` on implementation commit `36fa8e9` and again on the
final clean tree `d08eecd`) for the reconstructed sprite operation. The live engine path to the draw is **not**
reached; the exact blocker is recorded below. The checker distinguishes the
reconstructed operation and the live-Metal replay from a live-engine route.

| Gate | Result |
| --- | --- |
| Authentic original-OpenGL reference | **pass** |
| Additional distinct rendering operation | **pass** (`_GfxDraw`, caller `0x14f6c1b`, sprite effect) |
| Matching selected event and inputs | **pass** (mode 5, count 4, 80-byte stride-20 VBO, 112-byte constants, 1235×175 texture) |
| Valid prior framebuffer contents | **pass** (diagnostic target seeded with the authentic pre-draw payload) |
| Live Metal GPU execution | **pass** (command status 4, shared-target readback) |
| Composed pixel equivalence | **pass** (0 differing / 1,930,176) |
| Nonzero contribution | **pass** (157,254 pixels changed) |
| Unsupported state refusal | **pass** |
| Corrupted-input negative control | **pass** (max channel error 243, 42,284 mismatched) |
| Preserved M4 route and regressions | **pass** (M4 checker verdict `pass`, 67 offline tests) |
| Live engine reaches the draw | **not claimed** — `_GfxBeginScene` blocker |

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
VBO range, the constant block, the attached shader sources and the bound
texture image:

| Field | Value |
| --- | --- |
| Run | `.local/m5/experiments/sprite-gl-ref-08` |
| Selected event | `_GfxDraw`, caller `0x14f6c1b`, call_id 6, mode 5, count 4 |
| Post-draw | 1728×1117 BGRA8, `GL_BACK`, origin `lower_left`, SHA-256 `23ab7b82…` |
| Pre-draw | 1728×1117 BGRA8, SHA-256 `31f890d4…` |
| Vertex | 80 bytes, stride 20, SHA-256 `7f631749…` (live GL VBO) |
| Constants | 112 bytes, 7×`vec4`, SHA-256 `277f0413…` |
| Texture | 1235×175, `GL_RGBA8`, SHA-256 `5f4940d7…` |
| Shader | GL program 22, GLSL digest `51868985…` |
| Compiler inputs | VS `e9422b2c…`, PS `10ca615e…` after the declared extension/define normalization |
| Pack state | restored; array-buffer binding restored |

The shader source dump is the authentic driver-visible source. Normalizing the
driver's `GL_ARB_shader_texture_lod` extension line to the compiler's
`NO_SHADER_TEXTURE_LOD` define reproduces the exact Metal boot source hashes,
which is what admission uses.

## Metal implementation

- A new `sprite-map/asset-maincode-v0` effect is admitted by exact source hash
  (VS `e9422b2c…`, PS `10ca615e…`) in
  [qualified-sources.json](../gfx-shader/v0/qualified-sources.json).
- `eu4_gfx_shader.mm` compiles the sprite MSL (matrix, `Color`, `vXOffset`,
  `MapTexture`), selects a stride-20 vertex descriptor, and keeps the Simple
  stride-24 layout as the default.
- `eu4_gfx_sprite_draw.mm` validates and encodes the operation. It fails closed
  on a non-sprite shader, wrong primitive/count, stride other than 20, absent
  constants, texture/target mismatch, non-sprite blend/raster state, partial
  scissor, or invalid viewport.
- `eu4_gfx_draw_encode.mm` is the semantic dispatcher used by both the physical
  route and the diagnostic logical target. It never selects by caller RVA.
- `eu4_gfx_draw_route.mm` now uses that dispatcher and preserves the Simple
  route.
- `eu4_gfx_logical_capture.mm` now supports a persistent prefix target seeded
  with the authentic pre-draw contents, so the selected draw is encoded over
  valid destination contents rather than a blank target.

The production encoder was exercised by a live-Metal replay host
(`tests/gfx_m5_replay_host.mm`) using the captured vertex, constant and texture
bytes over the authentic pre-draw. The host calls the same dispatcher and the
same sprite encoder; it is not a second renderer. The live engine path does not
yet reach the draw because the second loading frame begins with the
unreconstructed original `_GfxBeginScene`; the first present is followed by a
`SIGSEGV` at `CGraphics::BeginScene + 39` (return from `callq _GfxBeginScene`).
The smallest next action is to reconstruct `_GfxBeginScene` and the immediately
following `CGraphics::ClearScene` frame-start path.

## Comparison

Policy: [comparison-policy.json](comparison-policy.json). Exact equality
(`max_channel_error=0`, `mismatched_fraction=0`, `mean_abs_error=0`) with only
the declared lower-left → top-left row decode.

| Field | Value |
| --- | --- |
| Reference | `sprite-gl-ref-08` |
| Candidate | `metal-replay-02` (and fresh checker replays) |
| Pixels | 1,930,176 |
| Max channel error | **0** |
| Mean absolute error | **0.0** |
| Mismatched pixels | **0** |
| Alpha differences | 0 |
| Contribution | 157,254 pixels differ between pre- and post-draw |
| Replay determinism | identical raw SHA-256 across independent host runs |

Exact equality is justified because the replay uses the captured texture,
constants, vertex bytes, viewport, blend and shader semantics, and is seeded
with the exact original pre-draw payload. No resampling or tolerance is used.
The loading-screen background is animated, so each reference is paired with a
replay seeded by its own pre-draw; independent reference runs are not expected
to share a raw hash.

## Negative controls

- **Unsupported state.** Forcing the Simple blend snapshot makes the sprite
  encoder refuse with `blend state is not the measured sprite alpha blend`.
- **Corrupted texture.** Inverting a texture band produces a meaningful
  comparison failure: status `fail`, max channel error 243, 42,284 mismatched
  pixels. The policy was not relaxed.

## Regressions

`tests.test_gfx_m5_sprite` covers exact replay, determinism, corrupted-input
failure and unsupported-state refusal (4 tests, pass). The checker also runs the
comparator, GL-reference, live-draw, Simple-draw and M4 checker tests.

The final checker passed all 18 gates on the clean implementation commit
(`36fa8e9`), including the M4 checker and 67 offline regression tests.

The tracked full suite was run on Python 3.14.10:
`PYTHONPATH=benchmark:. python -m unittest discover -s tests` ran **2468
tests: 2456 passed, 10 skipped, 2 errors**. Both errors are from local-only
untracked artifacts that are absent in CI: the untracked
`tests/test_gfx_l32b_e2e.py` imports the absent `automation.gfx_l32b_hooks`
module, and the partially present untracked
`results/20261006T193739Z-submission-experiment` lacks `manifest.json`. Every
tracked test passed; these are the same two local exclusions documented for M4.
The M4 checker and its host tests pass on their recorded evidence.

## Evidence and reproduction

Large raw artifacts remain under `.local/m5/experiments/` (gitignored). The
committed [evidence-manifest.json](evidence-manifest.json) pins every artifact's
SHA-256, the policy digest, the reference/candidate identities and the blocker.
The campaign pin is in [campaign.json](../../campaigns/eu4-m5/campaign.json).

```sh
# Original-GL capture (requires an authorized bounded launch)
python3 -m automation.live_gfx capture --target _GfxDraw --until-target-draw \
  --caller-rva 0x14f6c1b --gl-reference --timeout 70 \
  --output .local/m5/experiments/sprite-gl-ref-08 \
  --campaign-run-root .local/m5/impl

# Live-Metal replay of the captured operation
python3 -m automation.m5_replay \
  --reference .local/m5/experiments/sprite-gl-ref-08 \
  --output .local/m5/experiments/metal-replay-02

# Differential comparison
python3 -m automation.gfx_compare compare \
  --reference .local/m5/experiments/sprite-gl-ref-08 \
  --candidate .local/m5/experiments/metal-replay-02 \
  --policy analysis/m5/comparison-policy.json \
  --out .local/m5/experiments/compare-final-m5-01

# Acceptance
python3 campaigns/eu4-m5/accept.py .
```

## Known limitations

- The live engine does not reach the sprite draw; `_GfxBeginScene` is the exact
  next blocker. The live-Metal replay is a validation boundary, not a
  production frame loop.
- The engine sampler object is not decoded; the replay uses linear
  clamp-to-edge, which reproduces the captured GL image exactly.
- The diagnostic target is seeded with the authentic pre-draw because the
  background producer is not reconstructed. It encodes the selected draw over
  that content through the same encoder, which is the minimum valid composed
  comparison; it does not claim to reconstruct the animated background.
- Only the Simple and sprite operations are reconstructed. Title/map/font draws
  remain outside the admitted route.
