# `_GfxSetConstantBuffer`

entry_rva: `0x00000000015ec36a`
name: `_GfxSetConstantBuffer`
instruction_count: 21
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

- `static-verified`: 21 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First memory operand is `MOV` RDI+0 at instruction `0x00000000015ec36e`. No non-logging call.
- `inferred`: No calls. Loads the object through the handle. If the selector is -1, the word count is the buffer's first dword plus 3, shifted right by 2. Otherwise the count is a signed divide-by-4 of the selector plus 3, with the wrap described in the existing contract. Writes the count, the pointer, and sets the byte at +0x128.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
- `runtime-observed`: The 2026-10-25 P5a session qualified 64 calls of this function ([publication](../../../../../results/20261025T155406Z-gfx-live-publication/publication-summary.md)). That does not cover every selector.
- Reviewed ABI and offsets: [contract](../../../_GfxSetConstantBuffer/contract.md).
