# M7 authentic exact-prefix campaign

Objective: establish exact original-EU4-OpenGL versus Metal framebuffer equality
for every event from event 0 through the campaign's configured target of one
authenticated, sealed captured frame.

The original EU4 OpenGL process is the only oracle.  The reconstructed OpenGL
control is optional diagnostic evidence and is never the reference.  The
configured target comes from the campaign's acceptance command
(`--target-end N`); this objective is shared by every successive prefix
milestone.  The independent checker owns the verdict.

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

The operator deadline is authoritative only through
`python3 -m automation.campaign_guard status --state $EU4_CAMPAIGN_STATE`.
Never report a deadline stop unless that status shows no remaining time or a
campaign action is refused with `kind=deadline`; an unsupported correction path
or a missing observation is reported as its actual cause.  Diagnostic reports
belong under `analysis/m7/reports/`.

## Evidence convention

Use the trusted reference root and sealed frame recorded by the selected
campaign sidecar.  The checker verifies the seal, the event stream, the
observation file, the shader receipt binding, the published checkpoint request
and every applicable colour channel of every observation.  The channel scope
comes from the sealed attachment identity; a candidate cannot narrow it.  Do not
copy, relabel or combine observations from another seal.  Do not edit the
checker, `automation/gfx_prefix_check.py`,
`automation/gfx_independent_replay.py`, `automation/gfx_observation.py` or
`automation/m6_frame.py`.

## Sequence

1. Inspect the existing sealed evidence before launching: the historical M7
   pilot and the latest OpenGL-control experiment. Record identities and the
   first confirmed divergence. They are separate observations.
2. Run the synthetic drawable-format observer and the focused offline tests.
   Do not launch EU4 if the observer is incomplete or the prefix tests fail.
3. Live capture is a separate, operator-controlled preparation stage: an
   `autonomous_reconstruction` campaign must not launch EU4, and a
   `controlled_capture` campaign may launch only with an operator-issued
   one-use authorization. If the existing evidence is insufficient, stop with
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
8. If the defect belongs to a component outside the candidate's authorized
   scope (for example trusted shader translation or capture instrumentation),
   report the genuine candidate-scope limitation and the exact component. Do
   not compensate by changing unrelated renderer behavior; a correction belongs
   in the component that owns the defect.

## Stop conditions

Stop on any of: verified success for the configured target scope; a genuine
evidence gap that cannot currently be observed; an unavailable or unsafe
environment; an explicit operator cancellation; or the operator's execution
deadline as reported by `campaign_guard`. A first divergence does not end the
campaign after a correction exposes the next one, and a repeated failed
hypothesis is a reason to reassess rather than a quota stop. Do not merge, push
to `main`, modify historical seals, or relax exactness.
