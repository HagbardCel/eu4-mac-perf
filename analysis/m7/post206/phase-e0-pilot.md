# Phase E0 — bounded autonomous pilot

Status: pilot complete on frozen authentic evidence. No production migration
was started. No exploratory correction was accepted or merged.

## Objective

Run one small, representative rendering discrepancy through the full DSH
research/debugging workflow: discover the environment, select an authentic
qualified case, reproduce it offline, localize the first divergence, consult
Ghidra only where it answers a concrete question, test a correction hypothesis,
and report the remaining uncertainty.

## Environment and preflight

- Baseline `f13602e4`; clean preparation worktree `wt_m7_prep`.
- Checked interpreter `/opt/homebrew/bin/python3` (3.15.2 + NumPy 2.5.5).
- EU4 executable SHA-256 `b3d38876…141d`; `EU4 Test` display id 12,
  1920×1200, non-main; primary desktop 1728×1117 @2x.
- Ghidra headless corpus reachable through `eu4_re_query`; no interactive
  disassembly was needed.
- The existing portable case from Phase C was selected. The pilot did **not**
  launch EU4: all reproduction and hypothesis tests used frozen frames and the
  Metal host.

## Reproduction

```sh
python3 -m automation.agent_tools eu4_replay case \
  --case .local/m7/phase-c-authentic-v2/phase-c-authentic-case-v2 \
  --replay-out .local/m7/phase-c-case-replay-timed
```

- campaign complete, target sequence 3, 19.97 s;
- history `status=ok`, sequence 3, no missing/stale resources;
- portable target 1,162,904 / 1,930,176 differing pixels, max 73;
- identical statistics on a second replay.

## First divergence

The first authoritative divergence is the captured copy source, not the copy
destination:

```text
frame: 1 / present index 615
kind: intermediate
event: 284
target: 0 (default framebuffer)
operation: glCopyTexSubImage2D
producer: draw 283, _GfxDraw, program 346
unresolved interval: unbounded..284 (event 13 is an unrelated tex:432 match)
next checkpoint: event 142, target 0, requires_capture=true
```

The GL/GL control compares the captured GL source with the captured GL copy
destination under `bgra_to_rgba`:

```text
frame 1: match, 0 / 1,930,176 differing pixels
frame 2: unavailable, reason=unverified_lifecycle_coverage
frame 3: unavailable, reason=unverified_lifecycle_coverage
```

The copy path is exact. The difference is already present in the default
framebuffer before event 284.

## Ghidra evidence

Only one query was needed:

```sh
python3 -m automation.agent_tools eu4_re_query function 0x15ece13
```

`_GfxResolve` is a 29-instruction function whose pseudocode is a bind, one
`glCopyTexSubImage2D`, and an unbind. This confirms the GL control's scope and
rules out a hidden resolve step. No disassembly or decompilation detour was
required.

## Correction hypotheses tested

| Hypothesis | Test | Result |
| --- | --- | --- |
| Copy execution is wrong | GL source vs GL destination, frame 1 only | **rejected for frame 1**: 0 differing pixels after the declared swap; frames 2/3 are `unavailable` (no copy-only lifecycle coverage), so no three-frame claim is made |
| Constant channel bias | Add ±1 to B/G/R/all | **rejected**: mismatch rises from 1,234,947 to 1,747,123–1,928,591 |
| Simple 4×4/8×8 Bayer dither | Add ±1 Bayer patterns to Metal output | **not supported**: mismatch rises to 1,244,082–1,598,720; per-cell means are nearly uniform |
| Metal fast-math translation | Recompile all MSL with `fastMathEnabled=NO` | **rejected as dominant**: only 253 of 1,160,152 final mismatches change (1,159,899 remain) |
| Multisample/edge coverage | Compare mismatch on local edges vs flat regions | **not supported**: 5.07% of pixels are edges; mismatch is lower on edges (56.4%) than flat (64.4%) |
| Content-dependent shader/filter precision | 1-LSB share, cross-frame sign agreement, spatial pattern | **leading hypothesis**, not proven |

The residual is 63.98% of the default framebuffer, 75.29% of differing pixels
are exactly 1 LSB, the error sign agrees at the same coordinates across
different frames only 68.6–75.4% of the time, and no static screen-space
pattern or constant bias explains it. `gl_dither` and `multisample` remain
unsupported, but the pilot evidence does not establish either as the exclusive
cause. Root cause is **not established**; the source-resource lower bound is unbounded (0–284 for bisection).

## DSH trace (measured)

`automation.dsh_efficiency analyze` on the final session export:

| Metric | Value |
| --- | --- |
| Session | `session-67b86ef2…`, start 2026-11-17T18:43:58Z |
| Tool calls | 724 total: 695 bash, 0 native tool-level failures |
| Tool-level failures | 0 adapter errors; 21 shell commands with a nonzero exit marker (including deliberate negative tests) |
| Long jobs | 62; captures 250.4 s and 248.8 s, the stopped full run 153.2 s, focused suites 127.8/127.2/126.7/124.8/123.3/121.7 s, ~20–39 s replays |
| Test invocations | 55 invocations / 25 distinct commands; 30 focused re-runs; the module-list full run is not counted as a completed full-suite pass |
| Tool wait | not meaningful across the multi-hour unattended gap |
| Output volume | 1,054,253 chars; 0 oversized, 5 truncated |
| Tokens | 342.7 M total; 99.0% cache-read |
| Human interventions | 1 approval-policy event; 0 user questions |
| EU4 launches | 3 capture command invocations, 2 actual EU4 process starts (one failed `draw_cap`, one successful 3-frame capture); 0 for the pilot |

The exact final trace is refreshed in
[`dsh-efficiency.json`](dsh-efficiency.json).

### Command-level failures and recoveries

| Failure | Recovery | Cost |
| --- | --- | --- |
| `timeout` is not installed on macOS | used direct commands and `run_in_background` | seconds |
| `.local/m7` did not exist for the capture log | created the directory and retried | seconds |
| first capture used the 256-event default and ended `draw_cap` | added `--max-draws 512`; one new launch succeeded | one EU4 launch, ~4 min |
| placeholder `package_sequence_diagnosis` call had the wrong signature/destination | read the signature and repackaged | seconds |
| native fault test first selected event 14, which is overdrawn | probed events 285/291/299/310/325; event 291 changes the final image | ~1 min |
| one test assertion expected a frame-1 fault object | scoped the fault metadata to the applied frame | seconds |
| `numpy.ndarray.abs()` is not a method | used `np.abs(...)` | seconds |

## Efficiency assessment

- **Unnecessary calls or repeated investigations.** The main avoidable detour
  was the first capture's default event cap; the capture had to be run twice.
  The native fault-event probe also tried one overdrawn event before choosing
  291. There were no duplicate focused suites and no repeated EU4 launches for
  the pilot. The 11 oversized outputs were mostly large `sed` reads of existing
  documentation and one truncated test log; a more selective read would have
  saved tokens but not wall time.
- **Would earlier visual comparison or targeted diagnostics have helped?**
  Yes for the copy question: the GL/GL control is cheap and decisive and
  should run immediately whenever a first divergence is a copy. It was added
  in Phase C. For the remaining unbounded–284 interval, the current diagnostic
  already names the correct next checkpoint; no additional visual comparison
  would narrow it without a new capture-side checkpoint.
- **Did Ghidra provide evidence efficiently?** Yes. A single `eu4_re_query
  function` call returned the 29-instruction `_GfxResolve` pseudocode and
  settled the copy scope; no disassembly detour was needed.
- **Were test selection and artifact retrieval scoped?** Mostly. Focused
  modules were used (230 tests, 118.8 s), the authentic case was replayed
  offline, and the compact diagnosis was 6 KB instead of 3 MB. A broader full
  run was attempted later and stopped at an interactive live benchmark prompt;
  it belongs to the final gate and is not claimed as a pass.
- **Could the agent recover from expected failures without intervention?**
  Yes. Every command-level failure above was recovered in-session; no human
  intervention was required after the initial approval-policy change.

## Artifacts

- [`pilot-analysis.json`](pilot-analysis.json) — frame, difference statistics,
  hypothesis table, Ghidra summary, next checkpoint.
- `gl-control-frame1.json` … `gl-control-frame3.json` — same-backend
  controls in the gitignored capture root: frame 1 is a zero-difference
  `match`; frames 2/3 are `unavailable` (`unverified_lifecycle_coverage`),
  not differences. The authoritative summary is in
  [`evidence.json`](evidence.json); the earlier frame-2/3 `match` fields in
  those local files predate the lifecycle-coverage gate and are superseded.
- `agent-diagnosis.json` — compact ~6.3 KB projection for frame 1.
- Portable case under `.local/m7/phase-c-authentic-v2/phase-c-authentic-case-v2`
  (gitignored); manifest hashes in `evidence.json`.

## Conclusion and next action

The pilot demonstrates the workflow on real evidence and rejects the obvious
infrastructure explanations. It does **not** establish the root cause. The
single highest-value next experiment is a capture-side default-framebuffer
checkpoint at event 142, the midpoint of 0–284, which bisects the unbounded–284 interval; if that is not
available, capture at event 142 in a bounded re-capture and repeat the same
diagnosis. No production correction is authorized by this pilot.
