# Original EU4 renderer knowledge

Facts below are tied to the pinned executable/version in
[reverse_engineering/target.json](../../reverse_engineering/target.json). A
different executable or driver build needs its own verified observations. The
[renderer index](README.md) links the semantic dossiers and executable evidence.

## Observation method

Original OpenGL is authoritative. A test-only interposer records ordered calls,
resources, state and reference pixels while the game runs its original renderer. See
[frame trace](../../benchmark/eu4_gfx_frame_trace.c) and
[reconstruction charter](../renderer-reconstruction-and-differential-validation.md).

## Draw lifecycle (observed model)

1. Engine code selects a program, textures, sampler state and render target.
2. `_GfxDraw*`/`_GfxDrawIndexed` submit vertices with typed attributes.
3. Clears/copies/resolves write render targets and copies.
4. A present or scene boundary seals the frame. Selected events can have pre-
   and post-operation checkpoints on the default framebuffer or offscreen targets.

The graph builder orders events and derives same-resource producers/consumers,
including copies. Writer attribution must come from that graph; adjacency alone
is not evidence. See
[graph builder](../../automation/gfx_frame_graph.py) and
[step 02](../../plans/closed/harness-review-20261120/steps/02-reference-control.md).

## Resources and state

- Textures carry levels, raw internal format, decoded RGBA and, for S3TC, the
  original compressed blocks with raw GL enum, size and stride.
- Samplers carry min/mag, wrap S/T/R, LOD bias/clamps, compare mode/function
  and fault flags.
- Raster state carries viewport, scissor, cull enable/face/front winding,
  blend, depth/stencil, color mask, polygon offset and fill mode.
- Legacy seals can lack the drawable's internal format, sample count or depth
  precision. Drawable metadata applies to framebuffer 0. Offscreen depth
  storage requires a query of the actual attachment.

## Program/texture/copy identities

- Programs are identified by captured name plus stage source digests; effective
  linkage (attribute/varying locations) is part of the translation identity.
- Texture versions are content-addressed; a repeated payload may legitimately
  share a blob name. The verifier treats an omitted size as unknown, not zero.
- Copies distinguish destination versions from source evidence; a source match
  from another capture is excluded from the causal bound.

## Known inference limits

- The static census finds 32 direct GL callers (25 inside `_Gfx*`); indirect
  dispatch is unclassified. The census guides selection, not completeness.
- The original engine's exact depth/sample/drawable state for a given draw may
  be unavailable in a legacy capture. State equivalence files are focused
  evidence, not blanket proof.
- Terrain residuals remain unexplained. Present 616 checkpoint 15 differs by
  607 pixels (maximum 2), and present 904 differs by 4 (maximum 1).

## Sources

- [Renderer census](../../analysis/metal-census/v1/boundary.md)
- [RE queries](../../reverse_engineering/re_query.py)
- [Engine contracts](../../automation/gfx_contracts)
- [Current status](../status.md)
- [Review assessment](../../plans/closed/harness-review-20261120/assessment.md)
