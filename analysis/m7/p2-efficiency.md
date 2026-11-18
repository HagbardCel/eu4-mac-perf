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
  `EU4_OFFLINE_ONLY=1` marker make `gfx_launch.spawn`, `m6_frame capture`,
  `live_gfx capture` and `live_gfx probe` refuse a real launch. Production
  (unset profile) is unchanged. `automation.test_runner run` accepts
  `--profile` and `--min-tests`; the live profile requires an explicit module
  and is never selected for the full suite.
- **Mandatory offline manifest.** `automation.test_profiles verify
  --profile offline` imports every mandatory qualification module. Deleting or
  renaming one fails the manifest, and a test-run that executes fewer tests than
  its floor is `incomplete`, not passed. CI runs the manifest and the complete
  offline discovery with `EU4_TEST_PROFILE=offline`.
- **Classified live tests.** `tests.test_live_qualification` is decorated with
  `requires_live`; it skips with a reason in the offline gate and fails closed
  under the live profile when no real case is supplied. Known platform skips in
  the existing suite remain explicit (`skipUnless(..., reason)`).
- **Failure taxonomy.** Every `eu4-agent-tool/v2` envelope now carries
  `failure.kind` / `outcome.failure_kind`: `execution_timeout`,
  `missing_credential`, `graphics_unavailable`, `evidence_invalid`,
  `evidence_missing`, `policy_refusal` or `tool_failure`, separate from
  `failure_class: comparison` for a completed differing-pixel result. A
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

`automation.case_baseline` records one identity for an existing verified case
and replay baseline (manifest/payload inventory, seal, diagnostic report, graph,
replay result/readback, shaders, renderer sources, policy, platform) and checks
reuse fail-closed. It is a single identity receipt, not a persistent cache.
`automation.dsh_efficiency` now emits a compact execution-summary block for the
required P2 metrics; values the session export cannot support are explicit
`null`s with a reason (GL/Metal replay split, GPU checkpoint overhead, artifact
bytes), never measured-looking zeros.

## Authentic demonstration (C1)

Case: `.local/m7/p0-checkpoint-142/shadow/case` (`m7-live-frame-000001`,
manifest `7c00ba56…`, seal `794c482d…`, session `a3111e0d…`, 1,247 files,
graph complete, independently reproducible). No new EU4 launch and no replay
were performed.

| Check | Result |
|---|---|
| Offline verification | `eu4_replay verify` ok; graph complete; independently reproducible |
| First divergence unchanged | event 142, `fb:0`, `_GfxDrawIndexed`, program 331; final 1,160,291 / 1,930,176, max 70 |
| Causal interval unchanged | `start_event: null`, `lower_bound: unbounded`; `search_start_event: 0` is explicitly not matching evidence |
| Next checkpoint | event 71, `fb:0`, `requires_capture: true` |
| Baseline reuse | identity check `reusable=true`; no recomputation |
| Compact inspect | 8,018 bytes (was 479,719 bytes full) |
| Compact verify | 8,061 bytes (was 477,163 bytes full) |
| Reuse check | 3,426 bytes |
| Synthetic substitution | tampered payload fails `verify()` and invalidates the baseline (regression test) |
| Failure/unavailable outcomes | explicit taxonomy tests; timeout cannot be `ok` |
| Agent interface consumption | deterministic P2 workflow test reads identity, divergence, interval and next checkpoint from the compact envelope |

### P1 versus P2 operational overhead

The P1 reference is the preserved P1 session export
(`session-fd42d127-…`, one model turn). The P2 figure is the deterministic
native-interface workflow on the same verified case, not a new model turn (no
provider key is available in this environment).

| Metric | P1 | P2 interface path |
|---|---|---|
| Tool calls | 80 total (2 native, 66 shell) | 3 native, 0 shell |
| Returned output | 400,666 chars | 19,505 chars (–95.13%) |
| Oversized / truncated results | 6 / 2 | 0 / 0 |
| EU4 launches | 0 | 0 |
| Replays | 1 | 0 (baseline reused) |
| Tests run by the agent | 0 | 0 |
| Turns / elapsed | 1 / 1,220 s | not applicable (deterministic) |

The P1 model session did the investigation and reached
`requires_additional_evidence`; P2 does not claim to have re-run that reasoning.
It demonstrates that the routine evidence path is smaller, needs no shell, does
not replay an unchanged baseline and keeps the same qualified diagnostic
conclusions. Full per-session model overhead remains to be measured once
credentials are available.

## Offline gate

The final offline gate (`20261118T135839Z-d1948266`) ran with
`EU4_TEST_PROFILE=offline` / `EU4_OFFLINE_ONLY=1`: **3,701 tests, 40 skipped, 0
failures, 0 errors, 663.9 s**. The skips are classified: the live-only module,
platform-only hosts and 11 optional-QBDI TR3 tests. No EU4 process was started.
The same gate is configured as the required `portable` check.

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
