# `_GfxInit` handoff

Static call edges only, from [frame-model-static.json](../../frame-model-static.json) and [boundary.json](boundary.json). This is not a reconstruction. A row is engine-visible, OpenGL-only, or undetermined. Logging and C++ stream calls are omitted.

`_GfxInit` is `0x15e8ea0`. The opt-in B0 checkpoint returns from the primary window, bypasses `SDL_GL_SetSwapInterval` at `0x13cc1e4`, returns a sentinel from `SDL_GL_CreateContext` at `0x15e90d5`, and exits at `_glewInit` `0x15e91fd`. That sentinel is not a context. `_GfxGetGraphicsSettings` still calls `_glewInit` at `0x15ed576` and is not part of this checkpoint.

| Dependency | Static evidence | Unknown | Label |
| --- | --- | --- | --- |
| OpenGL conversion tables | `InitOpenGLConversionTables` is an early direct call | Whether any table is reused outside GL | undetermined |
| SDL window and context setup | `_GfxInit` calls `SDL_GetWindowFromID`, then `SDL_GL_CreateContext` at `0x15e90d5`, then `SDL_GetWindowSize` | Context ownership and what the engine stores | undetermined |
| GLEW | `_glewInit` at `0x15e91fd` is after context creation | Which capability decisions later code requires | undetermined |
| Rasterizer, blend, and depth-stencil defaults | `GetDefaultRasterizer`, `SOpenGLRasterizerState::Init`, then the three `GetOpenGLContext*` readers | Representation, ownership, and invariants | undetermined |
| `GfxCreateTexture` | Called at `0x15e99fb`, before those readers | Arguments, purpose, and lifetime | undetermined |
| `GetOpenGLContext*` readers | They read the original GL rasterizer, depth-stencil, and blend state | The Metal defaults that would replace those reads | OpenGL-only |
| `_GfxInit` return and stored state | The call graph does not show the return value or the globals written | Caller-visible postconditions | undetermined |

The next implementation replaces the engine-visible part of this list with Metal. It does not add another sentinel hook. The readers are the only row the graph can mark OpenGL-only; everything they feed is still undetermined.
