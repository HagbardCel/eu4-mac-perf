# `_GfxSetRenderTarget`

entry_rva: `0x00000000015ed004`
name: `_GfxSetRenderTarget`
instruction_count: 42
source_batch: `gfx-batch-00044.zip`
static_status: `success`
decomp_status: `success`
semantic_status: `reconstructed`
migration_role: `replace-metal`

## ABI

The sealed decompilation offers a C signature. That signature is `inferred`. This dossier does not re-prove System V register assignment, except where a linked contract already did.

## Fields

Offsets named in Behavior are displacements on the object that function uses. A matching displacement in another function is not an alias. Decompiler field names are `inferred`.

## Dependencies

Non-logging calls, in first-seen order: `indirect`, `_glViewport`.
Indirect call instructions in the body: 2. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 42 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First call in the body is `indirect` at instruction `0x00000000015ed03b`. Non-logging calls, in first-seen order: `indirect`, `_glViewport`.
- `inferred`: Binds framebuffer 0, or the name at object+0xc when an object is passed, stores the pointer at master+0x78, and sets the viewport from that object's width and height.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
