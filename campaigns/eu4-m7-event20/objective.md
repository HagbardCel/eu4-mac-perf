# M7 event-20 exact-prefix campaign

Objective: establish exact original-EU4-OpenGL versus Metal framebuffer equality
for every executed operation from event 0 through event 20 of one authenticated,
sealed captured frame.

The original EU4 OpenGL process is the only oracle.  The reconstructed OpenGL
control is optional diagnostic evidence and is never the reference.  The
independent checker `campaigns/eu4-m7-event20/accept.py` owns the verdict.

## Governance

There is no replay, correction, validation or goal-round quota. Live EU4
launches are controlled separately: this campaign is `autonomous_reconstruction`
by default, and a capture needs a `controlled_capture` campaign plus an
operator-issued one-use launch authorization. An operator-configured execution
deadline bounds unattended cost; it is not a per-experiment budget.

Before an expensive experiment, review the existing evidence, state the precise
evidence gap and what the experiment will establish, prefer cheaper/narrower
controls, and reuse a completed deterministic experiment instead of repeating
it. A repeated failed hypothesis causes reassessment, not automatic termination.

## Evidence convention

All live evidence for this campaign stays under

    .local/m7/event20-final2/evidence/frame     (sealed original-GL frame)
    .local/m7/event20-final2/evidence/shadow    (in-process Metal replay + checkpoints)

The checker verifies the seal, the event stream, the observation file, the
shader receipt binding, the published checkpoint request and every applicable
colour channel of every observation.  The channel scope comes from the sealed
attachment identity; a candidate cannot narrow it.  Do not copy, relabel or
combine observations from another seal.  Do not edit the checker,
`automation/gfx_prefix_check.py`, `automation/gfx_independent_replay.py`,
`automation/gfx_observation.py` or `automation/m6_frame.py`.

## Sequence

1. Inspect the existing sealed evidence before launching: the historical M7
   pilot and the latest OpenGL-control experiment. Record identities and the
   first confirmed divergence. They are separate observations.
2. Run the synthetic drawable-format observer and the focused offline tests.
   Do not launch EU4 if the observer is incomplete or the prefix tests fail.
3. This campaign is `autonomous_reconstruction`: it cannot launch EU4. Live
   capture is a separate, operator-controlled preparation stage in a
   `controlled_capture` campaign, with one operator-issued authorization per
   launch. If the existing evidence is insufficient, stop with
   `requires_additional_evidence` and name the exact capture request (caller
   set, prefix scope, required observations) for the operator; do not attempt a
   capture from this objective and never construct a custom launcher or bypass
   the one-use launch guard.
4. Run the independent checker. Record the earliest verified divergence and
   preserve the sealed frame, Metal outputs, request, receipt, logs and the
   portable replay case before diagnosing.
5. Diagnose with the cheapest discriminating control that can distinguish the
   remaining mechanisms. State the question, the relevant evidence, the gap and
   the expected information before a GPU replay. Change only the smallest
   renderer component supported by that evidence, run focused tests, replay the
   same seal, and submit the proposed correction to `correction_accept`.
6. Repair the earliest independently verified divergence and continue to the
   next one while meaningful, justified investigation remains possible. Do not
   stop merely because an earlier correction exposed another divergence.
7. If the evidence cannot support a causal correction, stop with
   `requires_additional_evidence` and name the exact missing observation.

## Stop conditions

Stop on any of: verified success for the complete event-0-through-20 scope;
a genuine evidence gap that cannot currently be observed; an unavailable or
unsafe environment; an explicit operator cancellation; or the operator's
execution deadline. A first divergence does not end the campaign after a
correction exposes the next one, and a repeated failed hypothesis is a reason to
reassess rather than a quota stop. Do not merge, push to `main`, modify
historical seals, or relax exactness.
