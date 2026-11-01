# `_GfxGetAdapterModeCount`

entry_rva: `0x00000000015ed0f0`
name: `_GfxGetAdapterModeCount`
instruction_count: 11
source_batch: `gfx-batch-00044.zip`
static_status: `success`
decomp_status: `success`
semantic_status: `reconstructed`
migration_role: `retain-original`

## ABI

The sealed decompilation offers a C signature. That signature is `inferred`. This dossier does not re-prove System V register assignment, except where a linked contract already did.

## Fields

Offsets named in Behavior are displacements on the object that function uses. A matching displacement in another function is not an alias. Decompiler field names are `inferred`.

## Dependencies

Non-logging calls, in first-seen order: `_SDL_GetWindowDisplayIndex`, `_SDL_GetNumDisplayModes`.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- `retain-original` stays valid only while this function's data layout and calling contract stay valid.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 11 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First call in the body is `_SDL_GetWindowDisplayIndex` at instruction `0x00000000015ed0fb`. Non-logging calls, in first-seen order: `_SDL_GetWindowDisplayIndex`, `_SDL_GetNumDisplayModes`.
- `inferred`: Uses the SDL window at +0x178 to ask SDL for a display index and a mode count. A negative SDL count becomes 0.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
