# P5.2c Simple GL–Metal equivalence tolerances

Predeclared for the offline Simple reference (`tests/metal_simple_ref_host.m`).

| Parameter | Value |
| --- | --- |
| Resolution | 32×32 RGBA8 |
| Channel tolerance | absolute ≤ 2 / 255 |
| Coverage | at least half of pixels with R > 100 after draw |
| Blend | SRC_ALPHA / INV_SRC_ALPHA |
| Inputs | identity `UNIFORM0[4]`, 1×1 texture (200,100,50,255), red vertex color |

This is bounded offline equivalence for the asset-maincode-v0 Simple translation. It is **not** P5.4 authentic engine-owned draw qualification and does not claim Decision 0003 runtime confirmation.
