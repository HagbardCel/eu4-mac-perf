# `_GfxDraw`

entry_rva: `0x00000000015ea789`
name: `_GfxDraw`
instruction_count: 106
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

Non-logging calls, in first-seen order: `indirect`, `SShaderOpenGL::EnableVertexAttribArrays`, `_glDrawArrays`.
Indirect call instructions in the body: 2. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 106 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `indirect` at instruction `0x00000000015ea806`. Non-logging calls, in first-seen order: `indirect`, `SShaderOpenGL::EnableVertexAttribArrays`, `_glDrawArrays`.
- `inferred`: Requires the shader at master+0x70. If the dirty byte at +0x128 is set, uploads up to six uniform vectors from the constant-buffer slots, then clears that byte. If +0x170 is set, enables vertex attribs from the buffers at +0x130 and clears that byte. Then glDrawArrays with the mode at master+8. A missing shader logs once and returns.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.

## Selected OpenGL submission evidence

- On the pinned x86-64 original, RVA `0x15ea944` tail-jumps to the
  `glDrawArrays` stub at RVA `0x18c2600`. The stub uses Darwin's lazy slot at
  unslid VA `0x101c4e840`. Static argument preparation loads mode from
  `master+0x08`, preserves entry `edx` as first, and chooses positive entry
  `esi` or `master+0x64` for count.
- The retained capture dylib registers an interposer for that import. The
  injected x86-64 fixture observes one invocation and one complete draw's
  worth of samples. This tests the dispatch form and forwarding in the fixture.
- Submission and target continuity in the original incomplete capture remain
  unverified. Its lossless pre/post images differ at all 482,112 pixels after
  normalization. [Capture evidence and investigation](../../../../../plans/repository-refactor/r3-capture-preparation.md#offline-submission-investigation)
- Two subsequent original runs observed exactly one `(5, 0, 4)` submission
  with matching contexts and recorded target fields. The selected entry
  check returned `GL_INVALID_ENUM` in both runs. The qualifying continuity
  witness remains incomplete, including after correction of two invalid
  resolve-observer queries. [Original run identities and limits](../../../../../plans/repository-refactor/r3-capture-preparation.md#authoritative-capture-with-submission-diagnostics)
