# `_GfxSetRasterizerState`

entry_rva: `0x00000000015ea35a`
name: `_GfxSetRasterizerState`
instruction_count: 65
source_batch: `gfx-batch-00043.zip`
static_status: `success`
decomp_status: `success`
semantic_status: `partial`
migration_role: `replace-metal`

## ABI

The sealed decompilation offers a C signature. That signature is `inferred`. This dossier does not re-prove System V register assignment, except where a linked contract already did.

## Fields

Offsets named in Behavior are displacements on the object that function uses. A matching displacement in another function is not an alias. Decompiler field names are `inferred`.

## Dependencies

Non-logging calls, in first-seen order: `_glEnable`, `_glDisable`, `_glPolygonMode`, `_glEnable`, `_glDisable`, `_glCullFace`, `_glFrontFace`.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 65 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `_glEnable` at instruction `0x00000000015ea388`. Non-logging calls, in first-seen order: `_glEnable`, `_glDisable`, `_glPolygonMode`, `_glEnable`, `_glDisable`, `_glCullFace`, `_glFrontFace`.
- `inferred`: Enables or disables scissor, sets polygon mode, cull face, and front face. Byte +0x194 on the dereferenced handle forces the line polygon mode. _GfxInit clears that byte on the master allocation. _GfxForceWireframe writes the same displacement on the pointer it receives. Those two pointers are not proved to be the same object.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
