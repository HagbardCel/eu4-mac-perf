# P5.2a research note — ShowBasicLoadText shader contract

Pinned base: `173321b6d3258576060e7941e9bd91f608e8f543`.
Executable SHA-256: `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.
No EU4 launch. Offline disassembly of the pinned GOG binary plus frozen corpus.

Labels: claims below are `Project-derived` unless marked otherwise.

## Decision 0003 context

Decision 0003 selects the first `_GfxDraw` at caller RVA `0xedfcd1` (return after `call _GfxDraw` at `0xedfccc`) in `CEU3Graphics::ShowBasicLoadText`, with complete master, non-null shader, mode 5, effective count ≥ 3.

Retained session `20261102T213704Z` reached that callsite with a non-null shader pointer and `effective_count` 0 (`draw_nonvisible`). It is **not** a qualifying Decision 0003 invocation. Therefore no claim may use evidence level `runtime_confirmed`.

## Call-chain closure (Project-derived)

`ShowBasicLoadText` (RVA `0xedfaf8`) on the loading-screen branch (`r12b != 0`):

1. `_GfxGetImmediateContext`
2. optionally `LoadLoadingScreens(true)`
3. `CGraphics::BeginScene` / `ClearScene`
4. `CEffectHandler::GetGfxEffect(int)` with `esi = *(CEU3Graphics+0x2c660)`
5. `_GfxUpdateConstantBuffer` / `_GfxSetConstantBuffer` (slot 2)
6. `_GfxSetVertexBuffers` (count 1)
7. `_GfxSetPrimitiveType(4)` — GL_TRIANGLES
8. `_GfxSetShader(*(effect+0))`
9. `_GfxSetTextures` if `*(CEU3Graphics+0x2c650) != 0` (loading-screen texture)
10. `_GfxDraw`
11. `Render2dObjectsToScreen` / `EndScene` / `PresentScene`

Corpus `analysis/frame-model-static.json` `graph.nodes` edges for this symbol include `GetGfxEffect`, `_GfxSetShader`, `_GfxSetTextures`, and `_GfxDraw` (verified).

### Effect-index writers

- Constructor `CEU3Graphics::CEU3Graphics` stores `-1` at `+0x2c660` (RVA store `0xedf057`).
- `LoadLoadingScreens` calls `AddSimpleEffect(this, &(+0x2c660), &stack_int)` at RVA `0xedf363`.

### `AddSimpleEffect` (RVA `0x148c00e`)

- `CEffectHandler::AddGfxEffect(..., "gfx/FX/simple.lua", "Simple", ...)` → writes handle into `*(rsi)` = `+0x2c660`.
- Second call: same file, technique `"Simple3D"` → writes into the stack out-param (discarded by `LoadLoadingScreens` for later draws).

On-disk asset recovered at `/Applications/EuropaUniversalisIV/gfx/FX/simple.shader` (VFS path uses `.lua` naming; contents are the Clausewitz `.shader` script). Fixture hash SHA-256 `b40fd8506d6f0fbfa84927a31fb31ca88f1a26c121d49cb379997aed123b8f1a`.

Effect `Simple` binds `VertexShaderSimple` + `PixelShaderSimple`, sampler `SimpleTexture` index 0, constant buffer `Matrix` / GLSL comment `UNIFORM0[4]`, blend SRC_ALPHA / INV_SRC_ALPHA.

## Evidence level

`static_candidate` for the Decision 0003 callsite’s effect/technique.

- **Unique under control flow:** the callsite’s `GetGfxEffect` index is the `Simple` handle from `AddSimpleEffect`. Not Color.
- **Unresolved variant selector:** exact `BuildGLSL` compiler-input bytes depend on macros/capabilities (historical metal-init sessions stopped on `glGetString(GL_VENDOR)` inside `BuildGLSL`). Offline reconstruction of MainCode bodies is available from the asset; byte-identical compiler input is not proved. Do not claim `statically_determined` or `runtime_confirmed`.

Color Stage D material is **not** this effect. `_GfxSetTextures` on this path is a real texture-state call when the loading-screen texture pointer is non-null; the Simple pixel shader samples `SimpleTexture`. That is independent of B2 Color acquisition results.

## Mode note vs Decision 0003

This callsite executes `_GfxSetPrimitiveType(4)` (triangles) immediately before `_GfxDraw`. Decision 0003’s mode-5 filter may therefore exclude the normal loading-screen submission. Recorded as `Unresolved` relative to the decision text: either the predicate needs a later superseding decision, or another visitation of `0xedfcd1` with mode 5 exists. Shader identity research still binds to this callsite’s effect path.

## Lifecycle disposition (summary)

All six of Create / AddVertex / AddPixel / Link / Set / Destroy are `replace-metal` for a Metal-only process: Link/Set/Destroy retain OpenGL-dependent callees (`SShaderOpenGL::LinkProgram`, `SetAll`, `UnloadAll`) and Set is on the supported draw path. See `lifecycle-disposition.json`.

## Prerequisite inventory (summary)

- **essential:** construction of effect `Simple` from `gfx/FX/simple.lua` (Create → BuildAndAdd* → Add* → Link), plus the loading-screen texture / constant buffer / vertex buffer used by this draw.
- **deferrable:** `Simple3D` (created in the same `AddSimpleEffect`, unused by this `GetGfxEffect` index).
- **unresolved:** full set of other effects constructed during broader `CGraphics` init before a later qualifying draw. Soft-fail logging in `CreateEffectFromFileNew` can allow reaching `_GfxDraw` with hollow shaders under current stubs; that is not a faithful success path.

Essential count is bounded (one primary effect + its draw resources). P5.2b should not expand into a general effect-library migration.

## Consumer use/def

See `consumer-use-def.json`. Engine-visible `0x78` fields `+0` / `+4` / `+8` are OpenGL object names in the original path. Replacement must not place Metal handles into those fields unless every remaining original consumer is replaced or proved unreachable. `_GfxSetShader` → `SShaderOpenGL::SetAll` is an original OpenGL consumer on the supported path until Set is replaced.

## Logical link vs PSO

Engine-visible link success/failure is a P5.2b lifecycle concern. `MTLRenderPipelineState` creation is P5.2c and may wait for vertex layout and attachment formats. Metal does not perform GLSL-style `glLinkProgram`.

## Follow-up

None yet. Research checkpoint will be captured by the campaign runner when used.
