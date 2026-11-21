# M7 event-20 diagnosis

Date: 21 November 2026. Seal: session `47590918-d63a-4798-9737-df00285ad51b`,
capture frame 1, 283 operations, reference
`b38d3804bce3d3575f848fbe800859e7675337e039d2606a984327f997bab362`.
All statements below are for this one sealed frame; no observation from another
seal is combined with it.

## Earliest authoritative divergence

- Event: **1** (`_GfxDrawIndexed`, caller `0x10d4e50`, program 310).
- Target: `tex:433`, a 1728×1117 GL `RGB8` colour attachment with a packed
  depth/stencil renderbuffer; the Metal materialization is `BGRA8Unorm`.
- Timing: post-event observation after the draw executed.
- Mismatch: **23 pixels**, max channel error **1**, alpha not applicable (the
  original attachment has no alpha channel); first differing pixel (129, 4);
  bounds x4..169, y4..559.
- Draw state: indexed draw of 24,576 indices, blend disabled, depth test LESS
  with depth write, cull enabled, dither enabled, multisample enabled, no
  scissor, full viewport.
- Sampled input: `tex2` = 2816×1024 `DXT1_ALPHA` atlas (the event stream carries
  both the captured decoded RGBA8 levels and the original BC blocks), plus
  `tex0`/`tex1`; program 310 has translated vertex and pixel stages.
- Event 0 (the preceding clear to the same target) matches exactly on R/G/B.
- Events 2–9 diverge on the same target, growing to 5,199 pixels / max 118.
  Events 10 and 12–17 match exactly, including alpha. Events 18–20 diverge on
  `fb:0` (359/12, 1,186/12, 4,781/22).

The first divergent operation is therefore event 1. The later event-18
divergence is real but is not the earliest one and was not used to bound the
campaign's stop.

## Bounded hypotheses

### H1 — texture representation (BC1/DXT1_ALPHA)

A 1-LSB, sparse difference on a draw that samples a DXT1_ALPHA atlas is
consistent with decode/filter rounding. The sealed replay already prefers the
captured BC blocks.

Discriminating test (used one offline replay): force the captured decoded RGBA8
chain instead of the BC blocks and replay event 1.

Result: decoded **17,446 pixels / max 1** versus compressed **23 pixels / max
1**. Decoded is much worse. Conclusion: **the compressed path is not the
correction and the remaining 23 pixels are not explained by falling back to
decoded RGBA8.** H1 is refuted as a correction; it remains a possible partial
cause only at the BC-decode-arithmetic level, not representation.

### H2 — GL dithering / unsupported sampler state

`gl_dither` and `multisample` are recorded as unsupported Metal state in the
shared coverage. If dithering were the cause, the differing pixels should show
a spatial pattern (a repeating dither matrix) rather than isolated points.

Discriminating test (offline pixel analysis, no replay): list the 23 differing
coordinates and their modulo-4 positions.

Result: the differences are scattered across (129,4), (150,13), (100,24),
(110,27), (111,29), … with no periodic modulo-4 cluster; each is a single ±1
change in one stored colour channel. Conclusion: **dithering is not supported
as the primary cause** by this evidence. A direct A/B would require a GL dither
toggle that the original process does not expose; this is still an unsupported
state, not a proven cause.

### H3 — BC1 decode or sampler/filter rounding

The residual pattern remains compatible with a one-LSB difference between the
original Apple GL DXT1 decode/filter path and Metal's BC1 path at a handful of
texel/edge samples, or with a state difference (depth/raster at isolated
samples). The differing points are isolated rather than edge lines, which makes
a pure depth-edge explanation less likely but not impossible.

Discriminating evidence needed: a per-texel comparison at the exact sample
coordinates of the event-1 draw (the atlas level, UV and filter footprint), or
a minimal synthetic BC1 texture rendered through both APIs with the same
sampler. Neither was performed; the campaign had one correction evaluation and
no accepted correction at the first divergence.

Conclusion: **root cause not established.** The leading unresolved hypothesis is
BC1 decode/sampler rounding at isolated samples; texture representation
fallback and dithering are weakened/refuted as primary causes.

## Later divergence

Events 18–20 diverge on the default framebuffer with alpha exact: 359 px/max 12,
1,186 px/max 12, 4,781 px/max 22, bounds beginning x178 and growing. They are
preserved in `analysis/m7/m7-event20-comparison.json` and the shadow outputs.
Because the campaign stops at event 1, no correction was attempted for them and
they are not combined with the event-1 difference.

## Control status

The reconstructed original-API OpenGL control was not run for this seal. It is
not required for a direct original-EU4-versus-Metal mismatch, and no causal
Metal claim here depends on it. The live drawable identity for issue #278 (CGL
colour 32/alpha 8/depth 32/stencil 8, legacy, no MSAA; default-window; colour
8/8/8/8 unsigned normalized; depth 32; stencil 8) is recorded in the same seal.
An equivalent control target is now format-identifiable, but the control still
needs an independently reviewed target assertion and a re-pinned authority
before it could support a causal claim.

## Result

`FIRST_DIVERGENCE` at event 1; exact verified prefix `[0, 0]`; exact isolated
observations at events 10 and 12–17; event 11 excluded as an isolated
unsupported auxiliary clear. `requires_additional_evidence` for the event-1
root cause. No equivalence, root cause, or correction is claimed.
