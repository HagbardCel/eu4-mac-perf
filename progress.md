# Progress

## Objective

Reconstruct and implement `_GfxUpdateVertexBufferMultiple` in the existing Metal vertex-buffer
backend, after the external checker captures the research note
`analysis/automation/pilot-gfx-update-vertex-buffer-multiple/note.md`.

## Findings

- `research resolve` gives exact entry RVA `0x15ebe5f`; the body is `0x15ebe5f`-`0x15ebf40`
  inclusive (`RET` at `0x15ebf40`), 70 instructions.
- The patchable prefix is `55 48 89 e5 41 57 41 56` (eight original bytes ending after
  `PUSH R14` at `0x15ebe65`).
- SysV arguments: `RDI` master-pointer handle, `RSI` engine header, `RDX` qword data-pointer
  array, `RCX` dword element-count array, `R8D` range count. `R9` and a stack dword passed by
  the caller are unread.
- It binds `GL_ARRAY_BUFFER` once at `0x15ebe9e`, then calls `glBufferSubDataARB` per range at
  running byte offsets starting at 0 (`0x15ebeb0`-`0x15ebedc`). It does not replace the
  buffer.
- It repeats the shader attribute-disable loop (`0x15ebf12`-`0x15ebf28`) and writes
  `[master+0x170] = 1` at `0x15ebf2a`.
- A zero `[header+8]` returns before any effect (`0x15ebe76`-`0x15ebe78`).

## Implementation

- `benchmark/eu4_gfx_buffers.h`: declared `eu4_gfx_update_vertex_buffer_multiple`.
- `benchmark/eu4_gfx_buffers.mm`: replacement copies each range into the existing shared
  `MTLBuffer` at running offsets from zero; validates all ranges before copying; records
  disabled attributes through the existing `g_disabled_attribs` deferred state; sets
  `master+0x170`; never replaces or resizes the buffer.
- `benchmark/eu4_gfx_entries.def`: registered
  `GFX_ENTRY(_GfxUpdateVertexBufferMultiple, 0x15ebe5f, 0x15ebf41, ..., "\x55\x48\x89\xe5\x41\x57\x41\x56")`.
  The registry name is the engine symbol, matching the index-buffer entries; the checker rejects the
  short `update_vertex_buffer_multiple` name as a missing registration.
- `tests/test_gfx_entry_registry.py`: added the entry baseline tuple and `_MULTI` prefix.
- `tests/gfx_buffers_host.mm`: added real-Metal `case:multiple-range`,
  `case:multiple-unchanged`, `case:multiple-lifecycle`, and `case:multiple-error` child modes
  for every stop reason.

## Test results

- `python3 -m unittest tests.test_gfx_buffers tests.test_gfx_entry_registry`: 24 tests pass.
  `tests.test_index_buffer_campaign` also passes.
- Child modes exercise `missing`, `range`, `size`, `bounds`, `null_data`, `released`, `attrib`,
  and `thread`, and verify the buffer and `master+0x170` are unchanged on failure.
- No EU4 launch, corpus rebuild, or reconstruction-directory read was performed.

## Next

- Implementation is complete and green. Nothing committed, reset, or pushed.
