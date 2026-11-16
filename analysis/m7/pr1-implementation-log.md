# M7 PR 1 implementation log

## Base verification

- `origin/main` was `6d7f9a6`. PR #198 (`fix/m7-diagnostic-correctness`) was
  open at branch time.
- Local `HEAD` was exactly PR #198's head `271cd04`. PR #198 has since merged
  to `main` as `b4c3955`; the PR was retargeted to `main` and no stack remains.
- No existing `frame_sealed`, `shadow_mode` or in-process replay API existed;
  only the M6 tracer and standalone replay core were present.

## Reconnaissance decisions

- Reused the six existing `OBS_ENTRY` sites (`_GfxDraw*`, `_GfxClear`,
  `_GfxPresent`, `PresentScene`) and the existing GL copy interposer. No
  duplicate detour was installed.
- Chose `PresentScene` entry as the authoritative readback point. Ghidra shows
  `CFrontEnd::Render` calls `EndScene` and then `PresentScene` at `0xf05148`;
  `PresentScene` calls `_GfxPresent` only when `this[0x4e8] != 0`, and
  `_GfxPresent` is only `_SDL_GL_SwapWindow`. The entry readback is therefore
  after the frame's draws and before the conditioned swap. The base M6 writer
  finalized at exit; PR 1 moved normal finalization to entry. Exit only
  publishes `scene_entry_missed` when no entry finalized the capture.
- Kept `gfx-frame-trace/v1` unchanged. Added a separate
  `gfx-frame-seal/v1` document rather than widening the historical manifest.
- Kept sealing in `eu4_gfx_frame_trace.c` instead of adding a translation unit:
  every existing native host and the injected dylib already link that file, so
  no build list changed and no new subsystem was introduced.

## Handoff design

- The descriptor is a fixed-layout struct (no pointers) with `struct_size` and
  `abi_version`; the callback receives a private copy valid for the call.
- The callback registry and dispatcher are independent of the trace writer's
  mutex. A TLS guard rejects same-thread re-entry; a condition variable
  serializes a concurrent thread. An atomic pending flag keeps the per-draw
  dispatch call to one acquire load when no frame is pending.
- The seal file is written atomically after `frame-trace.json`; the callback is
  published only after both are durable. Incomplete captures write
  `sealed=false` and are not published to the consumer.
- Added a bounded timeout and a total blob cap so a selected frame cannot grow
  without limit. Existing draw/event caps are reused.
- Added only one test-only branch (clear event without a GL context) so the
  native scenario host can exercise draw/clear/copy ordering. Production clear
  capture is unchanged.

## Live runs

1. `--max-draws` defaulted to 256; the menu frame has 325 events. The run
   correctly published `draw_cap` instead of notifying. Fixed the launch
   parameters, not the tracer.
2. The frame was complete, but `frame-seal.json` omitted the `complete` field,
   so the Python validator rejected the handoff. Added the field and made
   `seal_ns` nonzero.
3. An independent review found that normal finalization still happened at
   `PresentScene` exit (after a possible swap). Moved it to entry, added the
   `scene_entry_missed` incomplete path, and reordered the native scene
   scenarios to model draws-then-entry-then-exit.
4. Run 4 (pid 7100) established the entry boundary but used ABI-1 seals without
   an event-stream digest; it is historical evidence only.
5. Final run (pid 16791) sealed capture id 1 with 325 events, event stream
   `31d03c73…` / 6,392,559 bytes, and one verified notification, then terminated
   normally through the launcher.

## Known limits

- Logical immutability, not a read-only directory.
- `unsupported_dependencies` reports explicit capture-level failures, not a
  per-draw state audit (PR 2).
- A timeout is observed at the next engine hook; there is no independent timer
  thread inside the detour.
- No Metal execution and no second process.
