# M7-P1 — autonomous DSH differential-debugging pilot

Status: one real model-driven DSH session investigated the authentic M7-P0
event-142 divergence offline, reached a structured conclusion, and stopped with
an actionable evidence request. It proposed no patch, because no correction was
supported by the sealed case. This report is evidence, not a merge
authorization; independent review remains the gate.

## Base, branch and scope

- Base: `b4a1f3109880e7661eb0b343ab9d47cf168cf84f` (`origin/main`, merge of
  PR #210 / M7-P0).
- Branch: `m7/p1-dsh-autonomous-pilot`.
- Deliverable: one bounded autonomous diagnosis plus the minimum integration
  needed to run and audit it. No production Metal replacement, no gameplay
  coverage, no full-frame GL/GL reconstruction, no performance work.
- EU4 launches: **0**. The reference case is the existing immutable P0 capture.

The pilot is a DSH-native `/goal` session, not a new orchestrator: DSH owns the
model loop, tool calls, session lifecycle and goal state; the repository
supplies the case, the compact diagnosis and a conclusion validator.

## P1.1 — a working model-driven session

`dsh/scripts/preflight.sh` passes on the P1 worktree after reconciling the pnpm
pin to the installed `12.21.0` (the environment had upgraded from the pinned
`12.18.0`; downgrading a global install is not this PR's call). Resolved
runtime: DSH `0.2.2-rc.1`, Node `v26.13.1`, Python 3.14.10 with NumPy 2.5.5.
Provider `opencode-go`, model `deepseek-v4.1-flash`, credentials present in the
dedicated DSH home (the value is never printed or committed).

The model request header of the pilot session lists all **seven** native tools
(`eu4_re_query`, `eu4_session`, `eu4_capture`, `eu4_diff_run`,
`eu4_diff_inspect`, `eu4_replay`, `eu4_case_inspect`).

The previous `MISSING_CREDENTIAL` failure was resolved by supplying the stored
`OPENCODE_GO_API_KEY` to the headless process environment. A minimal smoke
session (`session-8eb3073d…`) proves the path end to end: one model turn, one
`eu4_case_inspect` call, answer
`case_id=m7-live-frame-000001 verification_ok=true`. A second, offline smoke
validated the launcher's export lookup and `dsh_efficiency` integration.

## P1.2 — verified diagnostic case

`.local/m7/p0-checkpoint-142/shadow/case` verifies: `ok=true`, no problems,
1,247 files, graph `complete`, `independently_reproducible=true`. Acceptance
now requires that verification rather than the conclusion's own
`verification_ok` flag: `summarize_run()` runs the existing
`gfx_replay_case.verify()` (or consumes an independently produced,
digest-bound receipt), requires valid/complete/independently reproducible
evidence, and checks the case ID, sealed capture identity, and the
`case.json` / `capture/frame-seal.json` digests against the conclusion.
Case manifest `7c00ba56…`, frame seal `794c482d…`.

The standalone `eu4_replay case` baseline reproduces the P0 statistics — first
divergence event 142 `fb:0`, 871,620 / 1,930,176 differing pixels (max 103), and
final 1,160,291 / 1,930,176 (max 70) — but is classified
`incomplete_diagnostics` with `frame_shader_identity=unverified_session`. This
is the documented limitation of a standalone replay, not a rendering result:
the in-process P0 diagnosis is `verified` and is the starting diagnosis.

The pilot was fed the 6.3 KB `gfx-agent-diagnosis/v1` projection, not the
multi-megabyte diagnostic report.

## P1.3/P1.4 — the authentic pilot

Session `session-fd42d127-1706-47d6-acb1-d40bfaa3e91b`, one turn, 20.3 minutes,
goal `create` → `complete`. It created the goal, read the compact diagnosis and
seal, verified the case, inspected the event stream, replay provenance and the
program-331 GLSL→MSL translation, and recomputed pixel statistics with NumPy.
It never launched EU4.

| Hypothesis | Verdict | Decisive evidence |
| --- | --- | --- |
| H1: positional/ordered dithering or coverage-edge-confined MSAA is the dominant cause | **refuted for those signatures** | 821,783 of 871,620 differing pixels (94.28%) are exactly 1 LSB; the signed error is asymmetric and unstructured in position (no 2/4/8 periodicity); only 162 pixels reach ≥8 LSB and those are edge-localized. This does **not** exclude a dithering implementation that is not position-ordered, or an MSAA resolve difference that is not edge-confined; no A/B toggle exists, so the broad "dithering or MSAA explains it" claim is not refuted and is not made. |
| H2: the immediately preceding program-331 water draws produced it | **insufficient** | 2,739/2,739 replay bindings have no substitution and the program-331 translation preserves the audited semantics; reference-land regions differ on 95.2% of pixels, which 122 water draws cannot paint; no `fb:0` observation exists between the event-7 full clear and event 142. Event 7 is a search boundary, not a matching checkpoint. |
| H3: the dominant 1-LSB component is consistent with numerical/quantization variation rather than a binding/shader-content defect | **supported (dominant component only; hypothesis, not mechanism)** | The same 1-LSB signature is already present in `tex:433` at event 270 (574,288 differing RGB pixels, 569,880 at 1 LSB, max 109), independent of program 331; captured textures upload as decompressed RGBA8, so block decode is not a source. It does not establish which pipeline stage varies, and the edge-localized residual is not explained. |

Independent verification recomputed the histograms from the sealed GL blob and
the Metal readback and reproduced the pilot's event-142 and final numbers
exactly (`event 142`: 821,783 at 1 LSB, 49,206 at 2, 631 at ≥3, 162 at ≥8, max
103, alpha identical; final: 1,160,291, max 70). The event stream confirms the
event-7 clear of `fb:0` (`flags=7`), program 301 at events 14–19 and program 331
at events 20–141; provenance confirms 2,739 bindings with zero substitutions.

**Outcome:** `requires_additional_evidence`, with no patch, `root_cause` still
`not_established`, and no causal interval claimed. The validator
(`python3.14 -m automation.dsh_pilot validate`) reports `ok=true`, zero
problems. The run summary is bound to the verified case receipt
(`case.json` and `frame-seal.json` digests) and its capture identity, and the
native gate confirms that `eu4_case_inspect` and `eu4_replay` actually executed
from the run-event records.

The evidence request is precise and same-frame-aware: capture a **new** sealed
frame of the identical selection with a pre-event `fb:0` checkpoint at
**event 14** (first draw after the event-7 clear); if it matches, at **event
20** (first program-331 draw); then **event 71** for midpoint bisection. Event
7 is an operational search boundary, not a matching checkpoint, so no lower
bound is invented. The request states that a new capture has a new
`session_id`, `capture_frame_id` and `event_stream_sha256`, that its
checkpoints cannot be combined with event 142 of sealed frame 1, and that every
requested checkpoint is compared only with its own new frame's GL reference.

## P1.5 — efficiency of the measured run

Source: `dsh_efficiency` over the persisted session export (digest in the
evidence JSON).

| Metric | Value |
| --- | --- |
| Turns / model calls | 1 / 72 |
| Elapsed | 1,220 s |
| Tool calls | 80 (66 bash, 6 `read_image`, 1 `eu4_case_inspect`, 1 `eu4_replay`, goal/tool bookkeeping) |
| Failed tool calls / shell non-zero exits | 0 / 3 (two path mistakes, one missing optional PIL) |
| Repeated identical calls | 0 |
| Tool wait | 34.1 s (2.8% of elapsed) |
| Output volume | 400,666 chars; 6 results >12 KB; 2 truncated (~50 KB `eu4_case_inspect` / `eu4_replay verify`) |
| Tests run by the agent | 0 |
| EU4 launches | 0 |
| Metal replays | 0 (existing verified baseline reused) |
| Tokens | 10,084,703 total: 223,442 input, 9,790,976 cache read, 70,285 output (97.1% cache read) |
| Goal | `complete` |

Interpretation:

- The run was evidence-driven, not launch-driven: zero EU4 processes, zero
  replays, no duplicate tests, no repeated calls.
- Model latency/context dominates: 97% of wall time is model time, and 97% of
  tokens are cache reads. A single long DSH turn is expensive; future pilots
  should cap context growth and prefer compact native responses.
- The two native calls returned ~50 KB each and were truncated, which pushed
  the agent into 66 shell calls. A compact-by-default `eu4_case_inspect` /
  `eu4_replay verify` projection is the highest-value next efficiency change.
- The late sampler-cache/texture-upload audit (roughly 20 calls after H3) did
  not change the conclusion; it is the main "unnecessary investigation" cost,
  though it produced a real latent finding (the sampler cache key omits
  `lodMinClamp`/`lodMaxClamp`/`wrap_r`/compare/anisotropy, equivalent on the
  event-142 path but incomplete generally).
- No correction was attempted, so the two-patch-experiment budget was unused.

## Integration corrections in this PR

1. **Same-capture causal bounds (fail-closed).** `capture_identity()` derives
   `session_id` / `capture_frame_id` / `event_stream_sha256` from the seal and
   trace; `reconcile_checkpoints(..., capture=...)` stamps every observation;
   `first_divergence()` refuses to bound an interval with a same-resource match
   from another capture (or with unknown identity once one side is known) and
   reports `cross_capture_excluded`; `analyse_frame()` marks a mixed-capture
   reconciliation `invalid` and records `capture.identity` in the report.
2. **Pilot conclusion contract.** `automation/dsh_pilot.py` validates
   `gfx-pilot-conclusion/v1`: required diagnosis fields, the three-hypothesis
   budget, no resolved interval from an unresolved pilot, and a claimed
   bounded interval that must carry one authoritative matching lower
   observation and one authoritative divergent upper observation on the same
   resource/target, in the same sealed capture, with no duplicate, incomplete
   or contradictory entries (an unrelated texture match cannot bound `fb:0`;
   resource generations must agree when present). Unbounded intervals are
   reported with a null `start_event` and only the divergent upper
   observation. Beyond the structural contract, every claimed endpoint is
   looked up in the verified case's existing `diagnostic-report.json`
   checkpoints; a fabricated event-71 match is `interval_unverified`, never a
   causal bound. The same module also requires a correction's before/after
   statistics and preserved exact-match regressions, and an evidence request
   with a frame scope and a cross-frame caveat for a new capture.
3. **Mandatory case verification and actual-invocation native gate.**
   `summarize_run()` requires a case directory, runs the existing
   `gfx_replay_case.verify()` (or a digest-bound receipt), and rejects absent,
   incomplete, corrupt, identity-mismatched or non-reproducible cases; the
   conclusion's own `verification_ok` flag is not independent proof.
   `automation.dsh_pilot gate` reads the launcher's `events.jsonl` and pairs
   each `tool_call` with a completed `tool_result`. Only the seven supported
   native EU4 tools (reused from `automation.agent_tools.TOOLS`) count; a
   bash-only, image-read-only or goal-management-only run fails. `summarize`
   uses the same gate and requires the EU4 launch count to be a present integer
   exactly zero.
4. **One-turn pilot launcher.** `dsh/scripts/run-pilot.sh` composes the shipped
   headless profile with the eu4-tools overlay, requires the provider key in
   the environment, preserves `events.jsonl`, `session.json`,
   `native-gate.json` and the efficiency audit, and fails when the gate fails.
   It does not schedule or continue goal rounds.
5. **Pilot objective.** `dsh/pilots/m7-p1/objective.md` is the bounded task the
   session executed; the same file can seed a rerun.
6. **Environment pin.** pnpm `12.21.0` reconciled in `dsh/runtime.json`.

## Verification

- New focused regressions: 62 tests (`tests.test_dsh_pilot` 55,
  `tests.test_m7_checkpoint_provenance` 7). They cover native-only,
  bash-only, mixed, empty, failed-native and required-tool traces; launch
  count missing/non-numeric/non-zero; missing/self-asserted/corrupt/
  identity-mismatched cases; digest-bound receipt reuse; fabricated
  same-capture endpoints; and bounded/unbounded causal intervals with mixed
  resources, cross-capture observations, missing or wrong endpoints,
  non-authoritative observations, duplicates and generation mismatches.
- Requested focused/dependent set: 277 tests, 1 skipped, passed in 4.5 s
  (`test_dsh_pilot`, `test_m7_checkpoint_provenance`, `test_m7_diagnosis`,
  `test_gfx_replay_checkpoints`, `test_agent_tools`,
  `test_dsh_native_bridge`).
- Recorded full-suite result `20261118T120425Z-a5e93e6b`: 3,641 tests, 28
  skipped, 11 failures, recorded before the final case-verification change.
  All eleven are the pre-existing QBDI-unavailable `test_trace_tr3` failures
  (QBDI x86-64 release absent at `~/.cache/eu4-mac-perf/qbdi/0.12.1`). The
  full-suite selection has no matching base full-suite record, so the previous
  reference was corrected: the affected modules were compared directly
  (`test_gfx_session` + `test_trace_tr3`) against the base checkout and
  `automation.test_runner compare` reports **`no_new_failures`** (11
  pre-existing, 0 new). The final portable CI run on the exact head is the
  full-suite gate.
- Portable checks: `compileall`, 34 goal-plugin tests, 9 tools-plugin tests,
  forbidden-tracked-paths, doc links and `git diff --check` pass.
- The authentic pilot still passes the strengthened gates: the case verifies
  (1,247 files, complete, independently reproducible), its actual
  `events.jsonl` shows `eu4_case_inspect` and `eu4_replay` executed, the
  summary binds to the case manifest/seal digests and matching capture
  identity, all 8 authoritative checkpoints are present, and the launch count
  is the integer `0`.
- Authentic integration check: diagnosing the packaged P0 Metal result now
  reports `capture.identity` and stamps all 8 checkpoints with the same capture;
  the first divergence remains event 142 with no cross-capture exclusions.

## Limits and next steps

- Event 71 in a **new** frame cannot establish a causal interval ending at
  event 142 of this frame. The request above deliberately asks for event 14
  first, within one newly sealed frame.
- `gl_dither` and `multisample` remain captured-but-unimplemented; this pilot
  excludes them as the *dominant* cause from the error pattern, not by an A/B
  toggle.
- The edge-localized residual (631 pixels ≥3 LSB, max 103) is unexplained.
- `tex:433` is a diagnostic checkpoint (RGB8 internal format), not an
  authoritative first-divergence observation.
- The replay artifact records bindings but not applied per-draw blend/depth
  state, so a state-level mismatch cannot be excluded from it alone.
- A multi-checkpoint same-frame extension (event 14 and 20 and 71 in one
  capture) is the justified next engineering step; it is not silently added
  here.
- The sampler cache-key omission is a real latent finding but was not converted
  into a patch: it is equivalent on the audited event-142 path and the pilot
  had no falsifiable correction that improved the sealed case.

## Evidence

- [compact evidence JSON](p1-autonomous-pilot-evidence.json)
- [validated pilot conclusion](p1-autonomous-pilot-conclusion.json)
- [P0 case and bisection](p0-checkpoint-bisection.md)
- Session export: `<dsh-home>/sessions/<workspace-slug>/session-fd42d127-1706-47d6-acb1-d40bfaa3e91b/session.v4.jsonl.zstd`
  (gitignored, digest in the evidence JSON); the smoke session export is
  referenced beside it.
