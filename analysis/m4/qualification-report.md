# M4 qualification report — original-GL reference and Metal presentation

Status: **not qualified.** The original-OpenGL reference and the corresponding
reconstructed Metal output are pixel-identical under the fixed policy, and the
Metal drawable is presented, but independent window-server and ScreenCaptureKit
captures of the EU4 window return a uniform frame. M4 requires that the
reconstructed loading frame be independently observed in the actual window, so
the acceptance checker returns `inconclusive`, not `pass`.

| Gate | Result |
| --- | --- |
| Authentic original-OpenGL reference | **pass** |
| Corresponding reconstructed Metal output | **pass** |
| Fixed-policy differential comparison | **pass** (exact, 0 differing pixels) |
| GPU-completed live Metal draw | **pass** |
| Drawable scheduled and presented | **pass** |
| Independently observable in the game window | **not demonstrated** |
| Offline regressions | **pass** |
| DSH autonomous implementation/remediation | **pass** (this session) |

## Baseline and identities

| Field | Value |
| --- | --- |
| Baseline commit | `dd4290bc9165574d81afa1b3ce151cb767031fda` (M3 merge, PR #185) |
| Implementation commit | `3de8d4a` on `m4/gl-reference-and-presentation` |
| Pinned executable | `/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4` |
| Executable SHA-256 | `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` |
| GPU | Apple M5 Max, Metal 4 |
| Selected event | `_GfxDraw` at caller RVA `0xedfcd1` (`CEU3Graphics::ShowBasicLoadText`) |
| Draw | `GL_TRIANGLE_STRIP` (mode 5), 4 vertices, first 0, Simple effect |
| Extent | 1728×1117 logical; physical drawable 3456×2234 (2×) |

## Original-OpenGL reference

`python3 -m automation.live_gfx capture --target _GfxDraw --target
_GfxCreateVertexBuffer --target _GfxSetVertexBuffers --target
_GfxUpdateVertexBuffer --until-target-draw --caller-rva 0xedfcd1
--gl-reference`

| Field | Value |
| --- | --- |
| Run | `.local/m4/experiments/gl-reference-04` |
| Session / PID | see `gl-reference.json` (`provenance`) |
| Caller / call id | `0xedfcd1` / 9 |
| Capture | `gl-reference.bgra`, 7,720,704 bytes, 1728×1117 BGRA8 |
| Raw SHA-256 | `69d41a8870679dcb3158bd3f3fad2f4afff11106e45990ab9a3ffc0f5dcc7670` |
| Origin | `lower_left` (glReadPixels; declared, not transformed) |
| GPU boundary | `glFinish` 1.3 ms, `glReadPixels` 0.5 ms, `gl_error=0` |
| Read buffer / viewport | `GL_BACK` / `[0,0,1728,1117]` |
| Caps bytes | `0040000001010101` (byte 4 = 1) |
| GLSL digest | `1e5d1d7b0910ec343c5d5da1ec811fa6f2f963157aff251d79bd588391905e7f` |
| Live vertex payload | 96 bytes, SHA-256 `f13dea22399b9649d84a09965ec6b21bc13b72db3c17049916a675922a39c8ab`, V 0..1 |
| Pre-draw target | all black, alpha 255 (authentic clear) |
| Determinism | independent launch `gl-reference-02` produced the same raw SHA-256 |

The observer only arms with `EU4_GFX_OPS_FRAMEBUFFER`; the unarmed path performs
no GL calls. A missing, truncated or corrupted payload is rejected by
`_gl_reference_block` and by the comparator.

## Corresponding reconstructed Metal output

`python3 -m automation.live_gfx probe --checkpoint after-present --caller-rva
0xedfcd1 --hold-ms 20000`

| Field | Value |
| --- | --- |
| Run | `.local/m4/experiments/metal-present-18` |
| Logical capture | `metal-logical.bgra`, 1728×1117 BGRA8, origin `top_left` |
| Logical raw SHA-256 | `c668b054e87ea92eea7de6161adc7eeeeef07ea19bfcbde0ef02f069ba77fe14` |
| Live vertex | same 96 bytes (`f13dea22…`), effective count 4, shader `simple-lua-Simple/asset-maincode-v0` |
| GPU | `MTLCommandBufferStatusCompleted` (4) for the logical encode and staging blit |
| Presented drawable | 3456×2234 BGRA8, raw SHA-256 `6921c242c66ad9929c7fd935178ae01bf2d4da0b25b6b57980660de3cf7999c3` |
| Presentation call | `presentDrawable` completed; drawable readback captured after completion |
| Window state | `window=21710`, visible, not occluded, layer-backed SDL view, 2× backing |

The presented drawable is visibly the loading image. Reducing it with a 2×2 box
filter to the GL logical extent gives a mean absolute error of 0.42/255 (max 46
at texture edges), consistent with 2× rasterisation. That is supporting
evidence only: the authoritative equivalence test is the 1:1 logical
comparison above, which is exact.

The logical capture is the same Metal encoder used by the live route, rendered
at the engine's logical extent so it is directly comparable with the original
GL surface. The physical drawable remains the presentation target.

## Comparison policy and result

Policy: [comparison-policy.json](comparison-policy.json) (pinned by digest in
the evidence manifest). Identities are pinned for the executable, the reference
GLSL digest, the candidate shader identity, the reference caps byte and a
required live vertex payload. The comparison is exact:

| Field | Value |
| --- | --- |
| Report | `.local/m4/experiments/compare-final/report.json` |
| Pixels | 1,930,176 |
| Max channel error | **0** |
| Mean absolute error | **0.0** |
| Mismatched pixels (>0) | **0** |
| Alpha differences | 0 |
| Normalization | `vertical_flip` (declared lower-left → top-left decode only) |
| Reference/candidate previews | identical SHA-256 (`6d87172e…`); difference image is black |

Thresholds were fixed at exact equality before the candidate was qualified.
Because the draw is an opaque, full-viewport quad over a cleared BGRA8 target
with the same linear sampler and no MSAA/dither, integer equality is justified;
the observed result is exactly zero across independent launches.

## Presentation evidence, and the missing gate

`metal-present-18` records all of the following:

- the Metal draw executed and its command buffer completed;
- the engine's own `_GfxPresent` path drained the draw, acquired a drawable,
  blitted and called `presentDrawable`;
- the destination drawable was read back with the loading image
  (`6921c242…`) after `waitUntilCompleted`;
- the game window (`21710`) was visible, not occluded, layer-backed, 1728×1117
  points at 2× backing.

Independent capture of that window did **not** produce the image. Every attempt
returned a uniform frame:

| Capture path | Result |
| --- | --- |
| `screencapture -l 21710` | 3456×2234, uniform (1 color bucket) |
| `screencapture -l` other game windows | uniform |
| full-screen `screencapture -x` | uniform |
| ScreenCaptureKit `SCScreenshotManager` | uniform |

The same capture tools were verified offline against a minimal Cocoa window
backed by a `CAMetalLayer` (solid red); both `screencapture` and
ScreenCaptureKit returned the red frame. The failure is therefore specific to
the EU4 window/layer composition, not the capture tooling.

This is the exact reason the acceptance checker reports `inconclusive`. A
completed `presentDrawable`, a drawable readback and a visible window object are
not independent proof that the intended image became visible.

### Known limitation and next experiment

The Metal layer is attached to the current SDL window's content view on the
main thread and is layer-backed, on top, opaque, and reports a 3456×2234
drawable. The window server still composites the SDL background. The next
bounded experiment is to render a solid diagnostic colour through the same
attached layer and capture the window; if that is also absent, the SDL view is
replacing or covering the layer during its fullscreen lifecycle, and the
presentation target must move to the SDL/Metal view owned by the engine rather
than a child view. That experiment needs one authorized launch; it is not a
pixel-comparison or reconstruction question.

## DSH autonomous execution and remediation

Native DSH goal `goal-1ccf024e-dbf4-4b0c-9fea-e150f9733330` (this session)
owned the work: inspection, code changes, builds, authorized launches,
comparison, diagnosis and correction. The campaign found and fixed a real
reconstruction defect:

1. First GL reference (V 0..1) and first Metal logical capture (V 0..0.75)
   differed on 99 % of pixels.
2. The comparator + live vertex payloads attributed it to the engine-computed
   loading quad: the Metal caps path let the engine persist
   `force_pow2_textures=no`, producing a different live V range.
3. The Metal backend now reports the measured original caps byte (`0x18c = 1`,
   caps `0040000001010101`), the draw records its live vertex payload, and the
   policy requires identical vertex inputs. The repaired Metal run draws V 0..1
   and matches the original GL capture exactly.

The failing pair is preserved as a genuine negative control; a rerun with the
fixed policy reports `invalid` on unmatched vertex inputs.

## Offline regressions

`tests.test_gfx_compare`, `tests.test_gl_reference` and
`tests.test_live_gfx_probe` pass (62 tests). The full repository suite is run on
the final commit; results are recorded with the commit.

## Deferred

- Visible-window evidence for this window/layer composition (blocker above).
- Full title screen, map rendering and any draw beyond the Simple loading quad.
- A decoded engine sampler object (the Simple sampler remains the asset
  contract).
- Host-wide lease cleanup for a crashed game's CrashReporter (a stale inherited
  lock fd required manual cleanup during the campaign).
