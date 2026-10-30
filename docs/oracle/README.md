# EU IV Oracle — ground-truth acquisition and reconstruction

**Status:** the P3 slice is **complete and on `main`**, released as annotated tag
`oracle-v2-p3b-20261017` (merge `f751555`, PR #59): the deterministic
infrastructure under `oracle/slice/`, the pinned OpenCode **binding**
(`oracle/slice/opencode.py`) with its guarded, model-free CI re-verification
(`tooling/common/check_oracle_receipt.py`), and the single declared P3-B
acceptance run `p3b-acceptance-20261017`, which **certified** (visible 12/12,
held-out 10/10) and whose sealed receipt is published under
`docs/oracle/receipts/`. The program's **P4/P4b** stage is **complete and on
`main`**, released as annotated tag `oracle-v2-p4b-20261018` (merge `fdf0820`,
PR #66): the function-agnostic, policy-pluggable recorder plus a synthetic target
(**P4r-A**, **P4r-B**, **P4r-C** — the record schema and immutable evidence store;
exact capture accounting, a declared capture policy, offline finalization,
concurrency and bounded capture; and the resource registry/closure verifier with
offline R0-M protocol compatibility), and the independent synthetic graphics replay
harness (**P4b**). No EU IV launch is authorized until P4/P4b pass **and the applicable
offline half-gate passes** — the synthetic replay alone does not satisfy it. The
gate is sliced **P4.9-P4.11** and specified in [P4_INTEGRATION.md](P4_INTEGRATION.md);
**P4.9** (bounded resource-content capture and the versioned observation export) is
implemented. The remaining gate is **split by acquisition scope** (2026-10-18): the
**function gate** (function-oriented **P4.10**) authorizes **P5a** — the first
real-function acquisition — and the **frame gate** (**P4.11**, or a qualified
alternative) authorizes **P5b**. The workstream is **function-first**; P4.11 does not
block P5a. The post-D work is organized as parallel offline packages **E1**, **F1**,
**G1** and **G3a** (with **E2** audit preparation), while **F2**, **G2**, **G3b**,
**G4** and **H1** remain gated; see [PLAN_OF_RECORD.md](PLAN_OF_RECORD.md). This
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
| [P4_INTEGRATION.md](P4_INTEGRATION.md) | Pre-P5 offline integration gate: resource-content capture, the versioned observation export, materialization and declared `gfx-v2` translation |
| [ACQ_DIVERSITY_V1.md](ACQ_DIVERSITY_V1.md) | Historical diversity-aware draft (`acq-diversity/v1`); superseded for future sessions |
| [EXACT_DEDUP_V1.md](EXACT_DEDUP_V1.md) | Active P5a recorder (`exact-dedup/v1`), merged in PR #94: reusable pool, exact canonical dedup, runtime `max_unique` |
| [TRACE_V1.md](TRACE_V1.md) | `execution-observation/v1`: synthetic trace-to-fixture contract. **Historical TR3 preflight gate:** at that gate TR3 was not authorized. Its preflight passed at `0a24c90`. The preflight-only work order is [work-orders/TR3.md](work-orders/TR3.md). The work order records prerequisites and a proposed later experiment. That pass authorizes no implementation, EU4 launch, injection, live trace, or replacement. The implementation scope is [work-orders/TR3-IMPL.md](work-orders/TR3-IMPL.md). Writing its named paths waits for that work order to merge. That gate left an EU4 run unauthorized |
| [GENERIC_TRACE_V1.md](GENERIC_TRACE_V1.md) | `trace-target/v1`: one preselected function, descriptor only. GEN1 may start from [work-orders/GEN1.md](work-orders/GEN1.md). GEN2 and GEN3 do not start. No EU4 launch |
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
| Tier 1 certification | [`P4_INTEGRATION.md`](P4_INTEGRATION.md) | Sealed-session admission, certification result axes, coverage and promotion boundary |
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
