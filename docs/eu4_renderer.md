# Original EU4 renderer knowledge

Facts below are tied to the pinned executable/version in
[reverse_engineering/target.json](../reverse_engineering/target.json). A
different executable or driver build needs its own verified observations. This
document is an index, not a replacement for the cited evidence.

## Observation method

Original OpenGL is authoritative. EU4 runs with a test-only interposer that
records ordered calls, resources, state and reference pixels into a sealed
frame. The capture does **not** replace the game renderer and does not run a
Metal-only session. See
[frame trace](../benchmark/eu4_gfx_frame_trace.c) and
[reconstruction charter](renderer-reconstruction-and-differential-validation.md).

## Draw lifecycle (observed model)

1. Engine code selects a program, textures, sampler state and render target.
2. `_GfxDraw*`/`_GfxDrawIndexed` submit vertices with typed attributes.
3. Clears/copies/resolves write render targets and copies.
4. A present/scene boundary seals the frame; default-framebuffer checkpoints
   may be captured before selected events.

The graph builder orders events and derives same-resource producers/consumers,
including copies. Writer attribution must come from that graph; adjacency alone
is not evidence. See
[graph builder](../automation/gfx_frame_graph.py) and
[step 02](../plans/harness-review/steps/02-reference-control.md).

## Resources and state

- Textures carry levels, raw internal format, decoded RGBA and, for S3TC, the
  original compressed blocks with raw GL enum, size and stride.
- Samplers carry min/mag, wrap S/T/R, LOD bias/clamps, compare mode/function
  and fault flags.
- Raster state carries viewport, scissor, cull enable/face/front winding,
  blend, depth/stencil, color mask, polygon offset and fill mode.
- Captured default-framebuffer state is not sufficient to reconstruct drawable
  color encoding, sample count or depth precision; those remain explicit
  unknowns.

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
- Terrain shading remains unresolved: present 616 checkpoint 15 is 607 px/max
  2, present 904 is 4 px/max 1, and the authentic cause is not established.

## Sources

- [Renderer census](../analysis/metal-census/v1/boundary.md)
- [RE queries](../reverse_engineering/re_query.py)
- [Engine contracts](../automation/gfx_contracts)
- [Current status](status.md)
- [Review assessment](../plans/harness-review/assessment.md)
