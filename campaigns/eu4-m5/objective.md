# EU4 M5 — second authentic draw and differential qualification

Extend the live Metal renderer from the M4 Simple loading text draw to the next
authentic rendering operation in the original loading screen: the alpha-blended
sprite draw at caller RVA `0x14f6c1b` (`CSpriteType::RenderInternal + 2271`).

The work must:

1. observe the selected operation with the existing original-GL observer and
   record its symbol, caller, occurrence, shader identity, primitive, vertex
   layout, constants, texture, blend/raster/scissor state, and pre/post pixels;
2. implement the operation in the existing Metal dispatcher/encoder, without
   selecting semantics by caller RVA and without silently skipping unsupported
   state;
3. compare the composed draw over its authentic prior framebuffer contents
   against the original-GL output, using the existing comparator and the same
   encoder for the physical and diagnostic targets;
4. prove a nonzero pixel contribution and keep the M4 Simple route and all
   regressions passing;
5. record the exact engine-reach blocker if the live engine cannot reach the
   operation, rather than claiming a live-engine route that did not execute.
