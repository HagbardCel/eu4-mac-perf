# M7-P2 — bounded offline diagnosis with compact native tools

You are the DSH agent for a bounded, **offline** differential diagnosis of one
authentic sealed OpenGL→Metal divergence. Produce a trustworthy conclusion or
a precise evidence request. Do not force a patch.

## Authoritative inputs (workspace-relative)

- Verified producer-bound case: `.local/m7/p2/producer-case-v5`
  (`m7-p2-producer-bound-v5`; same sealed capture as the historical P0 case, with
  a verified producer receipt)
- Compact diagnosis returned by `eu4_case_inspect` (do not read `report.json` or
  the historical `diagnostic-report.json`; both are ~6 MB)
- Frame seal: `.local/m7/p2/producer-case-v5/capture/frame-seal.json`
- Event stream: `.local/m7/p2/producer-case-v5/capture/frame-events.jsonl`
  (event N is line N)
- Existing replay baseline: `.local/m7/p2/producer-case-v5/replay/replay.json`
  and `replay/metal-frame.bgra`
- Baseline identity record: `.local/m7/p2/producer-baseline-v5.json`
- Tool reference: `docs/agent-tools.md`

The historical `.local/m7/p0-checkpoint-142/shadow/case` is read-only context:
it has the in-process diagnostic report but no producer receipt, so it cannot
satisfy baseline reuse. Do not relabel it, copy it over the producer case or
claim its classification for the producer case.

## Hard rules

1. **Zero EU4 launches.** Do not run `eu4_diff_run`, `eu4_capture probe` or any
   game executable. The session is offline; the launch guard refuses a real
   launch under `EU4_TEST_PROFILE=offline`.
2. **Reuse before recapture.** Read the compact case, verify it once, then
   reuse the existing replay. Do not create another full-frame capture.
3. **Same-capture causal rule.** An interval may be bounded only by
   checkpoints from the same `(session_id, capture_frame_id,
   event_stream_sha256)` on the same resource. An unbounded lower bound stays
   `null`; `search_start_event` is not observed evidence.
4. **Checkpoint economy.** Use the existing checkpoints. Request an additional
   readback only when the current causal interval requires it. The compact
   diagnosis names the next recommended checkpoint (`event 71`, `fb:0`,
   `requires_capture: true`).
5. **No unsupported qualifier.** `difference_with_partial_state_coverage` is
   not a qualified match and not a proven root cause. `gl_dither` and
   `multisample` remain unsupported; do not attribute the difference to them
   without an A/B experiment. The producer case's **standalone replay** is
   `incomplete_diagnostics` with `frame_shader_identity=unverified_session` by
   design; that axis is not a renderer bug. The historical in-process P0
   diagnosis is `difference_with_partial_state_coverage`; it is context, not a
   classification you may claim for the producer case.
6. **Budgets.** At most 3 hypotheses and 2 evidence-driven tests per hypothesis.
   At most one new offline replay, and only when the producer-verified evidence
   is invalid or a specific evidence gap requires it. No speculative renderer
   patch. No repository-wide searches, whole-log dumps or polling loops. Batch
   independent reads; keep independent failures separate.

## Method (bounded loop)

1. `eu4_case_inspect --case <case>`: read only the compact projection. It runs
   the portable verifier internally and returns the verification state,
   case/capture identity, qualification axes, first divergence and next
   checkpoint. Do **not** immediately call `eu4_replay verify` for a routine
   read; use it only when you need the independent verification command or are
   investigating a verification error.
2. Check reuse: `python3.14 -m automation.case_baseline check --case
   .local/m7/p2/producer-case-v5 --baseline
   .local/m7/p2/producer-baseline-v5.json`. A mismatch means recompute the
   affected result; do not silently use the old one and do not overwrite the
   recorded baseline.
3. Examine the current causal interval and only the relevant operations,
   shaders or Ghidra pseudocode. Use `eu4_re_query` for targeted lookup.
4. Form one falsifiable hypothesis, choose the cheapest discriminating test,
   run it, and record the result. Prefer NumPy arithmetic over the sealed GL
   blob and Metal readback over shell pipelines.
5. If a correction is supported, make the smallest edit, replay the identical
   sealed case, report before/after statistics and preserve exact-match
   regressions. Otherwise stop with an evidence request.
6. Emit a compact final report: classification, first divergence, interval,
   hypotheses/results, artifact paths, remaining uncertainty, next experiment.

## Required deliverable

Write `.local/m7/p2/pilot-conclusion.json` using the existing
`gfx-pilot-conclusion/v1` contract (see `dsh/pilots/m7-p1/objective.md` for the
schema). Validate it with the existing validator:

```sh
python3.14 -m automation.dsh_pilot validate \
  --conclusion .local/m7/p2/pilot-conclusion.json \
  --case .local/m7/p2/producer-case-v5
```

Finish with the adjusted diagnosis, the next checkpoint/experiment, and the
measured tool overhead (calls, output bytes, EU4 launches, replays). Do not
start a second session or campaign.
