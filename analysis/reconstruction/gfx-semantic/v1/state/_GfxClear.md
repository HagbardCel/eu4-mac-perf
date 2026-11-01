# `_GfxClear`

entry_rva: `0x00000000015ea552`
name: `_GfxClear`
instruction_count: 96
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

Non-logging calls, in first-seen order: `_glDepthMask`, `_glStencilMask`, `_glScissor`, `_glEnable`, `_glColorMask`, `_glClearColor`, `_glClear`, `_glColorMask`, `_glDisable`, `_glDepthMask`, `_glStencilMask`.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 96 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `_glDepthMask` at instruction `0x00000000015ea5ad`. Non-logging calls, in first-seen order: `_glDepthMask`, `_glStencilMask`, `_glScissor`, `_glEnable`, `_glColorMask`, `_glClearColor`, `_glClear`, `_glColorMask`, `_glDisable`, `_glDepthMask`, `_glStencilMask`.
- `inferred`: Forces depth and stencil masks writable, optionally applies a scissor from the master viewport, sets a color mask and clear color from the argument, calls glClear with a mask built from the flags, then restores the color mask, depth mask, and stencil mask from the master.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
