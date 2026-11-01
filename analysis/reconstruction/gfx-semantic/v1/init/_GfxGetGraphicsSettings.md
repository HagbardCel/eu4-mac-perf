# `_GfxGetGraphicsSettings`

entry_rva: `0x00000000015ed388`
name: `_GfxGetGraphicsSettings`
instruction_count: 366
source_batch: `gfx-batch-00044.zip`
static_status: `success`
decomp_status: `success`
semantic_status: `partial`
migration_role: `replace-metal`

## ABI

The sealed decompilation offers a C signature. That signature is `inferred`. This dossier does not re-prove System V register assignment, except where a linked contract already did.

## Fields

Offsets named in Behavior are displacements on the object that function uses. A matching displacement in another function is not an alias. Decompiler field names are `inferred`.

## Dependencies

Non-logging calls, in first-seen order: `indirect`, `_SDL_WasInit`, `_SDL_InitSubSystem`, `_SDL_GL_SetAttribute`, `_SDL_GL_SetSwapInterval`, `_SDL_GL_SetAttribute`, `_SDL_CreateWindow`, `_SDL_GL_CreateContext`, `_glewInit`, `_glewGetErrorString`, `indirect`, `_glGetFloatv`, `_glGetIntegerv`, `indirect`, `_glGetIntegerv`, `indirect`, `_SDL_GL_DeleteContext`, `_SDL_DestroyWindow`, `_SDL_QuitSubSystem`, `indirect`.
Indirect call instructions in the body: 6. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 366 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First call in the body is `CLogger::AccessInstance` at instruction `0x00000000015ed3b6`. Non-logging calls, in first-seen order: `indirect`, `_SDL_WasInit`, `_SDL_InitSubSystem`, `_SDL_GL_SetAttribute`, `_SDL_GL_SetSwapInterval`, `_SDL_GL_SetAttribute`, `_SDL_CreateWindow`, `_SDL_GL_CreateContext`, `_glewInit`, `_glewGetErrorString`, `indirect`, `_glGetFloatv`, `_glGetIntegerv`, `indirect`, `_glGetIntegerv`, `indirect`, `_SDL_GL_DeleteContext`, `_SDL_DestroyWindow`, `_SDL_QuitSubSystem`, `indirect`.
- `inferred`: Opens a temporary 128x128 SDL window named as a detection window, creates a GL context, runs glewInit, queries anisotropy and sample limits, writes a handful of fields on the caller settings object, logs the max texture size, then destroys that window and context. This is a separate GLEW entry from _GfxInit.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
