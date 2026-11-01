# `_GfxSetTextures`

entry_rva: `0x00000000015ecbf2`
name: `_GfxSetTextures`
instruction_count: 81
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

Non-logging calls, in first-seen order: `InternalSetTextureAndSamplerState`.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 81 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First call in the body is `InternalSetTextureAndSamplerState` at instruction `0x00000000015ecc6d`. Non-logging calls, in first-seen order: `InternalSetTextureAndSamplerState`.
- `inferred`: Walks the pointer array. Bit 0 of the flags binds each texture on a base unit. Bit 1 binds the same textures on units starting at +0xf. A null texture is replaced with the fallback at master+0x98. The bind itself is InternalSetTextureAndSamplerState, which is outside this cut. The dword at master+0xa8 is passed through as a comparison against 1.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
