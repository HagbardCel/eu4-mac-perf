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

From the [gfx-init-handoff](../../../../../analysis/metal-census/v1/gfx-init-handoff.md): `rdi` is the window-handle struct, `rsi` is the success byte, and `rax` returns the `0x198`-byte master (`movq %r12, %rax` at `0x15e9c6c`) after the store to `gMasterContext` at `0x15e8fe7`. The B0 run reached `_glewInit` and did not show this return.

## Fields

The handoff establishes these stores on the normal path before that return: the SDL window, an OpenGL context, GLEW flags, blend, rasterizer, depth-stencil, and the 4×4 texture. Offsets for those stores are the established list in [architecture.md](../architecture.md). A matching displacement in another function is not an alias.

## Dependencies

Non-logging calls, in first-seen order: `InitOpenGLConversionTables`, `/usr/lib/libc++.1.dylib::operator.new`, `_SDL_GetWindowFromID`, `_SDL_GetError`, `indirect`, `_SDL_GL_CreateContext`, `_SDL_GetError`, `indirect`, `_SDL_GetWindowSize`, `_glewInit`, `_glewGetErrorString`, `indirect`, `_glGetIntegerv`, `GetDefaultBlendState`, `/usr/lib/libc++.1.dylib::operator.new`, `SOpenGLBlendState::Init`, `GetDefaultDepthStencil`, `/usr/lib/libc++.1.dylib::operator.new`, `SOpenGLDepthStencilState::Init`, `GetDefaultRasterizer`, `/usr/lib/libc++.1.dylib::operator.new`, `SOpenGLRasterizerState::Init`, `GfxCreateTexture`, `GetOpenGLContextRasterizerState`, `GetOpenGLContextDepthStencilState`, `GetOpenGLContextBlendState`, `_GfxSetBlendState`, `_GfxSetDepthStencilState`, `_GfxSetRasterizerState`, `_SDL_GL_GetAttribute`, `_SDL_GL_SetSwapInterval`, `_glGetString`, `indirect`, `/usr/lib/libc++.1.dylib::operator.delete`.
Indirect call instructions in the body: 14. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 770 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `InitOpenGLConversionTables` at instruction `0x00000000015e8ec8`. Non-logging calls, in first-seen order: `InitOpenGLConversionTables`, `/usr/lib/libc++.1.dylib::operator.new`, `_SDL_GetWindowFromID`, `_SDL_GetError`, `indirect`, `_SDL_GL_CreateContext`, `_SDL_GetError`, `indirect`, `_SDL_GetWindowSize`, `_glewInit`, `_glewGetErrorString`, `indirect`, `_glGetIntegerv`, `GetDefaultBlendState`, `/usr/lib/libc++.1.dylib::operator.new`, `SOpenGLBlendState::Init`, `GetDefaultDepthStencil`, `/usr/lib/libc++.1.dylib::operator.new`, `SOpenGLDepthStencilState::Init`, `GetDefaultRasterizer`, `/usr/lib/libc++.1.dylib::operator.new`, `SOpenGLRasterizerState::Init`, `GfxCreateTexture`, `GetOpenGLContextRasterizerState`, `GetOpenGLContextDepthStencilState`, `GetOpenGLContextBlendState`, `_GfxSetBlendState`, `_GfxSetDepthStencilState`, `_GfxSetRasterizerState`, `_SDL_GL_GetAttribute`, `_SDL_GL_SetSwapInterval`, `_glGetString`, `indirect`, `/usr/lib/libc++.1.dylib::operator.delete`.
- `inferred`: Allocates a 0x198-byte master, stores it in gMasterContext and at offset 0 of that block, then fills window, GL context, size, capability bytes, default blend/depth/rasterizer objects, a 4x4 texture, and the current viewport-related slots. Failure paths log and still return that pointer, and the success byte follows GLEW init rather than window lookup alone. This reading does not show that a missing window, a missing context, and a GLEW failure share that success-byte result.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
- `runtime-observed`: The opt-in B0 run reached `_glewInit` inside this function and did not show that the function returned. See [gfx-init-handoff](../../../../../analysis/metal-census/v1/gfx-init-handoff.md). The stores after that call are not covered by that run.
