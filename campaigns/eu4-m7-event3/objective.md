# M7 event-3 exact-prefix milestone

Objective: establish exact original-EU4-OpenGL versus Metal framebuffer equality
for events 0 through 3 of the same authenticated, sealed captured frame.

The original EU4 OpenGL process is the only oracle.  The independent checker is
`campaigns/eu4-m7-event20/accept.py` with `--target-end 3`; it owns the verdict.
The offline three-event fixture is a mechanism smoke test, not evidence of
production progress.

## Governance

There is no replay, correction, validation or goal-round quota.  Live EU4
launches are controlled separately: this campaign is
`autonomous_reconstruction` and must not launch EU4.  If the sealed evidence is
insufficient, stop with `requires_additional_evidence` and name the exact
capture request; never construct a launcher or bypass the one-use launch guard.

Before an expensive experiment, review the existing evidence, state the precise
evidence gap and what the experiment will establish, prefer cheaper/narrower
controls, and reuse a completed deterministic experiment instead of repeating
it.  A repeated failed hypothesis causes reassessment, not automatic termination.

Use the prepared campaign validation path (`eu4_validate_candidate`) for the
authoritative checker run, and `automation.agent_tools` for evidence discovery
and replay diagnosis.  Do not compose a replacement checker invocation or
modify trusted validation code.

The operator deadline is authoritative only through
`python3 -m automation.campaign_guard status --state $EU4_CAMPAIGN_STATE`.
Never report a deadline stop unless that status shows no remaining time or a
campaign action is refused with `kind=deadline`; an unsupported correction path,
a missing observation or an environment restriction is reported as its actual
cause.  Diagnostic reports belong under `analysis/m7/reports/`.

## Required workflow

1. Verify the checker against the immutable trusted seal.  Record the current
   exact prefix and the earliest verified divergence before changing anything.
   Do not substitute reconstructed OpenGL, synthetic references,
   candidate-generated pixels or final-frame-only comparisons.
2. Investigate the earliest divergent event first.  Trace its renderer
   operation, translated shader, GPU resources, geometry and GL/Metal state.
   Determine whether the cause is local or inherited from earlier state.
3. Implement the smallest justified correction in an authorized candidate path.
   Preserve the renderer architecture; no event-number, pixel or fixture special
   cases.  A shader-translation workaround that belongs in the trusted infra is
   not a candidate change: either implement an equivalent renderer-side
   correction (the renderer compiles translated MSL) or report the blocker with
   its actual cause.
4. Rebuild the modified candidate from source, execute it through the existing
   trusted Metal replay, and submit the correction to the independent checker.
   Never declare success from the candidate's own output, a screenshot or a
   matching final image.
5. If events 0..3 are exact, stop and report the milestone.  Do not pursue
   event 4 or later in this campaign.
6. If a correction introduces an earlier regression, address or revert it.  If
   the evidence cannot support a causal correction, stop with the earliest
   verified divergence and the exact missing observation.

## Success criteria

All four events match the original EU4 OpenGL observations exactly, every
applicable channel is equal, the comparison is independently attributable to
actual Metal execution, and the checker reports a qualified prefix 0..3 with an
authentic sealed reference.  The event-20 target remains available through the
unchanged `campaigns/eu4-m7-event20` configuration.

Do not merge, push to `main`, modify historical seals or relax exactness.
