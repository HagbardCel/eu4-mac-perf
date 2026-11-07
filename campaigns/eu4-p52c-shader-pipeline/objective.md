# P5.2c — Production PSO, reflection, controlled offline draws

Pin workspace.commit to the merged P5.2b SHA before running.

## In scope

1. Production create → admit → link → `eu4_gfx_shader_pipeline_for_key` using
   **effective Metal** descriptor fields (device + functions + attachment format +
   blend + write mask + sample count). Not OpenGL state. Not scissor / dynamic
   encoder state. Depth-stencil stays separate.
2. Assert cache **selection/configuration**, not raw pointer inequality across
   processes (this host may still use pointer equality inside its dictionary).
3. `MTLRenderPipelineReflection` (or pipeline creation reflection) vs
   `binding-schema.json` for buffer/texture/sampler interfaces.
4. Controlled offline draws through the production shader/PSO path vs independent
   GL host using Phase A **exact** hashes (local fixtures). BGRA8Unorm; 1×/2× when
   the offline host owns the RT.
5. Mandatory Metal API Validation when GPU tests run; optional `gpucapture` /
   `gpudebug` only on failure or designated fixtures.

## Out of scope

Full `SetShader → SetRT → Clear → SetTextures → buffer update → draw → present`
production renderer sequences (P5.3/P5.4).
