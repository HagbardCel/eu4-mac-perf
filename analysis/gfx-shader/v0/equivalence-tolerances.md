# P5.2c Simple GL–Metal equivalence tolerances

Predeclared for the **production-path differential**: GL reference draw of the
Simple asset stages versus Metal drawn through the production
`eu4_gfx_shader_*` / PSO cache path (and the controlled offline host that
shares the same tolerances).

| Parameter | Value |
| --- | --- |
| Resolution | 32×32 RGBA8 |
| Channel tolerance | absolute ≤ 2 / 255 |
| Coverage | at least half of pixels with R > 100 after draw |
| Blend | SRC_ALPHA / INV_SRC_ALPHA |
| Inputs | identity `UNIFORM0[4]`, 1×1 texture (200,100,50,255), red vertex color |

PSO cache keys include the Metal **device** pointer plus stage functions and
attachment state. Reflection validation (`eu4_gfx_shader_reflect_validate`)
checks linked VS/PS functions before a production draw is admitted.

GPU capture / Metal frame-capture / replay tooling is **diagnostic-only**: it
must not admit shaders, promote oracles, or satisfy this campaign's pass.

This is bounded equivalence for the asset-maincode-v0 Simple translation. It is
**not** P5.4 authentic engine-owned draw qualification and does not claim
Decision 0003 runtime confirmation.
