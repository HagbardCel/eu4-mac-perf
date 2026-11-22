# M7 event-20 exact-prefix campaign

Objective: establish exact original-EU4-OpenGL versus Metal framebuffer equality
for every executed operation from event 0 through event 20 of one authenticated,
sealed captured frame.

The original EU4 OpenGL process is the only oracle.  The reconstructed OpenGL
control is optional diagnostic evidence and is never the reference.  The
independent checker `campaigns/eu4-m7-event20/accept.py` owns the verdict.

## Budgets

One EU4 launch, one newly sealed frame, one in-process Metal shadow execution,
two offline replays, one correction evaluation, 1800 seconds of wall clock and
zero automatic merges.  The campaign guard (`EU4_CAMPAIGN_STATE`) enforces
these; do not ask for a reset.

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
   pilot and the latest OpenGL-control experiment.  Record identities and the
   first confirmed divergence.  They are separate observations.
2. Run the synthetic drawable-format observer and the focused offline tests.
   Do not launch EU4 if the observer is incomplete or the prefix tests fail.
3. If the existing evidence is insufficient, make **one** managed capture with
   the exact-prefix mode enabled:
   `python3 -m automation.m6_frame capture --output .local/m7/event20/live
   --callers <the reviewed caller set> --min-draws 8 --max-draws 512
   --prefix-end 20 --seal --shadow --timeout ...`
   Use the managed capture infrastructure and the BetterDisplay background
   screen.  Never construct a custom launcher or bypass the one-use launch
   guard.  Restore `settings.txt` byte-for-byte and terminate the owned process,
   including on failure.
4. Run the independent checker.  Stop at the first verified divergence.  If a
   divergence is found, preserve the sealed frame, Metal outputs, request,
   receipt, logs and the portable replay case before diagnosing.
5. At most three bounded hypotheses, at most two discriminating tests each,
   preferring offline experiments on the sealed workload.  Change only the
   smallest renderer component supported by that evidence, run focused tests,
   replay the same seal, and submit the proposed correction to
   `correction_accept`.  Continue only if the checker authorizes continuation
   under the remaining budget.
6. If the evidence cannot support a causal correction, stop with
   `requires_additional_evidence` and name the exact missing observation.

## Stop conditions

A divergence at or before event 20 ends correction work at that event.  A
missing/invalid checkpoint, an unproven resource history, an unmatched event
identity, a forged or stale receipt, an exhausted budget, a repeated identical
failure, or an unrestorable environment all stop the campaign.  Do not merge,
push to `main`, modify historical seals, or relax exactness.
