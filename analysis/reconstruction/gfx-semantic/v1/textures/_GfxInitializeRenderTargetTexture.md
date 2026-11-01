# `_GfxInitializeRenderTargetTexture`

entry_rva: `0x00000000015ec8e2`
name: `_GfxInitializeRenderTargetTexture`
instruction_count: 175
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

Non-logging calls, in first-seen order: `_glGenTextures`, `_glBindTexture`, `GetOpenGLTextureFormat`, `_glTexImage2D`, `_glBindTexture`, `indirect`, `Gfx_Util::GetBytesPerPixel`.
Indirect call instructions in the body: 19. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 175 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First call in the body is `_glGenTextures` at instruction `0x00000000015ec933`. Non-logging calls, in first-seen order: `_glGenTextures`, `_glBindTexture`, `GetOpenGLTextureFormat`, `_glTexImage2D`, `_glBindTexture`, `indirect`, `Gfx_Util::GetBytesPerPixel`.
- `inferred`: Creates a 2D texture, a depth renderbuffer, and two framebuffers. One framebuffer attaches a color renderbuffer plus depth. The other attaches the texture plus depth. Multisample storage is used when the sample count argument is non-zero. Status is queried and not obviously branched on in the recovered control flow.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
