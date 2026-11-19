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
view. The compact envelope is capped at 8,192 exact emitted UTF-8 bytes and reports
`truncated`, `output_cap_bytes`, `output_bytes_before_cap` and
`truncated_fields` when reduced. If essential identity/provenance/causal fields
cannot fit, it returns a bounded `output_cap_exceeded` error rather than a
partial success.

`automation.case_baseline` records one identity for an existing baseline only
after the current case passes `gfx_replay_case.verify()`, whose single payload
pass re-hashes every declared file from disk. Reuse also requires a completed replay result
and readback. The identity covers the seal, diagnostic report, graph, a completed replay
result/readback (`stage=complete`), translated shader inputs, the renderer
include closure shared with the build (`eu4_gfx_replay_semantics.h`,
`eu4_gfx_draw_semantics.h`, `eu4_gfx_metal_replay.h`), the replay binary/source,
the comparison policy and the platform. A matching stored fingerprint is necessary
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

Final correction round:

6. **One replay-host dependency enumerator.** `automation.replay_host_dependencies`
   walks the two compilation units and their local include closure, and includes
   compiler identity, build flags and relevant build environment variables. Both
   `m6_frame.build_replay_host` and `case_baseline` use it. The build writes a
   dependency stamp only after a successful compile; a failed build removes the
   binary and stamp. Tests prove a semantics-header edit triggers a rebuild,
   an unchanged dependency set does not, flag/compiler changes invalidate, and
   failure leaves no valid-looking artifact.
7. **One payload-validation pass.** `case_baseline` no longer re-hashes declared
   payloads after calling the verifier. `gfx_replay_case.verify()` is the single
   authoritative content-hashing pass; the baseline classifies its payload
   problems without repeating the walk. A reusable replay requires
   `stage=complete`, `status=ok` and `readback=true`.
8. **Required tests execute, not merely exist.** `automation.test_profiles
   verify` now runs each required test id and rejects a dynamic skip
   (`self.skipTest`) as well as a static skip.
9. **Oversized essential compact fields fail closed.** If identity, provenance
   or causal fields cannot fit in 8,192 emitted UTF-8 bytes, the envelope returns
   an explicit bounded `output_cap_exceeded` error instead of silently dropping
   them.
10. **Session identity normalizes the CPython launcher.** The Homebrew
   `bin/python3.14` launcher and the framework app binary are the same process
   image for ownership purposes; start-time comparison still guards PID reuse.
11. **Replay results carry a producer receipt.** Every replay writes
   `producer-receipt.json` binding the replay executable SHA-256, build
   fingerprint/stamp, capture identity and `replay.json`/Metal-readback digests.
   Portable cases package and verify the receipt. Baseline reuse requires the
   receipt's executable and build fingerprint to match the current candidate
   renderer. Legacy cases without a receipt remain historical evidence but are
   rejected for reuse (`producer_identity_missing`).
12. **Replay execution uses an immutable per-run snapshot.** `replay()` copies
    the validated replay-host bytes to a private temporary executable, re-hashes
    the copy, and executes that snapshot; the producer receipt describes the
    snapshot bytes actually launched. If a concurrent build replaces the shared
    host between identity read and copy, the digest mismatch forces a retry
    rather than mislabelling the result.
13. **External replay overrides are rejected.** `case_baseline` binds reuse to
    the verified packaged `replay/` directory; a `--replay-dir` pointing
    elsewhere is `external_replay_override`, not a second provenance system.
14. **Missing receipt-bound artifacts fail closed.** A producer receipt without
    `replay.json`/Metal readback returns structured invalid evidence instead of
    an unhandled filesystem exception.
15. **Baseline reuse requires full-frame completion.** The shared
    `gfx_independent_replay.full_frame_completion_problems()` predicate requires
    `status=ok`, `stage=complete`, `readback=true`, `partial=false`,
    `timed_out=false`, `failed=0`, integer `events/encoded` with equality, and
    Metal `command_status=4`. Missing or inconsistent metadata fails as
    `replay_incomplete`; the producer receipt must agree with `replay.json` for
    those execution fields.
16. **Build publication is atomic and locked.** The replay host is compiled to a
   private temporary path, validated, atomically renamed, and stamped with its
   binary SHA-256. An exclusive process lock plus an in-process lock serialize
   overlapping builds; failed/timeout/interrupted builds remove the binary and
   stamp. Reuse checks both the source/build fingerprint and binary digest.

## Authentic demonstration (C1)

The historical P0 case (`.local/m7/p0-checkpoint-142/shadow/case`,
`m7-live-frame-000001`, seal `794c482d…`, session `a3111e0d…`) has **no producer
receipt**. It remains historical evidence, but baseline reuse is rejected with
`producer_identity_missing` rather than relabelling its replay as current-
renderer output.

One bounded offline replay of that same capture (no EU4 launch) produced a
producer-bound case, `.local/m7/p2/producer-case-v5` (`m7-p2-producer-bound-v5`),
which verifies with `producer_identity=verified`. All reported statistics below
are from that producer-bound case; the first divergence and pixel result are
unchanged.

| Check | Result |
|---|---|
| Offline verification | `eu4_case_inspect` runs `gfx_replay_case.verify()` internally: ok, graph complete, independently reproducible, producer verified |
| First divergence unchanged | event 142, `fb:0`, `_GfxDrawIndexed`, program 331; final 1,160,291 / 1,930,176, max 70 |
| Causal interval unchanged | `start_event: null`, `lower_bound: unbounded`; `search_start_event: 0` is explicitly not matching evidence |
| Next checkpoint | event 71, `fb:0`, `requires_capture: true` |
| Baseline reuse | identity check `reusable=true`; no recomputation |
| Compact inspect | 8,003 emitted bytes (was 479,719 bytes full) |
| Reuse check | 1,760 bytes |
| Producer receipt | executable SHA-256, build fingerprint, capture identity and replay/readback digests verified |
| Separate verify (not routine) | 8,061 bytes when independent verification is explicitly needed |
| Synthetic substitution | tampered payload fails `verify()` and invalidates the baseline (regression test) |
| Failure/unavailable outcomes | explicit taxonomy tests; timeout cannot be `ok` |
| Agent interface consumption | deterministic P2 workflow test reads identity, divergence, interval and next checkpoint from the compact envelope |

### Efficiency evidence scope

Four different things are deliberately separated:

1. **P1 autonomous session** (`session-fd42d127-…`): one model turn, 72 model
   calls, 80 tool calls (2 native, 66 shell), 400,666 returned characters,
   1,220 s. It reused the existing baseline (0 replays during the turn) and
   reached `requires_additional_evidence`.
2. **P2 deterministic interface demonstration**: the two routine native reads
   below, with no model turn. It proves the interface contract and output size,
   not the end-to-end cost of an investigation.
3. **P2 autonomous session (C2)** (`session-fbd20740-…`, 2026-11-19): one model
   turn against `.local/m7/p2/producer-case-v5`, 13 model calls, 20 tool calls
   (2 native, 9 shell, 6 read, 2 glob, 1 write), 85,622 returned characters,
   314.6 s, 0 replays and 0 EU4 launches. It reused the recorded producer
   baseline and reached `requires_additional_evidence`.
4. **P2 live event-71 capture**: not run. The offline pilot succeeded and did
   not expose a native-capture-path defect.

| Metric | P1 autonomous | P2 autonomous (C2) | P2 interface demo |
|---|---|---|---|
| Tool calls | 80 (2 native, 66 shell) | **20** (2 native, 9 shell) | 2 native, 0 shell |
| Model calls | 72 | **13** | 0 |
| Elapsed | 1,220 s | **314.6 s** | not applicable |
| Tokens total | 10,084,703 | **524,175** | not applicable |
| Returned output | 400,666 chars | **85,622 chars** (68,616 raw tool-result bytes) | 9,763 bytes |
| Oversized / truncated | 6 / 2 | **1 / 0** | 0 / 0 |
| Tool wait | 34.131 s (2.8%) | **4.057 s** (1.29%) | 0 |
| EU4 launches | 0 | **0** | 0 |
| Replays during turn | 0 | **0** (baseline reused) | 0 (baseline reused) |
| End-to-end comparable? | — | **yes; model-driven** | **no; interface-level only** |

The C2 session is 75.0% fewer tool calls, 81.9% fewer model calls, 74.2% less
wall time, 94.8% fewer tokens and 78.6% fewer returned characters than P1. It
is the end-to-end efficiency measurement that P2 previously lacked. The
interface path remains a separate reduction in routine diagnostic bytes and
shell work; it is not a substitute for the model-driven measurement.

C2's conclusion and independent reproduction are in
[P2 closure pilot](p2-closure-pilot.md). Acceptance required a minimal
`automation.dsh_pilot` correction because the producer-bound case has a
standalone replay report, not an in-process `diagnostic-report.json`. The fix
accepts producer-receipt-backed evidence for an **unbounded** observation while
keeping the in-process report mandatory for a **bounded** interval.

## Offline gate

The final offline gate (`20261119T072637Z-551b7e55`) ran with
`EU4_TEST_PROFILE=offline` / `EU4_OFFLINE_ONLY=1` and `--min-tests 3000`:
**3,760 tests ran, 3,718 executed, 42 skipped, 0 failures, 0 errors,
1,318.3 s**. The floor counts executed tests, so classified skips cannot satisfy
it. The skips are the live-only module, platform-only hosts and 11 optional-QBDI
TR3 tests; QBDI remains non-mandatory. No EU4 process was started. The same gate
is configured as the required `portable` check; GitHub Actions `portable` passed
on head `5ec4fdc2` in 4m34s (run 44483074062). CI is not independent approval.

The closure gate (`20261119T092938Z-23b4e94b`, same profile and floor) ran
**3,763 tests, 3,721 executed, 42 skipped, 0 failures, 0 errors in 675.7 s**.
The three additional tests are the producer-bound acceptance regressions in
`tests.test_dsh_pilot`.

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
