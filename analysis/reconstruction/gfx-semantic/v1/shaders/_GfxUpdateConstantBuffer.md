# `_GfxUpdateConstantBuffer`

entry_rva: `0x00000000015ec3b5`
name: `_GfxUpdateConstantBuffer`
instruction_count: 15
source_batch: `gfx-batch-00044.zip`
static_status: `success`
decomp_status: `success`
semantic_status: `reconstructed`
migration_role: `retain-original`

## ABI

The sealed decompilation offers a C signature. That signature is `inferred`. This dossier does not re-prove System V register assignment, except where a linked contract already did.

## Fields

Offsets named in Behavior are displacements on the object that function uses. A matching displacement in another function is not an alias. Decompiler field names are `inferred`.

## Dependencies

Non-logging calls, in first-seen order: `_memcpy`.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- `retain-original` stays valid only while this function's data layout and calling contract stay valid.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 15 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First call in the body is `_memcpy` at instruction `0x00000000015ec3d7`. Non-logging calls, in first-seen order: `_memcpy`.
- `inferred`: Copies dwords into the CPU pointer at header+8. Selector -1 uses the header's own count. A null header returns.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
