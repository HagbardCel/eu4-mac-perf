# `_GfxSetVertexBuffers`

entry_rva: `0x00000000015ebf41`
name: `_GfxSetVertexBuffers`
instruction_count: 125
source_batch: `gfx-batch-00044.zip`
static_status: `success`
decomp_status: `success`
semantic_status: `partial`
migration_role: `replace-metal`

## ABI

The sealed decompilation offers a C signature. That signature is `inferred`. This dossier does not re-prove System V register assignment, except where a linked contract already did.

## Fields

Offsets named in Behavior are displacements on the object that function uses. A matching displacement in another function is not an alias. Decompiler field names are `inferred`.

## Dependencies

Non-logging calls, in first-seen order: `indirect`.
Indirect call instructions in the body: 2. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 125 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First call in the body is `indirect` at instruction `0x00000000015ebf9d`. Non-logging calls, in first-seen order: `indirect`.
- `inferred`: Disables the current shader's attrib arrays, then for each supplied buffer stores the buffer pointer at master+0x130, a stride word at +0x150, and an offset word at +0x160, scaled by slot. Slot 0 also writes the vertex count and stride at +0x64 and +0x6c. A null buffer logs once. The attrib-dirty byte at +0x170 is set.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
