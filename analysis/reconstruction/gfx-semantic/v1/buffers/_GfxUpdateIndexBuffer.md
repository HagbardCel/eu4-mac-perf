# `_GfxUpdateIndexBuffer`

entry_rva: `0x00000000015ec23c`
name: `_GfxUpdateIndexBuffer`
instruction_count: 31
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

Non-logging calls, in first-seen order: `indirect`.
Indirect call instructions in the body: 1. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 31 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First call in the body is `indirect` at instruction `0x00000000015ec264`. Non-logging calls, in first-seen order: `indirect`.
- `inferred`: If the buffer name is non-zero, binds it and replaces the store with count*2 bytes. A non-positive count uses the stored count.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
