# P5.2b note — Simple shader lifecycle

Project-derived from P5.2a contract (`static_candidate` Simple effect).

Implemented Create / AddVertex / AddPixel / Link / Set / Destroy with a
lifetime-keyed backend registry. Engine `+0/+4/+8` stay zero (not GL names, not
Metal handles). Logical link does not create the PSO; `eu4_gfx_shader_pipeline`
builds `MTLRenderPipelineState` lazily (P5.2c surface).

Compiler-input identity is `asset-maincode-v0` (markers / surviving MainCode
substrings). BuildGLSL / BuildAndAdd remain original for live paths; offline
hosts feed reconstructed sources. Essential inventory stayed bounded (Simple).

Tests: `tests.test_gfx_shader`, entry registry, Metal debug layer.
