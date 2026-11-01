# `_GfxGetRenderType`

entry_rva: `0x00000000015ea460`
name: `_GfxGetRenderType`
instruction_count: 5
source_batch: `gfx-batch-00043.zip`
static_status: `success`
decomp_status: `success`
semantic_status: `reconstructed`
migration_role: `undetermined`

## ABI

The sealed decompilation offers a C signature. That signature is `inferred`. This dossier does not re-prove System V register assignment, except where a linked contract already did.

## Fields

Offsets named in Behavior are displacements on the object that function uses. A matching displacement in another function is not an alias. Decompiler field names are `inferred`.

## Dependencies

No non-logging call.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- Whether Metal can keep returning the OpenGL backend identifier is `undetermined`. Callers of that result are not established here.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 5 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. The body has no call and no memory operand. No non-logging call.
- `inferred`: Returns the constant 2. No call.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
