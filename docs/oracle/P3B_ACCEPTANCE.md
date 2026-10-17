# P3-B acceptance memo

**Status:** the single declared evaluation has been performed and **certified**,
and its sealed receipt is published and re-verified by a model-free guard. Run
`p3b-acceptance-20261017`; see [Results](#results). The design it references is
authoritative in [P3_SLICE.md](P3_SLICE.md); the phase contract is in
[PLAN_OF_RECORD.md](PLAN_OF_RECORD.md).

## What the gate asks

A **real** OpenCode agent must repair a supplied defective candidate using Oracle
diagnostics, and then pass a held-out verdict, under controller-enforced abort,
budget and receipts. PR-A proved the deterministic half with an in-process stub.
PR-B is the half a stub cannot prove.

## Acceptance conditions

A run is accepted only if **all** of the following hold, each re-checked by a
model-free guard over the published artifact rather than trusted from the run.

| # | Condition | How it is checked |
| --- | --- | --- |
| C1 | The pinned binary, model, variant and effective configuration are bound in the sealed receipt | `check_oracle_receipt.py` cross-checks the binding against the pre-registration |
| C2 | The pinned process enforces its configured restrictions on its **direct** tools | The pilot records a real structured denial and the pinned catalog; the memo distinguishes denial from absent tool, refusal and unrelated failure |
| C3 | Held-out fixtures are absent from the workspace and never intentionally exposed | The workspace is candidate-only; the five tools serve only the visible corpus |
| C3b | No held-out material reached any agent-visible surface | A `hid-*` id or held-out expected-value fingerprint in any tool response, workspace file or captured event fails the binding |
| C4 | The call count is ledger-authoritative and the stream proves a successful `oracle_inspect` and `oracle_test` | The sanitized event summary is reconciled with the ledger |
| C5 | A real repair: final ≠ defective, visible **and** held-out pass, certification ran | Seal, then CI replay of both suites over the committed artifact |
| C6 | The receipt is sealed and the artifact replays under a model-free guard | `check_oracle_receipt.py` |
| C7 | The run is bounded and not aborted | `execution.abort_reason` is null; the process groups were reaped |
| C8 | The pre-registration was pushed before the run | `docs/oracle/preregistration/` precedes `docs/oracle/receipts/` in history |
| C9 | Scope is stated honestly | This document |

## Results

**Run `p3b-acceptance-20261017` — certified.** Receipt digest
`39ed383b56d8e7f204fac973a75813cf47da73b9cabc3968837784f4f83af0c2`, published at
[receipts/p3b-acceptance-20261017.json](receipts/p3b-acceptance-20261017.json)
(next to its [artifact](artifacts/ebc277593a225c0d67d2541eef99ca3b24aa5bdb69ed23bcc845c3c4f1853cee.c),
[sanitized event summary](agents/p3b-acceptance-20261017.events.json) and
[pre-registration](preregistration/p3b-acceptance-20261017.json)), and re-verified
- seal, pre-registration cross-check, deterministic configuration
re-materialization, integrity-gated replay of both corpora - by
`tooling/common/check_oracle_receipt.py`.

| Binding | Value |
| --- | --- |
| Binary | `opencode` v2.0.30, sha256 `75617e69…` |
| Model | `opencode/deepseek-v4.1-flash-free#high` |
| Configuration | template `88c8688e…`, materialized `cc4be7af…` |
| Prompt | `4c907986…` |
| Events | raw sha256 `4146fb4e…` (61052 bytes); summary `2f58d715…` |

| Outcome | Value |
| --- | --- |
| Repair | `252ab6e8…` (defective) → `ebc27759…` (final), distinct |
| Visible | PASS (12/12) |
| Held-out | PASS (10/10) |
| Certification | `ran`; `candidate_verified` true |
| Accounting | ledger `granted = 4`, agent `tool_calls = 4`, in-process `0` |
| Tool evidence | `oracle_inspect`, `oracle_fixtures`, `oracle_coverage` and `oracle_test` all completed |
| Bounds | one attempt, 27.4 s wall, 300 s finalization budget, no abort |

### Criterion by criterion

| # | Outcome |
| --- | --- |
| C1 | Bound. The binary, model, variant, prompt and both configuration digests are in the sealed receipt, and the guard re-materializes the configuration from the tracked template plus the pre-registered substitutions. |
| C2 | **Pilot-attested, not run-attested.** The acceptance run produced no denial, because the agent stayed inside the permitted surface. The enforcement evidence is the pilot's machine-observed structured denial (a direct `read` of an external path → `status == "error"` with `Permission denied: external_directory`, while `read` stayed present), the absence of `shell`, `webfetch`, `websearch`, `subagent` and `browser` from the catalog, and the pinned permission block bound in the effective configuration. |
| C3 | The workspace holds only `candidate.c`, `synth.h`, `fixtures/visible/` and `README.md`, and the pilot showed `glob **/held_out/*` returns nothing from the agent's project root. |
| C3b | No held-out identifier or fingerprint appeared in any agent-visible surface. The binding fails closed if one does. |
| C4 | Ledger-authoritative: `granted = 4`, agent `tool_calls = 4`, in-process `0`. The event stream proves completed `oracle_inspect` and `oracle_test` (and `oracle_fixtures`, `oracle_coverage`). |
| C5 | Real repair: the final candidate differs from the defective one, visible and held-out both pass, `certification == "ran"`, `candidate_verified` true. |
| C6 | The sealed receipt and the committed artifact replay under the model-free guard, cross-checked against the pre-registration that was pushed before the run. |
| C7 | Bounded and not aborted: `abort_reason` null, one attempt, 27.4 s. |
| C8 | The pre-registration was pushed as `8901593` before the run. |
| C9 | See below. |

## What acceptance means and does not mean

Acceptance means that, on a frozen 22-fixture synthetic slice, a pinned model in a
pinned harness **changed a defective candidate so that it matched the reference
behaviour on 12 visible and 10 held-out fixtures it never saw**, and that an
independent, model-free replay of the committed artifact reproduces that verdict.

It does **not** mean:

* that 22 fixtures imply universal equivalence - they sample the slice's input
  space, not all of it;
* that a certified candidate is *proved repaired* - it is a candidate that passed
  a bounded differential check;
* that the holdout can be spent again - it was spent exactly once by the single
  declared evaluation, and CI's replay re-verifies *that same* evaluation rather
  than buying a new one;
* that the agent was *sandboxed* - descendant termination is containment, not an
  OS sandbox, and the MCP server still imports the host repository. Hard isolation
  is PR-F work;
* that the denial evidence comes from the acceptance run itself - C2 is
  pilot-attested (above).

## Disclosures

* **A visible-only rehearsal preceded the declared run.** The same pinned model
  and the same repair prompt were run once in a non-accepting mode, which ends in
  a code-level `gave_up` and therefore cannot certify and cannot spend the
  holdout. It reached visible PASS (12/12). The declared run is an independent
  session; its held-out corpus was spent exactly once, by it, and no fresh corpus
  was introduced and no other model was tried.
* **A real isolation defect was found and fixed before the run.** The pilot showed
  that OpenCode resolved its project directory from `PWD`, which `subprocess` does
  not update, so the agent's project root had been this repository - placing the
  held-out corpus inside the agent's own file-tool scope. The fix pins `PWD` to the
  workspace. See [P3_SLICE.md](P3_SLICE.md); the run above was performed after it.
* **The pre-registration could not have covered the fix.** It was computed from the
  corrected tree, and the run enforces the declared binary digest, version, model,
  run id and staging root, refusing to proceed otherwise.
