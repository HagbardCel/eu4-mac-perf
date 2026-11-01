# `_GfxSetDepthStencilState`

entry_rva: `0x00000000015ea261`
name: `_GfxSetDepthStencilState`
instruction_count: 80
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

Non-logging calls, in first-seen order: `_glDepthMask`, `_glEnable`, `_glDisable`, `_glDepthFunc`, `_glEnable`, `_glDisable`, `_glStencilMask`, `_glStencilFunc`, `_glStencilOp`.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 80 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `_glDepthMask` at instruction `0x00000000015ea28e`. Non-logging calls, in first-seen order: `_glDepthMask`, `_glEnable`, `_glDisable`, `_glDepthFunc`, `_glEnable`, `_glDisable`, `_glStencilMask`, `_glStencilFunc`, `_glStencilOp`.
- `inferred`: Writes depth mask, enables or disables the depth test, sets the depth func, and does the same for stencil mask, func, and op. Unchanged fields are skipped. The GL enum translation lives in the state object, not in this function's immediates alone.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
