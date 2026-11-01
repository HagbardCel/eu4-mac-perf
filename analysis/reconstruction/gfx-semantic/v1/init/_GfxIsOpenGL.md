# `_GfxIsOpenGL`

entry_rva: `0x00000000015c5a82`
name: `_GfxIsOpenGL`
instruction_count: 8
source_batch: `gfx-batch-00042.zip`
static_status: `success`
decomp_status: `success`
semantic_status: `reconstructed`
migration_role: `undetermined`

## ABI

The sealed decompilation offers a C signature. That signature is `inferred`. This dossier does not re-prove System V register assignment, except where a linked contract already did.

## Fields

Offsets named in Behavior are displacements on the object that function uses. A matching displacement in another function is not an alias. Decompiler field names are `inferred`.

## Dependencies

Non-logging calls, in first-seen order: `_GfxGetRenderType`.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- Whether Metal can keep this predicate true is `undetermined`. It follows `_GfxGetRenderType`, and callers of that result are not established here.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 8 instructions, static `success`, decompile `success`, in `gfx-batch-00042.zip`. First call in the body is `_GfxGetRenderType` at instruction `0x00000000015c5a86`. Non-logging calls, in first-seen order: `_GfxGetRenderType`.
- `inferred`: Calls _GfxGetRenderType and returns whether that value minus 2 is below 3. With the current constant 2, the result is true.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
