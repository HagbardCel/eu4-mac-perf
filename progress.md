# L3.2b-I — Effective input state at `_GfxDraw` caller `0xedfcd1`

## Objective

Reconstruct the effective input state consumed by the authentic EU4 loading-screen
`_GfxDraw` at caller RVA `0xedfcd1` and produce a fresh capture-based packet that
establishes:

- `vertex_buffer_bindings`
- `vertex_buffer_contents`
- `matrix_constant`

The packet stays `packet_ready=false`; this milestone does not implement Metal.
The frozen L3.2a oracle (`analysis/gfx-draw/v0/probe/`) is read-only.

## Findings

- Static disassembly of `_GfxDraw` (`0x15ea7ed`–`0x15ea806`) shows the constant
  slot-0 upload: `rax = master[0xd0 + slot*16]`, `esi = master[0xc8 + slot*16]`,
  `rdx = *(rax + 8)`, then `___glewUniform4fvARB`. Master slot 0 count `4` means
  64 bytes are uploaded.
- `_GfxSetConstantBuffer` (`0x15ec36a`) body verified with `otool`:
  - `movq (%rdi), %rax` loads the master from the handle.
  - `movl %ecx, %ecx` zero-extends the slot.
  - `R9D == -1` reads the byte length from `(%rsi)`; otherwise `R9D` is the
    byte length. Words = `(length + 3) >> 2`, stored at `master+0xc8+slot*16`.
  - `movq %rsi, 0xd0(%rax,%rcx)` stores the buffer header; `0x128` is set dirty.
  - Safe 9-byte entry prefix `55 48 89 e5 48 8b 07 89 c9` ends exactly before
    `cmpl $-0x1, %r9d`.
- Vertex-buffer header layout: `+0` stride, `+4` count, `+8` GL name. Draw master
  vertex slot 0: header at `master+0x130`, offset `+0x150`, stride `+0x160`.
- Checker acceptance: bindings come from the draw master snapshot plus a create
  record's `resource_id`; contents must be exactly the independently replayed
  lifecycle buffer (`method: "resource_lifecycle"`); the matrix is validated
  against the draw master's 64-byte slot 0 plus its sidecar.
- The draw record payload must stay exactly `0x171` bytes, so the matrix is not
  appended to it.
- Optional resource payloads use `eu4_gfx_observe_try_payload()` so an arena gap
  degrades to `payload_gaps` instead of `unavailable`.
- All observer edits are finalized before the capture; `library_sha256` must
  match the current build.

## Changes in this worktree

- `benchmark/eu4_gfx_observe*.c/.h`, `eu4_gfx_observe_entries.def`: added
  `_GfxSetConstantBuffer` observation (`cb_N`, slot/selector/header/data/vec4
  meta), last slot-0 constant bytes, draw-time 64-byte memcmp cross-check, and
  `retain=1` for create/update/bind/slot-0 constant records.
- `benchmark/eu4_gfx_op_capture.py`: exposes the new resource/draw meta keys.
- `benchmark/eu4_gfx_draw_packet.py`: builds lifecycle-backed bindings and
  contents, draw-time-matched matrix, and writes/verifies sidecars
  `payloads/<resource_id>.bin` and `payloads/matrix.bin`.
- `tests/test_gfx_draw_packet.py`: covers the established inputs and sidecars.

## Blockers

None.

## Test results

- `python3 -m unittest tests.test_gfx_draw_packet` — 22 tests, OK.
- `python3 -m unittest tests.test_gfx_observe` — 43 tests, OK.
- `build_library()` and `build_host()` compile successfully.
- Campaign capture `captures/l32b-i-effective-state` (1/3 launches):
  `status=complete`, 8 events, 1 qualifying draw (call 11, mode 5, count 4).
  - `vb_3` (call 5): header `0x7fce16f742e0`, count 4, stride 24, 96 bytes.
  - `cb_1` (call 8): slot 0, selector -1, header `0x7fce16f0beb0`, vec4 4,
    64 bytes.
  - Draw meta: `constant_slot0_match=true`, `constant_slot0_call_id=8`;
    master slot 0 vertex header equals `vb_3`; master constant pointer equals
    `cb_1` header.
- Packet `analysis/gfx-draw/v0/l32b/effective-state/packet`:
  `status=incomplete`, `packet_ready=false`, the three required inputs
  established, other required inputs still missing.
- Independent checker `campaigns/eu4-l32b-effective-state/accept.py`:
  `{"verdict": "pass", "established": ["vertex_buffer_bindings",
  "vertex_buffer_contents", "matrix_constant"], "captures_used": 1}`.

## Next

Hand the worktree to independent PR review. Optional follow-ups are the
remaining non-required inputs (shader, textures, sampler, render state, scissor,
attribute layout); this milestone deliberately leaves them missing and keeps
`packet_ready=false`.
