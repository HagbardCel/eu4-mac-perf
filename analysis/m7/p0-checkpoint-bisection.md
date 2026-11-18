# M7-P0 — targeted default-framebuffer checkpoint and causal bisection

Status: implementation, offline regressions and one authentic EU4 experiment
complete. This report is evidence, not a merge authorization.

## Base and head

- Base: `0a33c2278858dd3230839b10825cbc7275acc77e` (`origin/main`, the
  post-#209 merge).
- Implementation commit: `6f545dc6fc706a6e0bcf5aff00609fc47fa5fe55`
  (capture, graph/diagnosis integration and offline regressions).
- Clear-boundary ordering fix: `85d5c50bc5bf545005362d52dd2665d161f007fb`.
- Final head: the report commit that contains this file. Its exact SHA is
  recorded in the PR description; a commit cannot embed its own hash.

The working branch is `m7/p0-checkpoint-bisection`.

## What changed

The capture side now supports one opt-in, bounded, authoritatively indexed
default-framebuffer checkpoint:

- `--default-checkpoint-event EVENT` (capture CLI) sets
  `EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENT`.
- The tracer observes the default framebuffer immediately before the selected
  draw, clear or recorded copy executes: draw entry, clear entry, and the copy
  interposer before `glCopyTexSubImage2D`. Draw records are still emitted on
  exit; the checkpoint is captured at entry and attached to the same event.
- The readback reuses `read_drawable_bgra()` and therefore the existing state
  save/restore contract. No `glFinish()` and no error-queue drain are added.
- The blob is written through the existing content-addressed `put_blob()` path
  into `blobs/`; the selected event keeps its original index and the event
  count is unchanged. Metadata is an additive `default_checkpoint` field on
  that event with event, `fb:0`/target `0`, BGRA8/lower-left, dimensions,
  byte count, SHA-256, `timing=pre_event`, status and measured readback time.
- The image is rejected if the event is negative/out of range, missed,
  duplicated, unreadable, oversized (>256 MiB) or too slow (>10 s). A requested
  event with no pre-execution hook is reported as `default_checkpoint_missed`;
  a checkpoint captured at a boundary that finalizes before its selected record
  is emitted is `default_checkpoint_unattached`. No checkpoint is invented.
- When the option is absent no extra readback occurs and the manifest records
  `"default_checkpoint_event": null`.

The diagnostic side recognizes the captured record in the existing graph:

- `gfx_frame_graph.build()` validates the record, checks the blob's presence and
  size, and exposes `default_checkpoints`.
- `checkpoint_plan()` adds `(event, target="0", resource="fb:0")` to the same
  deterministic request; `checkpoint_request()` and `write_checkpoint_request()`
  are unchanged in shape.
- The Metal replay core already supports target `0`; the request is bound
  through the existing shader receipt and reconciled by
  `reconcile_checkpoints()`. The captured GL image remains reference evidence
  only and is never uploaded into Metal.
- `first_divergence()` now bisects a divergent `fb:0` draw/clear checkpoint:
  the lower bound is the last *same-resource* match, never an unrelated
  texture; an unbounded interval recommends the midpoint. Adjacent
  match/divergence recommends the operation after the matching observation.

Changed files:

| File | Purpose |
| --- | --- |
| [`benchmark/eu4_gfx_frame_trace.c`](../../benchmark/eu4_gfx_frame_trace.c) / [`eu4_gfx_frame_trace.h`](../../benchmark/eu4_gfx_frame_trace.h) | opt-in pre-event checkpoint capture, bounds, fail-closed hooks, test exports |
| [`automation/m6_frame.py`](../../automation/m6_frame.py) | CLI option, selection validation, env wiring, capture selection record |
| [`automation/gfx_frame_graph.py`](../../automation/gfx_frame_graph.py) | metadata validation, graph exposure, checkpoint plan/request |
| [`automation/gfx_independent_replay.py`](../../automation/gfx_independent_replay.py) | `fb:0` bisection/recommendation; no new comparator |
| [`tests/gfx_frame_trace_scenarios.c`](../../tests/gfx_frame_trace_scenarios.c) | offline native draw/clear/copy/missed/duplicate/out-of-range scenarios |
| [`tests/gfx_trace_state_host.m`](../../tests/gfx_trace_state_host.m) | real-GL checkpoint readback state-restoration and pixel check |
| [`tests/test_gfx_frame_trace_writer.py`](../../tests/test_gfx_frame_trace_writer.py), [`test_gfx_frame_graph.py`](../../tests/test_gfx_frame_graph.py), [`test_m7_diagnosis.py`](../../tests/test_m7_diagnosis.py), [`test_m6_frame.py`](../../tests/test_m6_frame.py), [`test_gfx_replay_case_sequence.py`](../../tests/test_gfx_replay_case_sequence.py) | focused regressions |

### Clear-boundary ordering fix

The previous hook order captured the checkpoint **before** the
`boundary_fb` handling in `eu4_gfx_frame_trace_clear_entry()`. That had two
consequences:

- An **opening** boundary clear armed the capture only after the checkpoint
  hook had already run, so a selected event 0 was missed.
- A **closing** boundary clear could capture a checkpoint for an operation that
  was never recorded. With the unattached guard this failed as
  `default_checkpoint_unattached`; without it, it could publish a complete
  frame whose event stream did not contain the requested checkpoint.

The hook now runs after the boundary block and before the clear event is
emitted or executes:

- opening clear: the boundary block arms the new frame, then the hook captures
  event 0 and the clear record carries the checkpoint;
- closing clear: the boundary block finalizes the armed frame, the hook sees
  the capture disarmed, and no readback is performed; a selected closing event
  remains `default_checkpoint_missed`, and the `default_checkpoint_unattached`
  guard is retained for any other unattached blob;
- normal clear, draw, copy and scene-boundary behavior are unchanged.

The test-only boundary model uses the default framebuffer without a GL context;
production still queries the real draw framebuffer. The authentic event-142
experiment used `--boundary scene` (`boundary_fb = -1`), so the boundary block
was skipped and the historical checkpoint data is unchanged.

## Authentic experiment

Exactly **one** EU4 process was started (pid 39241, session
`a3111e0d-9b76-4b23-910d-38bd48c9aa93`). No retry or second launch was made.
The command was the requested Phase C selection with the new checkpoint option:

```sh
python3 -m automation.m6_frame capture \
  --output .local/m7/p0-checkpoint-142 \
  --callers 0x103b3c8 --min-draws 1 --occurrence 300 --boundary scene \
  --continuous --shadow --seal --max-frames 1 --max-pending 1 \
  --max-draws 512 --max-bytes 2147483648 --case-max-bytes 2147483648 \
  --copy-source --default-checkpoint-event 142 \
  --background --duration 240 --timeout 600 \
  --shadow-timeout-ms 120000 --shadow-wait-ms 180000
```

The capture completed one sealed frame on the BetterDisplay `EU4 Test` display
(id 12, 1920×1200, non-main; window 1728×1117). The physical display was
unchanged, `settings.txt` was restored to its original SHA-256
`62b4902a…dd496`, and EU4 was terminated (`terminated=true`, `killed=false`).
No campaign launch lease was claimed because the command did not pass
`--campaign-run-root`; no lease was left behind.

| Field | Value |
| --- | --- |
| Session / pid | `a3111e0d-9b76-4b23-910d-38bd48c9aa93` / 39241 |
| capture_frame_id | 1 |
| present_index / scene_index | 617 / 617 |
| Boundary | scene |
| Operations | 325 (315 draws, 9 clears, 1 copy) |
| Dimensions | 1728 × 1117, BGRA8, lower-left |
| Seal | sealed=true, complete=true, trace_integrity=true, readback_ok=true |
| Manifest SHA-256 | `9cf099569d053962161105eb68a8d98742f4ca7d3d643feb0b153b59ba674702` |
| Event stream SHA-256 | `62cfdae407ecf38cbed0c98a956c5115203de12a85d46fd9b92e3d1c2dd9778c` |
| GL reference SHA-256 | `52322629c9d2e63e0f66c97a2ec2d79cf439377417f572fed688b3f6d7f5d1fb` |
| Frame payload | 428,764,075 bytes |

The run captured exactly one frame (present/scene 617); a one-frame experiment
has no predecessor/successor gap to validate. This is a new session/present
index; it is **not** compared image-for-image with the historical Phase C
frame.

## Checkpoint evidence

The selected checkpoint is a draw at event 142 (`_GfxDrawIndexed`, program 331,
caller `0x10ce96e`). The capture observes the default framebuffer before that
draw; the draw itself is still event 142 and the operation count remains 325.

| Field | GL reference (event 142) | Metal replay |
| --- | --- | --- |
| Resource / target | `fb:0` / `0` | `fb:0` / `0` |
| Dimensions | 1728 × 1117 | 1728 × 1117 |
| Format / origin | BGRA8 / lower-left | BGRA8 (declared lower-left) |
| File | `default-checkpoint-d77fa555…8160.bin` | `intermediate-event142-tex0.bgra` |
| Bytes | 7,720,704 | 7,720,704 |
| SHA-256 | `d77fa55573cf2b7b5d8a635e38f71cf3ef9227f6ba9d6bb5471ef93e1d6d8160` | `8ecc1072ffb69fe92dc2f50d6efb24cfa8514cffef8f342042bcbd06fa3ab13a` |
| Status | captured, pre-event | divergent, authoritative |

Pixel statistics for event 142:

```text
pixels              1,930,176
mismatched_pixels     871,620  (45.16%)
max_channel_error         103
mean_abs_error      [0.276850919, 0.229053206, 0.228078683, 0.0]
first_diff_pixel       {x: 360, y: 0, offset: 360}
differing_bounds       {x: 0, y: 0, width: 1728, height: 1117}
alpha_different       0
```

The existing event-284 copy-source checkpoint is still present and still
diverges: reference `copy-source-548.bgra`
(`63b6a3aee41f46acf73cf52182ca38e99294b07dfafe3408f3a016045f257768`), Metal
`b20add17…02bb5`, 1,234,383 / 1,930,176 mismatched (63.95%), max 104. Its
producer remains draw 283, `_GfxDraw`, program 346. The event-284 result did not
change; the new checkpoint only adds an earlier observation.

Checkpoint validation for the frame: 8 requested, 8 returned, 2 matched, 1
diagnostic, 5 divergent, 0 invalid, 0 problems; request bound through both the
shadow report and shader receipt. Execution: 325/325 events, 0 failures,
command status 4, GPU execution ok, provenance verified, operation coverage
complete. State coverage remains partial (`gl_dither`, `multisample`
unsupported).

## Causal interval

| | Before M7-P0 | After M7-P0 |
| --- | --- | --- |
| First observed divergent checkpoint | event 284, `fb:0` copy source | **event 142, `fb:0`** |
| Lower bound | unbounded | **unbounded** (only unrelated `tex:431/432` matches at events 11/13; no earlier `fb:0` match) |
| Unresolved interval | unbounded–284 | **unbounded–142** |
| Operational search range | 0–284 | **0–142** |
| Next checkpoint | 142 | **71** (`fb:0`, target `0`, requires capture, interval [0,142]) |
| Candidate producer | draw 283 / program 346 | **draw 141, `_GfxDrawIndexed`, program 331, caller `0x10ce96e`** (last `fb:0` producer before the divergent observation; not proven cause) |

This is **Case B**: the default framebuffer already differs before event 142,
so the lower bound was not manufactured from the matching texture checkpoints
and no earlier `fb:0` match is claimed. The interval narrowed from 284 to 142.
The next experiment is an `fb:0` checkpoint at approximately event 71
(`--default-checkpoint-event 71`) on the same selection, then repeat the
comparison. If event 71 matches, the interval becomes 71–142 and the next
midpoint is 106; if it differs, the interval becomes unbounded–71 and the next
midpoint is 35.

Diagnosis distinctions:

- first observed divergent checkpoint: event 142, `fb:0`, draw;
- first confirmed semantic divergence: none (binding audit is ok; no
  `kind=semantic` divergence);
- unresolved causal interval: unbounded–142;
- candidate producer operation: draw 141 (last same-resource producer), with
  the whole 0–141 range still unexcluded;
- established root cause: none. `gl_dither`/`multisample` remain unsupported
  and are not attributed. The difference is confirmed; the cause is not.

## Offline verification and replay

Focused and dependent suites (via `automation.test_runner`):

| Run | Modules | Result |
| --- | --- | --- |
| `20261118T093457Z-7e9eed2a` | `test_gfx_frame_trace_writer` (post-fix) | 48 tests, passed, 0.7 s |
| `20261118T093503Z-6f13babb` | `test_gfx_frame_graph`, `test_m6_frame`, `test_m7_diagnosis` (post-fix) | 169 tests, passed, 7.4 s |
| `20261118T084857Z-7d0a57c3` | `test_gfx_replay_case_sequence` | 55 tests, passed, 136.7 s |
| `20261118T085152Z-ad0b9dff` | `test_m7_continuous_replay`, `test_m7_continuous_supervisor`, `test_m7_shadow_receipt`, `test_gfx_gl_control`, `test_gfx_replay_case` | passed, 181.7 s |
| `20261118T090503Z-e17b7585` | `test_m7_canonical_diagnosis`, `test_m7_frame_seal`, `test_m7_frame_history` | passed, 10.2 s |
| `20261118T090709Z-03825f5f` | `test_m7_pixel_diagnostics` | 2 tests, passed |

The portable/replay rows predate the clear-entry ordering fix; that fix only
changes the `boundary_fb` clear path, which those suites do not exercise. The
post-fix native writer and the three directly affected modules were rerun.

The native boundary regressions are:

- opening boundary clear selected as event 0: complete sealed frame, clear
  record 0 carries the checkpoint;
- selected last recorded draw: complete sealed frame, checkpoint stays on the
  last event when the following boundary clear closes the frame;
- selected closing clear: explicit `default_checkpoint_missed`, no blob, no
  sealed frame. Before the ordering fix this was `default_checkpoint_unattached`
  with a blob for an unrecorded operation.

Portable CI gate: the `.github/workflows/profiler.yml` `portable` job runs on
the final PR head (Ubuntu, full portable `unittest discover` plus the repository
checks). Its status is reported in the PR checks; the native macOS-only tests
skip there as usual.

The native state host prints `trace_state=1 pending=0x500 after=0x500
reference=1 checkpoint=1`, proving the checkpoint readback preserves the FBO,
read buffer, pack state and pending GL error, and that the checkpoint pixels
match the known default back buffer. The native scenario host covers draw,
clear and copy indexing, missed, duplicate, out-of-range, disabled and
operation-count preservation.

Portable case verification:

```sh
python3 -m automation.agent_tools eu4_replay verify \
  --case .local/m7/p0-checkpoint-142/shadow/case
```

`ok=true`, `problems=[]`, 1,247 files, graph `complete`,
`independently_reproducible=true`. The case inventory includes the new
checkpoint blob and the checkpoint request.

Offline re-execution with no EU4 launch:

```sh
python3 -m automation.agent_tools eu4_replay case \
  --case .local/m7/p0-checkpoint-142/shadow/case \
  --replay-out .local/m7/p0-checkpoint-142/case-replay
```

The replay reproduces the **same** checkpoint statistics (142: 871,620
mismatched / max 103; 284: 1,234,383 / max 104) and the same
unbounded–142 / next-71 diagnosis. Its qualification axis is
`frame_shader_identity=unverified_session`, because a standalone single-frame
case replay has no in-process shadow identity report; it is classified
`incomplete_diagnostics`, not a qualified match. This is a known limitation of
the standalone case path, not a silent match: the authentic in-process
diagnosis has `frame_shader_identity=verified`, and the missing identity is
reported rather than assumed.

## Overhead and bytes

- One additional full-framebuffer readback: **5.966 ms** (recorded in the event
  metadata as `readback_ns`), versus the final-reference readback of 23.981 ms.
- Additional capture blob: **7,720,704 bytes**, 1.80% of the 428,764,075-byte
  frame payload.
- The Metal checkpoint output is a separate 7,720,704-byte file in the replay
  directory (`intermediate-event142-tex0.bgra`).
- With the option absent there is no extra readback and no metadata/blob.

## Reproduction and artifacts

Authentic payload is under `.local/m7/p0-checkpoint-142` (gitignored):

- `frames/frame-000001/` — sealed GL frame, event stream, checkpoint blob;
- `shadow/frame-000001/` — in-process Metal replay, checkpoint request,
  `agent-diagnosis.json`, `diagnostic-report.json`;
- `shadow/case/` — portable case (1,247 files) with the checkpoint request and
  blob;
- `case-replay/` — offline re-execution;
- `capture-report.json`, `launcher-report.json`, `verify.json`.

Committed evidence:

- [`p0-checkpoint-bisection.json`](p0-checkpoint-bisection.json) — compact
  machine-readable values;
- [`p0-checkpoint-bisection.agent-diagnosis.json`](p0-checkpoint-bisection.agent-diagnosis.json)
  — the bounded `gfx-agent-diagnosis/v1` projection (6.3 KB);
- this report.

## Remaining uncertainty and limits

- `gl_dither` and `multisample` remain unsupported; renderer equivalence is not
  claimed and the difference is not attributed to either.
- No full-frame GL/GL replay; the same-backend control remains transfer-scoped.
- Root cause is not established. The new lower bound is still unbounded; event
  71 is the next bisection, not a conclusion.
- One authentic launch only; the new session/present index (617) is not the
  historical 615 and images are not compared across launches.
- The standalone single-frame portable replay reports
  `unverified_session` by design; the in-process authentic evidence is
  `verified`.
- The broad interactive full suite was not run, per the M7-P0 instruction not
  to launch the interactive EU4 test set. Focused and dependent offline suites,
  native hosts, the portable CI workflow and portable verification were run
  instead.
- Native boundary-clear coverage is single-frame. The authentic continuous run
  uses the scene boundary, and the existing continuous tests cover that path
  unchanged; continuous `boundary_fb` rearm is not exercised by the native
  host.

## Next action

Capture `fb:0` immediately before event 71 on the same Phase C selection,
replay and compare it through the same request/receipt/reconcile path, and
narrow to 71–142 (match) or unbounded–71 (divergence). Do not merge this PR or
enable auto-merge; a fresh independent review of the exact final head is
required.
