# M7-P2 — bounded offline diagnosis with compact native tools

You are the DSH agent for a bounded, **offline** differential diagnosis of one
authentic sealed OpenGL→Metal divergence. Produce a trustworthy conclusion or
a precise evidence request. Do not force a patch.

## Authoritative inputs (workspace-relative)

- Verified sealed case: `.local/m7/p0-checkpoint-142/shadow/case`
- Compact diagnosis returned by `eu4_case_inspect` (do not read
  `diagnostic-report.json`, which is ~3 MB)
- Frame seal: `capture/frame-seal.json`
- Event stream: `capture/frame-events.jsonl` (event N is line N)
- Existing replay baseline: `replay/replay.json` and `replay/metal-frame.bgra`
- Baseline identity record (if present): `.local/m7/p2/authentic-baseline.json`
- Tool reference: `docs/agent-tools.md`

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
   without an A/B experiment.
6. **Budgets.** At most 3 hypotheses and 2 patch experiments per hypothesis.
   No repository-wide searches, whole-log dumps or polling loops. Batch
   independent reads; keep independent failures separate.

## Method (bounded loop)

1. `eu4_case_inspect --case <case>`: read only the compact projection. It runs
   the portable verifier internally and returns the verification state,
   case/capture identity, qualification axes, first divergence and next
   checkpoint. Do **not** immediately call `eu4_replay verify` for a routine
   read; use it only when you need the independent verification command or are
   investigating a verification error.
2. Check reuse: `python3.14 -m automation.case_baseline check --case <case>
   --baseline .local/m7/p2/authentic-baseline.json`. A mismatch means recompute
   the affected result; do not silently use the old one.
4. Examine the current causal interval and only the relevant operations,
   shaders or Ghidra pseudocode. Use `eu4_re_query` for targeted lookup.
5. Form one falsifiable hypothesis, choose the cheapest discriminating test,
   run it, and record the result. Prefer NumPy arithmetic over the sealed GL
   blob and Metal readback over shell pipelines.
6. If a correction is supported, make the smallest edit, replay the identical
   sealed case, report before/after statistics and preserve exact-match
   regressions. Otherwise stop with an evidence request.
7. Emit a compact final report: classification, first divergence, interval,
   hypotheses/results, artifact paths, remaining uncertainty, next experiment.

## Required deliverable

Write `.local/m7/p2/pilot-conclusion.json` using the existing
`gfx-pilot-conclusion/v1` contract (see `dsh/pilots/m7-p1/objective.md` for the
schema). Validate it with the existing validator:

```sh
python3.14 -m automation.dsh_pilot validate \
  --conclusion .local/m7/p2/pilot-conclusion.json \
  --case .local/m7/p0-checkpoint-142/shadow/case
```

Then call `update_goal complete` with a one-line summary. Finish with the
adjusted diagnosis, the next checkpoint/experiment, and the measured tool
overhead (calls, output bytes, EU4 launches, replays).
