# `_GfxDraw2dLines`

entry_rva: `0x00000000015ead87`
name: `_GfxDraw2dLines`
instruction_count: 71
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

Non-logging calls, in first-seen order: `indirect`, `_glMatrixMode`, `_glLoadIdentity`, `_glOrtho`, `_glMatrixMode`, `_glLoadIdentity`, `_glLineWidth`, `_glBegin`, `_glColor4f`, `_glVertex2f`, `_glEnd`.
Indirect call instructions in the body: 1. An indirect site is not a resolved callee.

## Uncertainties

- Replacing this function also means replacing the GL or SDL-GL effect it performs. A CPU-side store that feeds that effect is not optional.
- Runtime execution of every branch is `unknown` unless a record is named below.

## Behavior

- `static-verified`: 71 instructions, static `success`, decompile `success`, in `gfx-batch-00043.zip`. First call in the body is `indirect` at instruction `0x00000000015eadb3`. Non-logging calls, in first-seen order: `indirect`, `_glMatrixMode`, `_glLoadIdentity`, `_glOrtho`, `_glMatrixMode`, `_glLoadIdentity`, `_glLineWidth`, `_glBegin`, `_glColor4f`, `_glVertex2f`, `_glEnd`.
- `inferred`: Unbinds the shader program, loads an orthographic projection from the master viewport, and draws line pairs with immediate-mode glBegin(1), glColor4f, and glVertex2f. Restores the previous program object afterward.
- `unknown`: Which callers depend on the return register, and which branches run in a real frame, are not established by the static body.
