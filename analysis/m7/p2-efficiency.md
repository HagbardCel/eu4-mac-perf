# M7-P2 — reliability, compact diagnostics and measured efficiency

Status: implementation and offline demonstration complete on
`m7/p2-infra-hardening` (base `246d7e08`, the merge of M7-P1 / PR #211). This
report is evidence, not a merge authorization.

## Scope

P2 preserves the P0/P1 architecture: one authentic EU4 capture, immutable
portable cases, the existing `gfx_replay_case.verify()` contract, the existing
checkpoint request/receipt machinery and exact pixel comparison. It adds no
renderer, tracing framework, orchestrator or speculative OpenGL/Metal fix.

## Reliability and isolation (PR A)

- **Explicit test profiles.** `automation.test_profiles` defines `offline`
  (default) and `live`. `EU4_TEST_PROFILE=offline` and the durable
  `EU4_OFFLINE_ONLY=1` marker make `gfx_launch.spawn`,
  `eu4_gfx_startup_trace.run_ops` and `live_gfx probe` refuse a real launch.
  Production (unset profile) is unchanged. `automation.test_runner run` accepts
  `--profile` and `--min-tests`; the live profile requires an explicit module
  and is never selected for the full suite.
- **Executed-test floor and mandatory tests.** The floor counts executed tests
  (`ran - skipped`), so a suite of classified skips is `incomplete`. The
  mandatory manifest also names required test ids and rejects a skipped or
  missing mandatory test. Duplicate-run identity includes the profile **and**
  the floor; a running `min_tests=1` invocation cannot satisfy a stricter
  request.
- **Classified live tests.** `tests.test_live_qualification` is decorated with
  `requires_live`; it skips with a reason in the offline gate and, under the
  live profile, requires the named case to pass `gfx_replay_case.verify()` and
  be independently reproducible. Known platform skips in the existing suite
  remain explicit (`skipUnless(..., reason)`).
- **Failure taxonomy.** Every `eu4-agent-tool/v2` envelope now carries
  `failure.kind` / `outcome.failure_kind`: `execution_timeout`,
  `missing_credential`, `graphics_unavailable`, `dependency_unavailable`,
  `evidence_invalid`, `evidence_missing`, `policy_refusal`, `tool_failure` or
  `null` for a comparison. Classification is driven by explicit
  status/diagnostic first: invalid evidence wins over dependency wording, a
  Metal shader/replay correctness error is a tool failure (not
  `graphics_unavailable`), a completed differing-pixel result is a comparison,
  and a nested invalid verification/diagnosis cannot appear qualified. A
  timeout or tool exception cannot report `ok`. The test runner records a
  `failure_kind` for passed/failed/timeout/incomplete outcomes and preserves
  the full log and record on failure.
- **Identity regressions.** The tuple
  `(session_id, capture_frame_id, event_stream_sha256)` is the capture identity;
  the existing same-capture interval tests are joined by a native monotonic
  capture-id test (3-frame campaign, then a new session whose first id is again
  1) and by cross-session / partial-identity / compact-evidence regressions.
  A deterministic P2 workflow test proves that tampered payloads fail
  `gfx_replay_case.verify()` and invalidate a stored baseline.

## Compact diagnostics and reuse (PR B)

`eu4_case_inspect` and `eu4_replay verify` now return `gfx-agent-case/v1` by
default: case/capture identity, verification, qualification axes, first
divergence, selected checkpoint statistics, the next recommended checkpoint,
artifact names/sizes and the next action. Full manifests, dependency graphs,
command streams and screenshots stay on disk. `--full` restores the complete
view. The compact envelope is capped at 8,192 emitted bytes and reports
`truncated`, `output_cap_bytes` and `output_bytes_before_cap` when reduced.

`automation.case_baseline` records one identity for an existing baseline only
after the current case passes `gfx_replay_case.verify()` and every declared
payload is re-hashed from disk. Reuse also requires a completed replay result
and readback. The identity covers the seal, diagnostic report, graph, replay
result/readback, translated shader inputs, the actual renderer include closure
(including `eu4_gfx_replay_semantics.h`), the replay binary/source, the
comparison policy and the platform. A matching stored fingerprint is necessary
but not sufficient: an invalid current identity is never reusable. It is a
single identity receipt, not a persistent cache.
`automation.dsh_efficiency` now emits a compact execution-summary block for the
required P2 metrics; values the session export cannot support are explicit
`null`s with a reason (GL/Metal replay split, GPU checkpoint overhead, artifact
bytes), never measured-looking zeros.

## Review-round corrections

The first reviewed head (`10087c33`) was corrected in five places:

1. **Baseline reuse is verification-bound.** Record/check now require
   `gfx_replay_case.verify()`, re-hash every declared payload from disk,
   require a completed replay/readback, and include the renderer include
   closure (including `eu4_gfx_replay_semantics.h`), shader inputs, replay
   binary/source, policy and platform. A matching fingerprint alone is not
   reusable; an invalid current case is rejected.
2. **The test floor counts executed tests**, not ran tests. Mandatory module
   and test ids are checked; duplicate-run identity includes profile and floor;
   a live case must pass `gfx_replay_case.verify()` and be independently
   reproducible.
3. **Failure classification is explicit.** Invalid evidence wins over generic
   dependency terms; shader/replay correctness failures are `tool_failure`;
   genuine capability failures are `graphics_unavailable`; dependency failures
   are `dependency_unavailable`; nested invalid evidence cannot look qualified.
4. **Compact output is enforced in exact emitted UTF-8 bytes** (8,192), not JSON
   characters. Essential identity, verification, first divergence, unresolved
   interval, recommended checkpoint and artifact references survive; truncation
   names what was removed.
5. **Inspection is a single native read by default.** `eu4_case_inspect`
   verifies internally; an immediate `eu4_replay verify` is redundant for a
   routine read and is reserved for independent qualification/error work. The
   efficiency evidence now separates interface-level and end-to-end claims.

## Authentic demonstration (C1)

Case: `.local/m7/p0-checkpoint-142/shadow/case` (`m7-live-frame-000001`,
manifest `7c00ba56…`, seal `794c482d…`, session `a3111e0d…`, 1,247 files,
graph complete, independently reproducible). No new EU4 launch and no replay
were performed.

| Check | Result |
|---|---|
| Offline verification | `eu4_case_inspect` runs `gfx_replay_case.verify()` internally: ok, graph complete, independently reproducible |
| First divergence unchanged | event 142, `fb:0`, `_GfxDrawIndexed`, program 331; final 1,160,291 / 1,930,176, max 70 |
| Causal interval unchanged | `start_event: null`, `lower_bound: unbounded`; `search_start_event: 0` is explicitly not matching evidence |
| Next checkpoint | event 71, `fb:0`, `requires_capture: true` |
| Baseline reuse | identity check `reusable=true`; no recomputation |
| Compact inspect | 8,018 emitted bytes (was 479,719 bytes full) |
| Reuse check | 2,355 bytes |
| Separate verify (not routine) | 8,061 bytes when independent verification is explicitly needed |
| Synthetic substitution | tampered payload fails `verify()` and invalidates the baseline (regression test) |
| Failure/unavailable outcomes | explicit taxonomy tests; timeout cannot be `ok` |
| Agent interface consumption | deterministic P2 workflow test reads identity, divergence, interval and next checkpoint from the compact envelope |

### Efficiency evidence scope

Three different things are deliberately separated:

1. **P1 autonomous session** (`session-fd42d127-…`): one model turn, 72 model
   calls, 80 tool calls (2 native, 66 shell), 400,666 returned characters,
   1,220 s. It reached `requires_additional_evidence`.
2. **P2 deterministic interface demonstration**: the two routine native reads
   below, with no model turn. It proves the interface contract and output size,
   not the end-to-end cost of an investigation.
3. **P2 autonomous session**: not run. `OPENCODE_GO_API_KEY` is absent in this
   environment, so end-to-end efficiency is **not yet measured**.

| Metric | P1 autonomous session | P2 interface demonstration |
|---|---|---|
| Tool calls | 80 (2 native, 66 shell) | 2 native, 0 shell |
| Returned output | 400,666 chars | 10,373 bytes |
| Oversized / truncated results | 6 / 2 | 0 / 0 |
| EU4 launches | 0 | 0 |
| Replays | 1 | 0 (baseline reused) |
| Model calls / elapsed | 72 / 1,220 s | 0 / not applicable |
| End-to-end comparable? | — | **No; interface-level only** |

The interface path is a genuine reduction in routine diagnostic bytes and
shell work, but it is **not** a replacement measurement for the P1 agent's
reasoning. A bounded P2 autonomous session against the same verified case is
still required before claiming end-to-end efficiency.

## Offline gate

The final offline gate (`20261118T162742Z-35ac5960`) ran with
`EU4_TEST_PROFILE=offline` / `EU4_OFFLINE_ONLY=1` and `--min-tests 3000`:
**3,731 tests ran, 3,690 executed, 41 skipped, 0 failures, 0 errors, 691.7 s**.
The floor counts executed tests, so classified skips cannot satisfy it. The
skips are the live-only module, platform-only hosts and 11 optional-QBDI TR3
tests; QBDI remains non-mandatory. No EU4 process was started. The same gate is
configured as the required `portable` check.

## Evidence

- [machine-readable evidence](p2-evidence.json)
- [P0 checkpoint and bisection](p0-checkpoint-bisection.md)
- [P1 autonomous pilot](p1-autonomous-pilot.md)
- [P2 bounded objective](../../dsh/pilots/m7-p2/objective.md)
- Preserved P1 session export:
  `~/.local/share/eu4-dsh/sessions/--Users-fabian-Projects-MiniProjects-eu4-mac-perf-wt_p1--/session-fd42d127-1706-47d6-acb1-d40bfaa3e91b`
  (gitignored).

## Remaining limits

- No new model-driven P2 session was run; the operational comparison is the
  native-interface path plus the P1 export.
- The local offline gate passes with classified platform/QBDI skips. The
  portable Linux CI run on the final head is the independent environment
  check.
- `gl_dither` and `multisample` remain unsupported; the event-142 difference is
  not attributed to them.
- The causal interval before event 142 remains unbounded; the recommended
  event-71 checkpoint requires a new capture and cannot bound the existing
  frame.
- A multi-checkpoint same-frame capture is the next justified instrumentation
  step; it is not silently added here.
