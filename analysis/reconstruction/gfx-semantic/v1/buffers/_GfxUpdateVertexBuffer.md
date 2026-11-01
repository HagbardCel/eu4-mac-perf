# `_GfxUpdateVertexBuffer`

entry_rva: `0x00000000015ebd81`
name: `_GfxUpdateVertexBuffer`
instruction_count: 68
source_batch: `gfx-batch-00044.zip`
static_status: `success`
decomp_status: `success`
semantic_status: `partial`
migration_role: `retain-original`

## ABI

The sealed decompilation offers a C signature. That signature is `inferred`. This dossier does not re-prove System V register assignment, except where a linked contract already did.

## Fields

Offsets named in Behavior are displacements on the object that function uses. A matching displacement in another function is not an alias. Decompiler field names are `inferred`.

## Dependencies

Non-logging calls, in first-seen order: `indirect`.
Indirect call instructions in the body: 4. An indirect site is not a resolved callee.

## Uncertainties

- `retain-original` stays valid only while this function's data layout and calling contract stay valid.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 68 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First call in the body is `indirect` at instruction `0x00000000015ebdc3`. Non-logging calls, in first-seen order: `indirect`.
- `inferred`: Binds the buffer name and either replaces the whole store or calls glBufferSubData. A null name is skipped. It also disables vertex attribs on the current shader when that shader exists.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
