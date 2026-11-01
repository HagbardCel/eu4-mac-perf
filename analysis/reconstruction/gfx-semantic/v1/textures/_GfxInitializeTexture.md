# `_GfxInitializeTexture`

entry_rva: `0x00000000015ec43b`
name: `_GfxInitializeTexture`
instruction_count: 130
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

Non-logging calls, in first-seen order: `Gfx_Util::GetBitsPerPixel`, `Gfx_Util::IsBlockCompressed`, `GetOpenGLTextureFormat`, `_glGenTextures`, `_glBindTexture`, `GetOpenGLCubemapFace`, `_glPixelStorei`, `GetOpenGLCubemapFace`, `_glTexImage2D`, `_glPixelStorei`, `indirect`.
Indirect call instructions in the body: 1. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 130 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First call in the body is `Gfx_Util::GetBitsPerPixel` at instruction `0x00000000015ec477`. Non-logging calls, in first-seen order: `Gfx_Util::GetBitsPerPixel`, `Gfx_Util::IsBlockCompressed`, `GetOpenGLTextureFormat`, `_glGenTextures`, `_glBindTexture`, `GetOpenGLCubemapFace`, `_glPixelStorei`, `GetOpenGLCubemapFace`, `_glTexImage2D`, `_glPixelStorei`, `indirect`.
- `inferred`: Resolves a GL internal format, generates and binds a texture name on the 0x44-byte object, then uploads each image as a 2D or cubemap face. Compressed uploads are a separate call. Pixel-store alignment is set to 1 around the upload and restored to 4.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
