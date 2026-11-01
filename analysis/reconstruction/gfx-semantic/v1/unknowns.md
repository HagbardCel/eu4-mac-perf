# Unknowns

Blockers are questions a Metal `_GfxInit` cannot ignore. Everything else can wait until the call that needs it. Status counts are in [inventory.md](inventory.md). No function is `blocked`: every one of the 92 has a successful static and decompile row. `partial` means the reading stopped short of a closed account.

## Blocks Metal `_GfxInit`

The implementation contract is [metal-init-interface.md](metal-init-interface.md). It settles the settings probe, the context pointer, and the default-state objects. Still open, and not classified by that contract:

- The fourteen indirect calls inside `_GfxInit`. Implementation inspects any whose side effect the postconditions require.
- Which capability bytes besides the advertised max-texture dimension a later caller tests. The contract writes the rest of that span as 0.
- The numeric format argument of the 4×4 texture.
- Whether any caller outside the 92 reads `+0x180` as a GL context.
- The meaning of the settings constants `0x400` and `0x30000000300`, and the fields inside a sample record.
- The wireframe pointer alias in [architecture.md](architecture.md).

## Does not block init

- Empty or constant functions: deferred contexts, locks, gamma, used memory, device check, end scene, non-indexed instancing, set-render-type. Their contracts are the constants they return.
- `_GfxForceWireframe` is a one-byte store. It stays `retain-original` only while `+0x194` on its argument still means the flag `_GfxSetRasterizerState` reads. The alias with the master is not proved.
- Draw, texture upload, shader compile, and `BuildGLSL` details. They matter at the first call that reaches them, not before init returns.
- The 126 indirect sites in the boundary census, except any inside `_GfxInit` that a caller actually requires.
- Screenshot and TGA writers.
- Per-field blend, depth, and rasterizer enum tables. The setters name the GL calls. The exact skip conditions are `partial`.
