# `_GfxDrawIndexed`

entry_rva: `0x00000000015eaa77`
name: `_GfxDrawIndexed`
instruction_count: 124
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

Non-logging calls, in first-seen order: `indirect`, `SShaderOpenGL::EnableVertexAttribArrays`, `_glDrawElements`.
Indirect call instructions in the body: 2. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 124 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `indirect` at instruction `0x00000000015eaaf4`. Non-logging calls, in first-seen order: `indirect`, `SShaderOpenGL::EnableVertexAttribArrays`, `_glDrawElements`.
- `inferred`: Same dirty-uniform and attrib-enable prologue as _GfxDraw. The index count defaults to master+0x68 when the argument is below 1. The zero-base path tail-jumps to the `_glDrawElements` thunk. The other path is `JMP RAX`, which the decompiler names glewDrawElementsBaseVertex. The static row does not name that register target. The element type immediate in the decompilation is 0x1403.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
