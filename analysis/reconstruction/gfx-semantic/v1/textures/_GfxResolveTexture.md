# `_GfxResolveTexture`

entry_rva: `0x00000000015ece63`
name: `_GfxResolveTexture`
instruction_count: 56
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

Non-logging calls, in first-seen order: `indirect`, `_glViewport`.
Indirect call instructions in the body: 4. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 56 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First call in the body is `indirect` at instruction `0x00000000015ece91`. Non-logging calls, in first-seen order: `indirect`, `_glViewport`.
- `inferred`: If source and dest are the same object and a byte at dest+0x1b is clear, it returns. Otherwise it binds two framebuffers, blits, then rebinds the current target at master+0x78 and resets the viewport from that target's size.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
