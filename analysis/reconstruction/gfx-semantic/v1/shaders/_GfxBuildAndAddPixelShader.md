# `_GfxBuildAndAddPixelShader`

entry_rva: `0x00000000015eb7ae`
name: `_GfxBuildAndAddPixelShader`
instruction_count: 58
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

Non-logging calls, in first-seen order: `BuildGLSL`, `CPdxLogFileAndLine::CPdxLogFileAndLine`, `CPdxLogFileAndLine::operator()`, `_GfxAddPixelShader`, `/usr/lib/libc++.1.dylib::operator.delete`.
Indirect call instructions in the body: 0. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 58 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `CString::CString` at instruction `0x00000000015eb7d6`. Non-logging calls, in first-seen order: `BuildGLSL`, `CPdxLogFileAndLine::CPdxLogFileAndLine`, `CPdxLogFileAndLine::operator()`, `_GfxAddPixelShader`, `/usr/lib/libc++.1.dylib::operator.delete`.
- `inferred`: Calls BuildGLSL for a pixel shader, then _GfxAddPixelShader on success.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
