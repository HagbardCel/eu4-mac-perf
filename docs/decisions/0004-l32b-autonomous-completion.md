# 0004 — Autonomous L3.2b completion: fail-closed contracts and evidence-driven continuation

**Status:** accepted 2026-11-09. Immutable. Supersede with a later record.

**Scope:** the L3.2b-II completion campaign for the pinned GOG EU IV 1.37.5
x86-64 loading-screen draw at caller RVA `0xedfcd1`. This decision does not
authorize L3.3, Metal execution, pixel equivalence, or a general workflow
engine.

## Context

L3.2b-I established vertex bindings, vertex contents and the 64-byte matrix
from one authentic session. Ten required draw inputs remain missing. The merged
checker is fail-open for required names it does not know: an established field
without a validator is silently accepted. The reviewer campaign's acceptance
script returns `pass` for a structurally valid report even when its outcome is
`rejected`. Launch accounting is per campaign, so starting a new campaign resets
the effective budget.

The original-EU4 evidence needed for the remaining fields can be discovered
autonomously, but only if the evidence contract can be extended without letting
the implementer weaken its own acceptance criteria.

## Decision

1. **Fail-closed acceptance.** A new immutable checker revision
   (`automation/gfx_draw_accept_v2.py`) classifies each required input as
   `validated`, `provisional`, `missing` or `invalid`. No required input may be
   accepted without a field-specific validator. The v1 checker stays
   byte-identical because the completed L3.2b-I manifest pins it.
2. **Provisional checkpoints.** When structurally valid original evidence
   exists for one or more fields but no validator exists, the checker writes a
   durable, immutable checkpoint bundle outside the candidate worktree and the
   temporary packet directory. Partial checkpoints are permitted while other
   fields are still missing. A checkpoint is a qualification work order, never
   acceptance of L3.2b.
3. **Independently qualified contracts.** New validators are proposed in a
   separate campaign, executed in a separate process, and reviewed by an
   independent reviewer who authors field-specific adversarial cases. The
   validator must accept the checkpoint's original evidence and reject
   incorrect-but-structurally-valid evidence. Promotion is append-only and
   creates a new integration commit.
4. **Two identities.** Every campaign records a `candidate_base_commit` (the
   implementation worktree parent) and a `trusted_contract_commit` (the
   immutable checker, contract, tests and oracle used for acceptance). Trusted
   files are verified at the trusted revision; candidate changes are verified
   against the candidate base. Promotion combines the accepted histories in one
   integration commit; no active campaign is retargeted or rebased.
5. **One global launch ledger.** The workflow-level ledger is the only
   authority. Receipts are `reserved`, `started`, `completed`, `failed` or
   `abandoned`; every claim counts and there is no automatic refund. Capture
   selection reads the ledger, not a per-campaign mirror. The ledger is an
   authorization/accounting control, not a sandbox.
6. **Evidence-driven loop.** The coordinator maintains unresolved/provisional/
   validated fields and repeats investigation, qualification and continuation
   until every required field is validated. It then takes one fresh coherent
   original-game capture and obtains a final independent review. Contract
   versions are assigned as the work requires; there is no fixed
   shader → texture → state sequence.
7. **Scope of `packet_ready`.** The trusted qualification receipt's
   `packet_ready=true` means every source input required by the declared L3.2b
   replay contract is qualified. It does not assert framebuffer contents, GPU
   execution, pixel equivalence, a complete Metal frame, or L3.3 completion.

## Consequences

- The infrastructure bootstrap is a separate, thin PR. It includes no shader
  interception, gateway rewrite, generic mutation engine, 16-slot expansion,
  predefined contract versions, or automatic PR opening.
- The completion campaign may expand observation targets, payload bounds or
  record capacity only after measurement and with independent review.
- Original shader and texture bytes remain under `.local`; the repository
  tracks hashes, lengths, provenance and assumptions.
- A genuinely unresolved or unsafe original-engine ambiguity remains a human
  decision. The coordinator may attempt bounded additional research first.

## Non-goals

No L3.3, Metal execution, pixel equivalence, general workflow engine/MCP,
second Metal engine, copyrighted asset redistribution, silent model
substitution, or removal of the `_GfxInit` admission barrier without a narrow
qualified extension.
