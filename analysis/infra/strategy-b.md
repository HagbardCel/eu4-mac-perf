# Strategy B — independent Metal replay of a captured EU4 frame

Date: 2026-11-14. Worktree: `wt_infra`, branch `infrastructure`, based on the
reviewed integration head `485bb92955ad10231726ba16ba3466dd73836bf4`.
Follow-up commits: `eb814a6` (background display and capture hardening),
`fd4f175` (independent Metal intermediates), `beca74b` (agent tools),
`5d8bb6e` (portable case verification and evidence).

This is the acceptance record for the capture-once/replay-independently
workflow. It is deliberately explicit about what is qualified and what is not.

## 1. What was built

| Layer | Implementation |
| --- | --- |
| Background display | `automation/gfx_display.py`, `benchmark/eu4_display_probe.m`, `benchmark/eu4_display_isolate.m` (opt-in guard); integrated with `gfx_launch`, `gfx_session` and `gfx_dual_run` |
| Capture integrity | strict payload/digest/extent validation before comparison; invalid evidence is preserved but never compared |
| Request capacity | Python refuses >256-frame requests; the native observer rejects an oversized request instead of truncating |
| Effective launch spec | `--continuelastsave` is part of the immutable effective argv and its digest |
| Startup health | GPU/API health is polled after readiness, not only in the comparison loop |
| Correspondence | image correlation is diagnostic only; identity and workload establish correspondence |
| Dependency contract | `automation/gfx_frame_graph.py` classifies external inputs, renderer-generated outputs and unproven content |
| Independent replay | `automation/gfx_independent_replay.py` drives the existing M6 Metal replay host, enforces provenance and compares checkpoints |
| Portable case | `automation/gfx_replay_case.py` packages capture, shaders, graph, checkpoints, replay, first divergence and a relative replay command |
| Agent surface | `eu4_capture graph`, `eu4_replay independent`, Strategy B `eu4_replay case` / `eu4_case_inspect`, DSH bridge/schema |

The replay host was corrected in three substantive ways:

1. **Numeric GL texture identities were dropped.** `string_value()` returned
   `nil` for JSON numbers, so `g_render_targets` was never populated. Every one
   of the 2,759 texture bindings used captured OpenGL contents. After the
   `texture_key()` fix, 274 bindings use Metal render targets and 2,485 use
   external inputs. This is the concrete mechanism by which the previous replay
   could match final pixels while Metal's intermediate rendering remained
   unvalidated.
2. **Render-target sampling lacked a V flip.** A Metal render target stores
   GL row 0 at the opposite end of the texture from a GL-uploaded asset.
   Sampling renderer-generated targets now goes through a cached
   `orient` compute pass (V flip; RGB targets also get alpha 1.0).
3. **Replay events are serialized.** A sampling transform owns its own command
   buffer, so prior events must be complete before it reads a target. One event
   per submission also makes checkpoint readback exact.

## 2. Selected capture

Canonical frame: `.local/m6/frames/menu-occ300/frame` (outside this worktree;
content copied into the portable case).

- 326 events: 317 draws, 9 clears, 0 captured resolve/copy events.
- Reference: 1728x1117 BGRA8, lower-left, SHA-256
  `ff2f4e2b9d2cfeef8509dc6777213c859a7ef6af7bc6ad37c4ca564ce57deb15`.
- 68 logical resources: 60 external inputs, 7 renderer-generated, 1 unproven.
- Five renderer-generated resources are sampled downstream: textures 431, 432,
  433, 443 and 447.
- The only frame seed is the first draw's pre-image on texture 433; an in-frame
  clear at event 0 supersedes it.

### Coverage report

```json
{
  "status": "partial",
  "counts": {
    "events": 326, "draws": 317, "clears": 9, "resolves": 0,
    "resources": 68, "external_inputs": 60,
    "renderer_generated": 7, "renderer_generated_sampled": 5,
    "unproven": 1
  },
  "unproven": ["tex:442"],
  "reasons": [
    "tex:442 is sampled but its content version is unproven: sampled full-extent texture has no captured producer; a resolve/copy or earlier pass is missing from the capture",
    "the capture records draws and clears only; a sampled full-extent texture without a producer indicates an uncaptured resolve/copy operation (for example _GfxResolve)"
  ]
}
```

Texture 442 is 1728x1117 and is sampled by the bloom pass (events 287 and 291)
but is never written by a captured draw, clear or copy. The engine has
`_GfxResolve`, and the frame trace has a resolve entry point, but the canonical
capture records no resolve/copy operations. The frame is therefore **partial**
by contract even though the replay's final pixels match. This is the correct
fail-closed result: complete-frame certification requires the missing producer
to be captured.

## 3. Independent replay result

Command (from the repository root):

```sh
python3.14 -m automation.gfx_independent_replay \
  --frame .local/infra/strategy-b/moved/case/capture \
  --shaders .local/infra/strategy-b/moved/case/shaders \
  --out .local/infra/strategy-b/cli-check
```

| Property | Result |
| --- | --- |
| Events encoded | 326/326, 0 failed |
| Metal command status | completed (4) |
| Final readback | present, 1728x1117 |
| Final mismatch | **0 / 1,930,176** pixels, max error 0 |
| Final SHA-256 | `ff2f4e2b…` (equal to the OpenGL reference) |
| Metal render-target bindings | 274 |
| External captured-input bindings | 2,485 |
| Captured intermediate seeded into Metal | **0** |
| First sample before any write | **0** |
| Checkpoints 431, 432, 447, 443 | exact (0 differing pixels) |
| Checkpoint 433 | diagnostic only: GL RGB8 vs Metal BGRA8 readback, 573,377 pixels differ after channel normalization, max 114; not authoritative |
| Determinism | two consecutive replays produced identical final and checkpoint hashes; 4.12 s each |

The final image is produced after Metal independently renders textures 431,
432, 433, 443 and 447 from the captured external inputs. Texture 442 remains an
unproven external input for the bloom pass; that is the coverage boundary.

## 4. First-divergence experiment

Controlled fault: disable the render-target V flip.

| Field | Value |
| --- | --- |
| Earliest authoritative divergent checkpoint | event **291**, target **443** |
| Producer | event **289**, `_GfxDraw`, caller `0xed7c76`, program 349 |
| Divergence | 481,860 / 482,112 pixels, max 155 |
| Upstream checkpoint 447 | exact (proves the divergence is the consuming pass, not its input) |
| Final output | 53,449 / 1,930,176 pixels differ, max 17 |

This reproduces the earlier live "first divergence" class (render-target
orientation) offline and localizes it to one draw. The correct replay is
restored by removing the fault; all authoritative checkpoints and the final
output then match again.

## 5. Historical sampler-binding defect

Injection: for `texN` with `N >= 7`, select the engine texture captured at unit
`N-1`; when that unit has no payload, record the missing unit (the shader would
sample the default texture). The replay host and shader slots are unchanged.

| Property | Correct replay | Defective replay |
| --- | --- | --- |
| Binding audit | 0 mismatches | **842** mismatches |
| Earliest state divergence | none | event **14** |
| `tex7` | unit 7 -> texture 429 | unit 6 -> **missing** (default) |
| Other fields | — | `tex8` unit 7 -> texture 429 instead of 2; `tex9` -> 2 instead of 387; `tex10` -> 387 instead of 389; `tex11` -> 389 instead of 434; ... |

The title frame's terrain draws are overdrawn by the menu layer, so this
deliberate state defect does **not** change the final pixels. It is detected by
the explicit binding audit, which is the correct outcome for a defect that
global pixel similarity conceals. The mirrored UI-label regression remains in
the existing M6 case suite.

## 6. Portable case

`.local/infra/strategy-b/moved/case` contains `capture/`, `shaders/`,
`replay/`, `report.json`, `graph.json`, `case.json` and `README.md`. All paths
in `case.json` are relative and every file has a SHA-256. It was moved to a new
directory and verified; replay from the moved case reproduced the golden final
output. The replay command recorded in the manifest is:

```sh
python3.14 -m automation.gfx_replay_case replay --case . --out replay-rerun
```

## 7. Background display (Phase 0)

- BetterDisplay virtual screen `EU4 Test` exists once and is reused.
- Qualified geometry: 1920x1200, 60 Hz, HiDPI off, non-main, origin
  `(1728, 0)`; physical built-in display remains main at 1728x1117 points /
  3456x2234 pixels and its modes were not changed.
- One real original-OpenGL launch was executed directly into background mode:
  - observer installed, **820 frame observations** before termination;
  - the game's `settings.txt` was switched to windowed for the session and
    restored byte-for-byte afterwards;
  - process terminated cleanly by the owned-session path (SIGKILL after the
    harness stop); no unrelated process was signalled;
  - primary display configuration unchanged.
- **Acceptance not met.** The window was reported at `[0, 32, 1728, 1145]` on
  the physical display, not on the virtual screen, and EU4 was frontmost for 68
  of 71 verification samples. The first launch therefore demonstrates valid
  background captures, clean termination and correct restore, but **not**
  focus/placement isolation.
- The opt-in `eu4_display_isolate` AppKit guard was written to block
  activation and place the window before ordering it on screen. It is **not
  qualified**: in guarded launches the window was not visible to the window
  server and the game still became frontmost. It is therefore disabled by
  default (`focus_guard=False`) and background mode must not be claimed as
  default until a minimal AppKit qualification shows both placement and no
  activation.
- Five consecutive launches were not performed because the host lease was
  concurrently held by the parallel M6 worktree, and the isolation mechanism
  is not yet qualified. Foreground remains the safe fallback.

## 8. Tests

- Python 3.14: 208 relevant infrastructure tests pass, including 18 display,
  14 dependency/replay-contract and the extended Phase 1 regressions.
- Native macOS: frame observer host tests pass, including the new oversized
  request rejection. The replay host builds with `-Wall -Wextra`.
- DSH plugin: 6/6 Node tests pass; schemas and argument mapping cover the new
  graph/independent/fault operations.
- Full Python discovery: 2,814 tests, 11 failures. All 11 reproduce at the
  reviewed base `485bb92` in a detached worktree (Metal init capability span,
  shadow-worker texture-LOD state, live-capture native canonical bytes and
  process/worker host tests), so they are pre-existing environment/build-cache
  failures rather than Strategy B regressions. The 221 tests covering the
  touched areas pass.
- No test launches EU4.

## 9. Remaining limitations

1. The canonical capture is partial: `_GfxResolve`/copy capture is not yet part
   of the frame trace, so texture 442 has no producer. Implement resolve/copy
   capture and re-capture the frame before claiming a complete frame.
2. The live Metal-enabled EU4 qualification was not re-run in this session
   (the M6 worktree held the launch lease). Offline replay qualifies the
   renderer workload, not all live integration behavior.
3. Background display focus/placement isolation is unqualified; keep the
   foreground fallback.
4. Checkpoint 433 is diagnostic because GL RGB8 and Metal BGRA8 readbacks have
   different channel/alpha conventions and the checkpoint comparison does not
   yet declare a quantization policy.
5. The sampler-defect experiment demonstrates state divergence, but the title
   frame masks its pixels; a map/terrain frame is needed for pixel-level
   detection of that historical defect.

## 10. Efficiency measurements

See [`strategy-b-metrics.json`](strategy-b-metrics.json). Four authentic EU4
launches were attempted for display qualification (none qualified for focus
isolation); no new GPU capture was taken. One authentic frame was replayed
offline eight times; the qualified replay takes 4.12 s versus roughly 340 s to
reach the title scene live, and the five renderer/provenance changes were
validated without relaunching EU4.

## 11. Merge recommendation

The correctness fixes, dependency contract, independent replay, provenance
enforcement, first-divergence experiment and portable case are mergeable as
focused follow-up commits. The frame should be labeled **partial** until
resolve/copy capture is added. Background mode should remain opt-in and
unqualified; the existing foreground path and the dual-process runner are
unchanged in their qualification claims.
