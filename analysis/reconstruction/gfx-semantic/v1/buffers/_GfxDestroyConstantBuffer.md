# `_GfxDestroyConstantBuffer`

entry_rva: `0x00000000015ec318`
name: `_GfxDestroyConstantBuffer`
instruction_count: 26
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

Non-logging calls, in first-seen order: `/usr/lib/libc++.1.dylib::operator.delete[]`, `/usr/lib/libc++.1.dylib::operator.delete`.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- `retain-original` stays valid only while this function's data layout and calling contract stay valid.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 26 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First call in the body is `/usr/lib/libc++.1.dylib::operator.delete[]` at instruction `0x00000000015ec32f`. Non-logging calls, in first-seen order: `/usr/lib/libc++.1.dylib::operator.delete[]`, `/usr/lib/libc++.1.dylib::operator.delete`.
- `inferred`: Deletes the CPU array at header+8, returns the header to a 128-slot cache or frees it, and decrements nGLCBCount.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
