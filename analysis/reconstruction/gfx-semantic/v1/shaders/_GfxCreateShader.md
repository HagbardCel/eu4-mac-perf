# `_GfxCreateShader`

entry_rva: `0x00000000015eae97`
name: `_GfxCreateShader`
instruction_count: 16
source_batch: `gfx-batch-00043.zip`
static_status: `success`
decomp_status: `success`
semantic_status: `reconstructed`
migration_role: `retain-original`

## ABI

The sealed decompilation offers a C signature. That signature is `inferred`. This dossier does not re-prove System V register assignment, except where a linked contract already did.

## Fields

Offsets named in Behavior are displacements on the object that function uses. A matching displacement in another function is not an alias. Decompiler field names are `inferred`.

## Dependencies

Non-logging calls, in first-seen order: `/usr/lib/libc++.1.dylib::operator.new`, `indirect`.
Indirect call instructions in the body: 1. An indirect site is not a resolved callee.

## Uncertainties

- `retain-original` stays valid only while this function's data layout and calling contract stay valid.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 16 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `/usr/lib/libc++.1.dylib::operator.new` at instruction `0x00000000015eaea2`. Non-logging calls, in first-seen order: `/usr/lib/libc++.1.dylib::operator.new`, `indirect`.
- `inferred`: Allocates 0x78 bytes, clears the qword at +4, and stores a glCreateProgram name in the first dword.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
