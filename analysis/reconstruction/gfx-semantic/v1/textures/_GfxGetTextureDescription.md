# `_GfxGetTextureDescription`

entry_rva: `0x00000000015ec7f1`
name: `_GfxGetTextureDescription`
instruction_count: 12
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

No non-logging call.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- `retain-original` stays valid only while this function's data layout and calling contract stay valid.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 12 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First memory operand is `MOV` RDI+0x40 at instruction `0x00000000015ec7f5`. No non-logging call.
- `inferred`: Copies the description qwords and a trailing dword from the texture object, starting at +0x28, into the caller buffer.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
