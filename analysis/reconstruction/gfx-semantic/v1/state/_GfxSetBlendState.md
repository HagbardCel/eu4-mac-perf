# `_GfxSetBlendState`

entry_rva: `0x00000000015ea0bf`
name: `_GfxSetBlendState`
instruction_count: 125
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

Non-logging calls, in first-seen order: `_glEnable`, `_glDisable`, `_glEnable`, `_glDisable`, `indirect`, `_glBlendFunc`, `indirect`, `_glAlphaFunc`, `_glColorMask`.
Indirect call instructions in the body: 3. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 125 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `_glEnable` at instruction `0x00000000015ea0ee`. Non-logging calls, in first-seen order: `_glEnable`, `_glDisable`, `_glEnable`, `_glDisable`, `indirect`, `_glBlendFunc`, `indirect`, `_glAlphaFunc`, `_glColorMask`.
- `inferred`: Compares the incoming description with bytes already on the master and skips GL calls that would not change those bytes. The calls that do run are blend enable, blend func or blend-func-separate, blend equation, alpha func, and color mask.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
