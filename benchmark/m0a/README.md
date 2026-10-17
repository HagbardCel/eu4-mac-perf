# M0a — single-draw Metal probe (scaffold)

Parallel track to R0-M (see [`docs/eu4_recommended_strategy.md`](../../docs/eu4_recommended_strategy.md)). This directory will host a standalone x86_64 Metal+GL harness under Rosetta to:

1. Trace one representative mesh draw (`CPdxMeshObject::RenderBuckets` → `GfxDrawIndexed`).
2. Reproduce it in Metal with MSL translated from the captured effect.
3. Microbenchmark semantic-equivalence GL vs B0-style Metal (backend tax).
4. Draft the provisional command IR.

**Status:** scaffold only — implementation follows R0-A Gfx/shader inventory.

Build entry point (placeholder):

```sh
# future: make -C benchmark/m0a
```

Static path references: [`analysis/mesh-render-path.md`](../../analysis/mesh-render-path.md), [`analysis/draw-callers.json`](../../analysis/draw-callers.json).
