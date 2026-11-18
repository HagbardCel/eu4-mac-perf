# M7-P1 — one autonomous differential-debugging pilot

You are the DSH agent for a bounded, **offline** investigation of an authentic
OpenGL→Metal rendering divergence in one sealed EU4 frame. Your job is to
produce a trustworthy conclusion — or a precise, actionable evidence request —
not to force a patch.

## Authoritative inputs (all relative to the session workspace)

- Portable sealed case: `.local/m7/p0-checkpoint-142/shadow/case`
- Compact diagnosis (read this, ~6 KB):
  `.local/m7/p0-checkpoint-142/shadow/frame-000001/agent-diagnosis.json`
- Frame seal (identity): `.local/m7/p0-checkpoint-142/shadow/case/capture/frame-seal.json`
- Captured GL event stream (325 lines, event N is line N):
  `.local/m7/p0-checkpoint-142/shadow/case/capture/frame-events.jsonl`
- Metal replay result and readbacks:
  `.local/m7/p0-checkpoint-142/shadow/case/replay/`
- Existing offline baseline replay (same case, already produced):
  `.local/m7/p0-checkpoint-142/case-replay/`
- P0 method and limits: `analysis/m7/p0-checkpoint-bisection.md`
- Canonical pixel-exact regression frame: `.local/m6/frames/menu-occ300/frame`
- Tool reference: `docs/agent-tools.md`

Do **not** read `shadow/case/diagnostic-report.json` (multi-megabyte) unless a
specific field is missing from the compact diagnosis; prefer the compact file,
`replay/replay.json`, and bounded queries over the event stream.

## Ground truth to start from

- The case verifies: 1,247 files, graph complete, independently reproducible.
- One authentic sealed frame: 325 operations (315 draws, 9 clears, 1 copy),
  present/scene index 617, session `a3111e0d-…`, event stream
  `62cfdae4…`.
- The final framebuffer differs on 1,160,291 / 1,930,176 pixels, max channel
  error 70.
- First confirmed intermediate divergence: `fb:0` immediately before the draw
  at event 142 (program 331, `_GfxDrawIndexed`): 871,620 pixels differ, max 103.
- Checkpoints that exist in the sealed case: event 11 `tex:431` (match),
  event 13 `tex:432`, event 142 `fb:0` (divergent), events 270/284/286/288/290
  (textures, including the in-frame copy source/destination).
- Event 71 has **no** captured checkpoint.
- `gl_dither` and `multisample` are captured but not implemented by Metal. No
  A/B experiment attributes the difference to them.
- The standalone case replay is classified `incomplete_diagnostics`
  (`frame_shader_identity=unverified_session`) **by design**: an offline replay
  has no in-process shadow identity report. The authentic in-process diagnosis
  is `difference_with_partial_state_coverage`. That axis difference is known
  and is not the rendering bug you are looking for.

Distinguish four things explicitly in your conclusion:

1. the confirmed difference (pixels/checkpoint statistics);
2. the first **observed** divergence (event 142 — not a root cause);
3. a candidate producer (event 141 / program 331 / its inputs — a hypothesis);
4. an established root cause (only after a discriminating experiment).

## Hard rules

1. **Zero EU4 launches.** Do not run `eu4_diff_run`, `eu4_capture probe`, a
   dual-process campaign, or any EU4 executable. Everything must be offline.
2. **Same-capture causal rule.** A causal interval may only be bounded by
   checkpoints from the same sealed frame (same `session_id`,
   `capture_frame_id`, `event_stream_sha256`) on the same resource. Never
   combine a checkpoint from a new capture/frame with event 142 of this frame,
   even if scene parameters match. If you conclude that a new observation is
   required, return a capture request instead of a claim.
3. **No speculative patch.** A correction may be proposed only when a
   falsifiable hypothesis is supported by evidence and the corrected renderer
   improves the **identical sealed case**. Otherwise the correct outcome is
   `requires_additional_evidence`.
4. **No unsupported qualifiers.** Do not call the case a qualified match, do
   not assert a root cause, and do not attribute the difference to dithering or
   multisampling without an experiment that supports it.
5. **Budgets.** At most 3 evidence-driven hypotheses; at most 2 patch
   experiments per hypothesis; targeted `eu4_re_query` calls only (no broad
   disassembly); one baseline replay unless the existing output verifies (it
   does); write scratch data only under `.local/m7/p1/`. Keep the captured case
   and the GL reference bytes untouched.
6. Prefer native tools first: `eu4_case_inspect`, `eu4_replay verify`,
   `eu4_replay case`/`frame`, `eu4_re_query`. Use shell only for bounded file
   inspection and arithmetic (`python3.14` has NumPy).

## Method (bounded loop)

1. Call `create_goal` with the objective "Investigate the sealed M7-P0 event-142
   divergence offline and produce a validated pilot conclusion" and
   `max_goal_rounds: 1`.
2. Reproduce the baseline from existing evidence: `eu4_case_inspect`, then
   `eu4_replay verify`; confirm the compact diagnosis and the checkpoint
   statistics. Do not re-run the baseline replay unless the existing output is
   not usable.
3. Inspect the captured state for event 142 (and 141): render state, program,
   shader sources, textures/samplers, draw parameters; compare with what the
   Metal replay reports it applied (`replay/replay.json`, provenance).
4. For each hypothesis: state it as a falsifiable proposition, choose the
   cheapest discriminating test, run it, and record the result. Characterize
   the event-142 difference quantitatively (per-channel error histogram,
   spatial distribution, alpha, edges vs interior) before attributing it.
5. If — and only if — an experiment supports a minimal correction in the
   existing Metal renderer or shader-translation path: make the smallest edit,
   rebuild through the existing replay path (source mtimes trigger the build),
   replay the **identical** sealed case, and report before/after statistics.
   Also preserve the canonical exact-match frame's 0-mismatch result.
6. If no correction is defensible, stop. Write a precise capture request:
   which event/resource, in which frame scope, and why the existing checkpoint
   set cannot decide the question. Include the cross-frame caveat when the
   request is for a new capture.

## Required deliverable

Write `.local/m7/p1/pilot-conclusion.json` with exactly this shape:

```json
{
  "schema": "gfx-pilot-conclusion/v1",
  "case": {
    "case_id": "m7-live-frame-000001",
    "verification_ok": true,
    "sealed_frame": {
      "session_id": "<copy from frame-seal.json>",
      "capture_frame_id": 1,
      "event_stream_sha256": "<copy from frame-seal.json>"
    }
  },
  "starting_diagnosis": {
    "classification": "difference_with_partial_state_coverage",
    "first_divergence": {"event": 142, "resource": "fb:0"},
    "root_cause": "not_established"
  },
  "hypotheses": [
    {
      "id": "H1",
      "statement": "<falsifiable statement>",
      "status": "supported | refuted | insufficient",
      "evidence": ["<concrete observation, file and field>"],
      "test": {"method": "<what you ran>", "result": "<what it showed>"}
    }
  ],
  "outcome": "correction_proposed | requires_additional_evidence",
  "correction": {
    "files": ["<repo-relative path>"],
    "description": "<minimal change and why>",
    "validation": {
      "same_sealed_case": true,
      "before": {"mismatched_pixels": 1160291, "max_channel_error": 70},
      "after": {"mismatched_pixels": 0, "max_channel_error": 0},
      "replay_artifact": "<path to the after replay output>",
      "exact_match_regressions": [
        {"case": "canonical-title", "mismatched_pixels": 0}
      ]
    }
  },
  "evidence_request": {
    "reason": "<why the existing evidence cannot decide>",
    "frame_scope": "same_sealed_frame | new_capture",
    "cross_frame_caveat": "<required for new_capture>",
    "checkpoints": [
      {"event": 71, "resource": "fb:0", "why": "<what it would decide>"}
    ]
  },
  "remaining_uncertainty": ["<explicit unresolved item>"],
  "next_experiment": "<single most useful next step>"
}
```

Only include `correction` for `correction_proposed` and `evidence_request` for
`requires_additional_evidence`. Any claimed `causal_interval` must carry
`same_capture: true` and a `basis` whose checkpoints all name the sealed frame's
capture identity.

Validate before finishing (iterate until it passes):

```sh
python3.14 -m automation.dsh_pilot validate \
  --conclusion .local/m7/p1/pilot-conclusion.json
```

Then call `update_goal complete` with a one-line summary. Finish with a short
answer containing: starting diagnosis, hypotheses and results, outcome, patch
(if any), remaining uncertainty, and the next experiment.
