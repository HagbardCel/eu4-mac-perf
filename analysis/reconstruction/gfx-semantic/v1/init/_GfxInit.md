# `_GfxInit`

entry_rva: `0x00000000015e8ea0`
name: `_GfxInit`
instruction_count: 770
source_batch: `gfx-batch-00043.zip`
static_status: `success`
decomp_status: `success`
semantic_status: `partial`
migration_role: `replace-metal`

## ABI

The sealed decompilation offers a C signature. That signature is `inferred`. This dossier does not re-prove System V register assignment, except where a linked contract already did.

## Fields

Offsets named in Behavior are displacements on the object that function uses. A matching displacement in another function is not an alias. Decompiler field names are `inferred`.

## Dependencies

Non-logging calls, in first-seen order: `InitOpenGLConversionTables`, `/usr/lib/libc++.1.dylib::operator.new`, `_SDL_GetWindowFromID`, `_SDL_GetError`, `indirect`, `_SDL_GL_CreateContext`, `_SDL_GetError`, `indirect`, `_SDL_GetWindowSize`, `_glewInit`, `_glewGetErrorString`, `indirect`, `_glGetIntegerv`, `GetDefaultBlendState`, `/usr/lib/libc++.1.dylib::operator.new`, `SOpenGLBlendState::Init`, `GetDefaultDepthStencil`, `/usr/lib/libc++.1.dylib::operator.new`, `SOpenGLDepthStencilState::Init`, `GetDefaultRasterizer`, `/usr/lib/libc++.1.dylib::operator.new`, `SOpenGLRasterizerState::Init`, `GfxCreateTexture`, `GetOpenGLContextRasterizerState`, `GetOpenGLContextDepthStencilState`, `GetOpenGLContextBlendState`, `_GfxSetBlendState`, `_GfxSetDepthStencilState`, `_GfxSetRasterizerState`, `_SDL_GL_GetAttribute`, `_SDL_GL_SetSwapInterval`, `_glGetString`, `indirect`, `/usr/lib/libc++.1.dylib::operator.delete`.
Indirect call instructions in the body: 14. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 770 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `InitOpenGLConversionTables` at instruction `0x00000000015e8ec8`. Non-logging calls, in first-seen order: `InitOpenGLConversionTables`, `/usr/lib/libc++.1.dylib::operator.new`, `_SDL_GetWindowFromID`, `_SDL_GetError`, `indirect`, `_SDL_GL_CreateContext`, `_SDL_GetError`, `indirect`, `_SDL_GetWindowSize`, `_glewInit`, `_glewGetErrorString`, `indirect`, `_glGetIntegerv`, `GetDefaultBlendState`, `/usr/lib/libc++.1.dylib::operator.new`, `SOpenGLBlendState::Init`, `GetDefaultDepthStencil`, `/usr/lib/libc++.1.dylib::operator.new`, `SOpenGLDepthStencilState::Init`, `GetDefaultRasterizer`, `/usr/lib/libc++.1.dylib::operator.new`, `SOpenGLRasterizerState::Init`, `GfxCreateTexture`, `GetOpenGLContextRasterizerState`, `GetOpenGLContextDepthStencilState`, `GetOpenGLContextBlendState`, `_GfxSetBlendState`, `_GfxSetDepthStencilState`, `_GfxSetRasterizerState`, `_SDL_GL_GetAttribute`, `_SDL_GL_SetSwapInterval`, `_glGetString`, `indirect`, `/usr/lib/libc++.1.dylib::operator.delete`.
- `inferred`: Allocates a 0x198-byte master, stores it in gMasterContext and at offset 0 of that block, then fills window, GL context, size, capability bytes, default blend/depth/rasterizer objects, a 4x4 texture, and the current viewport-related slots. Failure paths log and still return that pointer. The success byte written through the second argument follows GLEW init, not context creation alone.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
- `runtime-observed`: The opt-in B0 run reached `_glewInit` inside this function and did not show that the function returned. See [gfx-init-handoff](../../../../../analysis/metal-census/v1/gfx-init-handoff.md). The stores after that call are not covered by that run.
