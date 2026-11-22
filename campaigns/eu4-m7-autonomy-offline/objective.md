# Offline M7 autonomous correction qualification

You are validating the M7 autonomous OpenGL-to-Metal reconstruction workflow.

The candidate renderer in this worktree contains a deliberately introduced
rendering discrepancy. Diagnose it using the existing captured reference
evidence, identify the smallest causal renderer correction, implement it, and
submit the result to the independent checker.

The fixture is a derived offline event subset (original events 10, 15, 17) with
a trusted copied reference outside this worktree. The acceptance scope is the
exact prefix 0..2; it is narrower than the production event-20 campaign and the
checker reports that scope explicitly.

## Rules

- Work autonomously across native DSH goal rounds.
- You may modify only authorized candidate renderer/test paths.
- Use focused tests and trusted replay executions.
- Never modify original reference evidence, the checker, the campaign limits or
  its receipts.
- If the checker rejects the correction, use its diagnostics to revise your
  hypothesis and continue while a justified experiment remains.
- Do not claim success based on your own screenshots or reports. Success
  requires an independent checker pass on actual GPU output produced from your
  modified source.
- Do not merge. Stop with explicit evidence if the correction cannot be
  justified, the required observation or environment is unavailable, or the
  operator's execution deadline has passed.

## Acceptance

The independent checker is
`campaigns/eu4-m7-autonomy-offline/accept.py`. A pass means:

- the candidate worktree/revision/policy is verified;
- the candidate renderer is built from its source snapshot by the trusted
  toolchain;
- the trusted execution runs against the immutable copied reference;
- events 0, 1 and 2 are all exact;
- no event is excluded or invalid;
- the source/build/host/output digests are bound in the receipt.

The canonical event-20 acceptance criterion is unchanged and is not used by
this offline smoke campaign. This fixture qualifies the mechanism only; it
does not establish original-frame equivalence.

## Experiment policy

There is no replay, correction, validation or goal-round quota. Before an
expensive GPU experiment, review the existing evidence, state the precise
evidence gap and what the experiment will establish, prefer cheaper/narrower
checks, and reuse an existing result when the same deterministic experiment was
already completed. A repeated failure is evidence for reassessment, not an
automatic stop.
