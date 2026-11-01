# Unknowns

Blockers are questions a Metal `_GfxInit` cannot ignore. Everything else can wait until the call that needs it. Status counts are in [inventory.md](inventory.md). No function is `blocked`: every one of the 92 has a successful static and decompile row. `partial` means the reading stopped short of a closed account.

## Blocks Metal `_GfxInit`

- The engine-visible postconditions in [architecture.md](architecture.md) are the established list. The open items there are the blockers: unnamed indirect calls inside init, which stored GLEW flags callers test, the 4x4 texture format, whether `+0x180` must still be a GL context, and the settings constants written by `_GfxGetGraphicsSettings`.
- `_GfxGetGraphicsSettings` is a second `_glewInit`, on a window it creates and destroys. Skipping it, or skipping only the GLEW call inside `_GfxInit`, does not leave the settings object filled.
- Default state objects at `+0x80`, `+0x88`, and `+0x90` are `SOpenGL*` today. Init can keep creating them only if the later setters still understand them. Replacing the objects during init means those setters are in the same change.

## Does not block init

- Empty or constant functions: deferred contexts, locks, gamma, used memory, device check, end scene, non-indexed instancing, set-render-type. Their contracts are the constants they return.
- `_GfxForceWireframe` is a one-byte store. It stays `retain-original` only while `+0x194` on its argument still means the flag `_GfxSetRasterizerState` reads. The alias with the master is not proved.
- Draw, texture upload, shader compile, and `BuildGLSL` details. They matter at the first call that reaches them, not before init returns.
- The 126 indirect sites in the boundary census, except any inside `_GfxInit` that a caller actually requires.
- Screenshot and TGA writers.
- Per-field blend, depth, and rasterizer enum tables. The setters name the GL calls. The exact skip conditions are `partial`.
