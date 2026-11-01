# `_GfxAddVertexLayout`

entry_rva: `0x00000000015eaec7`
name: `_GfxAddVertexLayout`
instruction_count: 71
source_batch: `gfx-batch-00043.zip`
static_status: `success`
decomp_status: `success`
semantic_status: `partial`
migration_role: `retain-original`

## ABI

The sealed decompilation offers a C signature. That signature is `inferred`. This dossier does not re-prove System V register assignment, except where a linked contract already did.

## Fields

Offsets named in Behavior are displacements on the object that function uses. A matching displacement in another function is not an alias. Decompiler field names are `inferred`.

## Dependencies

Non-logging calls, in first-seen order: `/usr/lib/libc++.1.dylib::operator.new[]`.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- `retain-original` stays valid only while this function's data layout and calling contract stay valid.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 71 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `/usr/lib/libc++.1.dylib::operator.new[]` at instruction `0x00000000015eaf03`. Non-logging calls, in first-seen order: `/usr/lib/libc++.1.dylib::operator.new[]`.
- `inferred`: Counts incoming elements and allocates an array of 0x28-byte records on the destination object. Element field meanings inside each record are not fully mapped.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
