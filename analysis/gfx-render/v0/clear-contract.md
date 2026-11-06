# Offscreen `_GfxClear` contract (PR4)

Bounded Metal support for a color-only full-attachment clear through the existing production backend and `metal-shadow/v0` worker. Not complete `_GfxClear` equivalence, not a draw, not a live EU4 frame.

## Acceptance claim

For the supported state below, the Metal backend submits a real GPU clear, the worker observes successful completion, and validated BGRA8 readback agrees with predetermined pixels and an independent OpenGL reference. Unsupported states fail closed.

## Supported subset

| Gate | Requirement |
| --- | --- |
| Flags | Packed dword: bit0 color, bit1 depth, bit2 stencil. Only `flags == 1` (color). |
| Attachment | Explicit offscreen `BGRA8Unorm` color target; no MSAA, sRGB, depth, or stencil attachment. |
| Scissor | No pending box (`scissorValid == false`). |
| Viewport | Master image equals the full attachment: x at `+0xbc`, y at `+0xc0`, width at `+0xb0`, height at `+0xac` → `(0, 0, w, h)`. |
| Color mask | Master blend snapshot mask at `+0x2c` is `0x01010101` (four channels writable, as `fill_blend_snap`). |
| Color | `NULL` → `(0, 0, 0, 1)` float32. Otherwise four finite float32 values in `[0, 1]`. |
| Pass | One new pass: `MTLLoadActionClear` + `MTLStoreActionStore`. No draws in the pass. |
| Master | No writes into the `EU4_GFX_MASTER_SIZE` engine object. |

Original `_GfxClear` also forces depth/stencil masks, applies a viewport-derived scissor, overrides then restores the color mask, and builds a `glClear` bitfield. PR4 accepts only the full-attachment color-clear *outcome* under the gates above. It does not reconstruct temporary GL mask overrides or claim Metal-visible restore side effects.

## Pending clear (PR4 limitation)

At most one outstanding clear per backend. `clear_checked` while a command is pending returns unsupported and submits nothing. After `complete_offscreen` resolves that submission, another clear is allowed. This is not a general command scheduler.

## Sync and validity

- `clear_checked` encodes, commits, invalidates `contentsKnown` immediately, stores the pending command buffer, and returns without waiting. `ok` means submitted, not GPU-completed. Submission identity is that single `pendingClear` pointer; a second clear or configure while pending is rejected.
- Only `complete_offscreen` waits, checks terminal status and error, and may set `contentsKnown` for the pending buffer.
- `configure_offscreen` rejects while a clear is pending. Replacement textures are allocated before publishing; a failed allocate leaves the prior target intact.
- Rejected preflight does not invalidate prior known contents.

## Shutdown

`eu4_gfx_shutdown` drains a pending offscreen submission (wait + terminal validation) before releasing the backend. GPU failure during drain is terminal. Synchronization occurs only at shutdown and in the diagnostic complete helper, not on the normal production clear path.

## Production entry

`void eu4_gfx_clear` keeps the reconstructed ABI. With game-window mode disabled it calls `clear_checked` (diagnostic offscreen) and stops on non-ok. With game-window mode enabled and master `+0x78` null it clears the game backbuffer instead; see [default-target-contract.md](default-target-contract.md). Live launchers that never enable game mode and never configure an offscreen target therefore stop at the first clear. That is intentional for PR4 and is not live-startup progress.

## Worker evidence

Successful `clear` reports submitted, completed command-buffer status, and `contents_known: true` after `complete_offscreen`. `read_offscreen` exports bytes only when contents are known. Committed GPU failure uses `gpu_failed` with `executed: true` and terminates. Preflight rejection stays recoverable.

## Non-claims

- **GPU failure classification.** The `gpu_failed` terminal path is implemented but not fault-injection-tested, as documented.
- **Original-engine equivalence.** The independent GL reference demonstrates the bounded color-clear behavior, not equivalence of EU4's entire `_GfxClear` implementation. A uniform full-attachment clear also does not prove row orientation.
- **Live rendering.** No actual EU4 draw or frame is demonstrated.
