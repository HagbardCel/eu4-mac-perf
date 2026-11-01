# `_GfxWriteTextureAsTGA`

entry_rva: `0x00000000015ed272`
name: `_GfxWriteTextureAsTGA`
instruction_count: 55
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

Non-logging calls, in first-seen order: `/usr/lib/libc++.1.dylib::operator.new[]`, `_glBindTexture`, `_glGetTexImage`, `GfxWriteBufferAsTGA`, `/usr/lib/libc++.1.dylib::operator.delete[]`.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 55 instructions, static `success`, decompile `success`, in `gfx-batch-00044.zip`. First call in the body is `/usr/lib/libc++.1.dylib::operator.new[]` at instruction `0x00000000015ed2af`. Non-logging calls, in first-seen order: `/usr/lib/libc++.1.dylib::operator.new[]`, `_glBindTexture`, `_glGetTexImage`, `GfxWriteBufferAsTGA`, `/usr/lib/libc++.1.dylib::operator.delete[]`.
- `inferred`: Allocates a CPU buffer, binds the texture, glGetTexImage into it, and writes a file. Returns 1 on the path that completes and 0 when the object is missing. The TGA header layout is not fully recovered.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
