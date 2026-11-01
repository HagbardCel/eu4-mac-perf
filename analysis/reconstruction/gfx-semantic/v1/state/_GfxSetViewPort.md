# `_GfxSetViewPort`

entry_rva: `0x00000000015ea4f8`
name: `_GfxSetViewPort`
instruction_count: 13
source_batch: `gfx-batch-00043.zip`
static_status: `success`
decomp_status: `success`
semantic_status: `reconstructed`
migration_role: `replace-metal`

## ABI

The sealed decompilation offers a C signature. That signature is `inferred`. This dossier does not re-prove System V register assignment, except where a linked contract already did.

## Fields

Offsets named in Behavior are displacements on the object that function uses. A matching displacement in another function is not an alias. Decompiler field names are `inferred`.

## Dependencies

Non-logging calls, in first-seen order: `_glViewport`.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 13 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `_glViewport` at instruction `0x00000000015ea527`. Non-logging calls, in first-seen order: `_glViewport`.
- `inferred`: Copies the caller rectangle onto master+0xac through +0xc0 and calls glViewport. Which of those dwords are x, y, width, and height is fixed by the glViewport argument order in the decompilation: the second vector's two dwords, then the first vector's y and x. Treat that component order as inferred until an instruction-level read confirms it.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
