# `_GfxDraw`

entry_rva: `0x00000000015ea789`
name: `_GfxDraw`
instruction_count: 106
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

Non-logging calls, in first-seen order: `indirect`, `SShaderOpenGL::EnableVertexAttribArrays`, `_glDrawArrays`.
Indirect call instructions in the body: 2. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 106 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `indirect` at instruction `0x00000000015ea806`. Non-logging calls, in first-seen order: `indirect`, `SShaderOpenGL::EnableVertexAttribArrays`, `_glDrawArrays`.
- `inferred`: Requires the shader at master+0x70. If the dirty byte at +0x128 is set, uploads up to six uniform vectors from the constant-buffer slots, then clears that byte. If +0x170 is set, enables vertex attribs from the buffers at +0x130 and clears that byte. Then glDrawArrays with the mode at master+8. A missing shader logs once and returns.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
