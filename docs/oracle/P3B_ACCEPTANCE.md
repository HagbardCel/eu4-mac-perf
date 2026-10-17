# P3-B acceptance memo

**Status:** acceptance conditions recorded; the single declared evaluation has not
been performed. **No P3-B result is asserted here yet.** The binding that the run
will bind is implemented and tested offline; the run itself is published only
once, after its pre-registration is on `main`.

This memo is the acceptance record for the P3 gate. The design it references is
authoritative in [P3_SLICE.md](P3_SLICE.md); the phase contract is in
[PLAN_OF_RECORD.md](PLAN_OF_RECORD.md).

## What the gate asks

A **real** OpenCode agent must repair a supplied defective candidate using Oracle
diagnostics, and then pass a held-out verdict, under controller-enforced abort,
budget and receipts. PR-A proved the deterministic half with an in-process stub.
PR-B is the half that a stub cannot prove.

## Acceptance conditions

A run is accepted only if **all** of the following hold, each re-checked by a
model-free guard over the published artifact rather than trusted from the run.

| # | Condition | How it is checked |
| --- | --- | --- |
| C1 | The pinned binary, model, variant and effective configuration are bound in the sealed receipt | `check_oracle_receipt.py` cross-checks the binding against the pre-registration |
| C2 | The pinned process enforces its configured restrictions on its **direct** tools | The pilot records a real `permission.rejected` and the pinned catalog; the memo distinguishes denial from absent tool, refusal and unrelated failure |
| C3 | Held-out fixtures are absent from the workspace and never intentionally exposed | The workspace is candidate-only; the five tools serve only the visible corpus |
| C3b | No held-out material reached any agent-visible surface | A `hid-*` id or held-out expected-value fingerprint in any tool response, workspace file or captured event fails the binding |
| C4 | The call count is ledger-authoritative and the stream proves a successful `oracle_inspect` and `oracle_test` | The sanitized event summary is reconciled with the ledger |
| C5 | A real repair: final ≠ defective, visible **and** held-out pass, certification ran | Seal, then CI replay of both suites over the committed artifact |
| C6 | The receipt is sealed and the artifact replays under a model-free guard | `check_oracle_receipt.py` |
| C7 | The run is bounded and not aborted | `execution.abort_reason` is null; the process groups were reaped |
| C8 | The pre-registration was pushed before the run | `docs/oracle/preregistration/` precedes `docs/oracle/receipts/` in history |
| C9 | Scope is stated honestly | This document |

## What acceptance would and would not mean

Acceptance would mean that, on a frozen 22-fixture synthetic slice, a pinned model
in a pinned harness **changed a defective candidate so that it matched the
reference behaviour on 12 visible and 10 held-out fixtures it never saw**, and
that an independent, model-free replay of the committed artifact reproduces that
verdict.

It would **not** mean:

* that 22 fixtures imply universal equivalence - they sample the slice's input
  space, not all of it;
* that a certified candidate is *proved repaired* - it is a candidate that passed
  a bounded differential check;
* that the holdout can be spent again - it is spent exactly once by the single
  declared evaluation, and CI's replay re-verifies *that same* evaluation rather
  than buying a new one;
* that the agent was *sandboxed* - descendant termination is containment, not an
  OS sandbox, and the MCP server still imports the host repository. Hard isolation
  is PR-F work.

## Honest negative

If the single declared run does not pass, that is recorded as an honest negative:
the holdout is not spent again, no fresh corpus is substituted, and no other model
is shopped for. A failure is a real result about the pin, and is reported as such.

## Results

_Pending the run. This section will carry the run id, the receipt digest, the
binding (binary, model, configuration, prompt and event digests), the observed
permission evidence, and the replay outcome._
