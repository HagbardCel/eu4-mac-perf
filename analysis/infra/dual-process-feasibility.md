# Dual-process EU4 feasibility spike

**Status: demonstrated twice on 2026-11-14.** Two genuine instances of the pinned
EU4 build ran at the same time on one macOS host, one with the authentic OpenGL
renderer and one with the reconstructed Metal backend, and both presented
frames. This is the feasibility result PR 2 depends on; it is not yet a
synchronised differential campaign.

Machine evidence: [`dual-process-feasibility.json`](dual-process-feasibility.json).
Raw run directories are local-only under `.local/infra/spike/run-{7,8}`.

## Environment

| Item | Value |
| --- | --- |
| Host | macOS 27.0.1 (26A434), arm64 |
| Executable | `/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4` |
| Executable SHA-256 | `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` (pinned) |
| Build | EU4 v1.37.5.0 Inca (491d), GOG `59158198792514056` |
| Launch args | `-enabletelemetry -gdpr-compliant` (the installed GOG launcher args) |
| Reference | plain launch, no injection: an authentic OpenGL context |
| Candidate | `DYLD_INSERT_LIBRARIES=libeu4_metal_boot.dylib`, `EU4_METAL_BOOT_MODE=metal_init`, `SDL_VIDEO_EXTERNAL_CONTEXT=1` |

## Procedure

The spike used the existing launch controls rather than a new launcher:

1. Create one workflow ledger (`automation.launch_ledger`), pinned to the
   executable and output root, with a bounded budget and per-launch deadline.
2. Claim and start **two** ordinary ledger tokens, one per process.
3. Acquire the host-wide launch lease **once** (`automation.launch_lease`).
   Both children are spawned with the same inherited lease descriptor, so the
   kernel lock is held until both games exit. An unrelated launcher (another
   worktree or agent) is still excluded.
4. Spawn each game in its own process group (`start_new_session=True`) with
   separate stdout/stderr and artifact directories.
5. Observe liveness with `libproc` (the sandbox denies `/bin/ps`), on-screen
   windows with the existing `benchmark/eu4_window_list` helper, and the Metal
   `init.json`/`present.json`/`swap.json` receipts.
6. Terminate only the owned process groups (SIGTERM, then SIGKILL after a
   bounded grace), reap both, release the lease, and finish both ledger tokens.

The candidate needed the same configuration the M6 live path already uses:
`EU4_GFX_QUALIFIED_SOURCES` and `EU4_GFX_SHADER_CACHE` (the cache **JSON file**,
not its directory), plus `EU4_GFX_DRAW_SKIP_UNSUPPORTED=1`.

## Result

Two independent runs (`run-7`, `run-8`) produced the same result:

| Observation | run-7 | run-8 |
| --- | --- | --- |
| Both processes alive concurrently | yes | yes |
| Concurrent on-screen window interval | 20.07 s | 20.06 s |
| Reference window | 1728×1117, fullscreen layer, OpenGL | same |
| Candidate window | 1728×1145 outer, on-screen | same |
| Metal `init.json` success | yes | yes |
| Metal `present.json` (`_GfxPresent`) | yes | yes |
| Metal swap | game/drawable 3456×2234 | same |
| Candidate accepted draws | many (`draw-ok-*.json`) | many |
| Termination | SIGTERM both, exit 0 | SIGTERM both, exit 0 |

The candidate's 3456×2234 drawable at a 1728×1117 logical extent is the
expected 2× HiDPI backbuffer. The 28 px difference in the candidate's outer
window height is the windowed title bar; it is not a rendering-extent
difference. The reference was fullscreen and the candidate was windowed, so a
differential campaign must compare **logical render targets**, never window
rectangles or desktop screenshots.

## Constraints found (all actionable)

1. **Writable user data is mandatory.** Under the `workspace-write` DSH sandbox,
   both games aborted after ~2.5 s with an uncaught `CFileException` because
   `~/Documents/Paradox Interactive/Europa Universalis IV GOG` is not writable.
   Real launches need the normal user-data location (the session was approved
   for `danger-full-access`; subsequent sessions default to it). The harness
   must preflight this condition and report `sandbox_denied`, not a generic
   crash. This is why a gray screen or early exit alone is not a stall verdict.
2. **No single-instance restriction was observed.** Two SDL/Cocoa windows, one
   OpenGL context and one Metal layer, coexisted. Window focus/fullscreen is a
   presentation concern, not a launch blocker.
3. **Shared writable state remains a real risk.** Both instances used the same
   user-data directory, so their `logs/*.log` and settings are shared and their
   log files are overwritten. The spike proves concurrency, not isolation.
   Per-session user directories (`userdir`/`-userdir`) remain to be validated;
   until then the coordinator must treat shared-log evidence as ambiguous and
   rely on per-process artifact directories.
4. **The first unsupported/rejected draw is immediate and diagnostic.** Without
   the qualified shader sources the candidate stopped at `_GfxDraw` (caller
   `0xedfcd1`) after ~4 s with `reason: unsupported` and
   `draw-refused-edfcd1.json` (`current shader is not linked`). Without a valid
   shader-cache **file** it stopped at `_GfxInit` with `reason: shader-cache`
   and `stages: -1`. This is a concrete example for the PR 1 health gate: the
   failure is actionable in seconds, not minutes.
5. **Launch ownership is already separable.** Each process ran in its own
   process group and was terminated individually without touching the other.
   The lease excluded unrelated launchers while still allowing the owned pair.

## What this does not prove

- No frame correspondence, alignment, pixel comparison or lockstep was
  attempted here. Those are PR 2–3.
- No per-session user-dir isolation or concurrent-write reconciliation.
- No 100-frame run; the demonstrated settled interval is 20 s.
- No controlled renderer defect was injected in this spike (see the PR 4
  regression cases).

## Reproduction

The spike runner is intentionally local-only; the reusable successor is the
dual-process coordinator (`automation/gfx_dual_run.py`) that applies the same
lease/ledger/ownership rules. Until it lands, the recorded procedure above is
the exact method used for runs 7 and 8.
