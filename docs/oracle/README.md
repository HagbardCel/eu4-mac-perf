# EU IV Oracle — ground-truth acquisition and reconstruction

**Status:** the P3 slice is **complete and on `main`**, released as annotated tag
`oracle-v2-p3b-20261017` (merge `f751555`, PR #59): the deterministic
infrastructure under `oracle/slice/`, the pinned OpenCode **binding**
(`oracle/slice/opencode.py`) with its guarded, model-free CI re-verification
(`tooling/common/check_oracle_receipt.py`), and the single declared P3-B
acceptance run `p3b-acceptance-20261017`, which **certified** (visible 12/12,
held-out 10/10) and whose sealed receipt is published under
`docs/oracle/receipts/`. The program is now in the **P4** stage: a
function-agnostic, policy-pluggable recorder plus a synthetic target, then a
synthetic graphics replay harness (**P4b**). The recorder core is implemented and
merged (**P4r-B**: exact capture accounting, a declared capture policy, offline
finalization and concurrency); the resource registry/closure and R0-M protocol
compatibility are merged (**P4r-C**); and the independent synthetic graphics replay
is merged (**P4b**). No EU IV launch is authorized until P4/P4b pass.
This
directory is authoritative for **capture/validation infrastructure** only; the
function-level reconstruction lifecycle stays in the reconstruction contract
[`../renderer-reconstruction-and-differential-validation.md`](../renderer-reconstruction-and-differential-validation.md)
(merged in PR #44).

## Purpose

Establish correct ground truth for automated, agent-driven C++ reconstruction of large
parts of the EU IV graphics stack. The Oracle (capture + validation infrastructure)
supplies the fixtures, comparator and service that the function-level RE0–RE3 lifecycle
consumes. The project's power/heat objective is the downstream consumer of that work.

## Documents

| Document | Role |
| --- | --- |
| [PLAN_OF_RECORD.md](PLAN_OF_RECORD.md) | Authorized P0–P5 execution sequence and gates |
| [REUSE_MAP.md](REUSE_MAP.md) | REUSE / ADAPT / REPLACE / RETAIN_HISTORICAL decisions over existing assets |
| [BASELINE_P0.md](BASELINE_P0.md) | Frozen baseline record (revision, checks, pre-existing failures) |
| [P3_SLICE.md](P3_SLICE.md) | P3 synthetic slice: trust domains, contracts and PR-A acceptance criteria |
| [P4_RECORDER.md](P4_RECORDER.md) | P4 recorder: record schema, canonicalization, evidence store and offline gates |
| [P4B_REPLAY.md](P4B_REPLAY.md) | P4b: independent synthetic graphics replay — sealed frame captures, closure and fail-closed reconstruction |
| [BLUEPRINT_PROVENANCE.md](BLUEPRINT_PROVENANCE.md) | Preserved v2 blueprint bytes, hashes and tag (historical reference) |
| [../repo-map.md](../repo-map.md) | Tracked-path inventory and lifecycle labels (P1 audit) |
| [../repo-audit-context.json](../repo-audit-context.json) | Audit identity triple (source snapshot, policy, tools); governs `docs/repo-{inventory,edges,lifecycle}.csv` |
The blueprint that this program refines is preserved verbatim at
`docs/proposals/EU4_Oracle_v2/` (annotated tag `oracle-v2-blueprint-20261016`, provenance in
[BLUEPRINT_PROVENANCE.md](BLUEPRINT_PROVENANCE.md)). Its executable seed now lives in the
`oracle/` package (`oracle/core.py`, byte-identical to the preserved prototype) with tests in
`tests/test_oracle_core.py`. See [PLAN_OF_RECORD.md](PLAN_OF_RECORD.md) for the phase sequence.

## Ownership model

| Layer | Owner document | Scope |
| --- | --- | --- |
| Overall strategy | [`../eu4_recommended_strategy.md`](../eu4_recommended_strategy.md) | Mission and priorities |
| Function lifecycle | [`../renderer-reconstruction-and-differential-validation.md`](../renderer-reconstruction-and-differential-validation.md) (merged, PR #44) | RE0–RE3 per-function reconstruction and oracle-strength comparison modes; RE3 optional |
| Capture/validation infrastructure | this directory (`docs/oracle/`) | Recorder, deduplication, fixtures, comparator, service, epochs |

Oracle infrastructure produces fixtures and a comparator that **RE2** consumes; RE0–RE3
remains the per-function lifecycle and does not define the recorder.

## Reconciliation actions

1. **PR #44 — done:** the reconstruction contract was merged (PR #44, merge `b7773f9`)
   after being reconciled onto `main`: the stale "open PR #43" language now describes the
   merged measurement infrastructure, the contract acknowledges the full renderer corpus
   and the graphics-subsystem endpoint, and its metadata header is whitespace-clean. The
   contract now lives on `main`; no competing copy exists.
2. **Canonical Mission update — done:** `../eu4_recommended_strategy.md` names the gated
   RE0–RE3 workstream in its Mission, and `../status.md` records ground-truth acquisition
   plus reconstruction as the primary next-phase capability.
3. **Seed migration (P2) — done:** the original `../proposals/EU4_Oracle_v2/` bytes are
   preserved verbatim and tagged (see [BLUEPRINT_PROVENANCE.md](BLUEPRINT_PROVENANCE.md)); the
   prototype is copied byte-identically into `oracle/` and its tests join `tests/`. The blueprint
   stays the historical reference while `docs/oracle/` remains authoritative; see
   [REUSE_MAP.md](REUSE_MAP.md).
