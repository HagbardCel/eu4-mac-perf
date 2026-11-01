# `_GfxDrawIndexedInstanced`

entry_rva: `0x00000000015eac9c`
name: `_GfxDrawIndexedInstanced`
instruction_count: 63
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

Non-logging calls, in first-seen order: `indirect`, `SShaderOpenGL::EnableVertexAttribArrays`.
Indirect call instructions in the body: 1. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 63 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `indirect` at instruction `0x00000000015ead02`. Non-logging calls, in first-seen order: `indirect`, `SShaderOpenGL::EnableVertexAttribArrays`.
- `inferred`: Uploads dirty uniforms and enables attribs when a shader is current. The body then tail-jumps through RAX. The decompiler names that target glewDrawElementsInstanced. The static row does not. The empty _GfxDrawInstanced path is not used.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
