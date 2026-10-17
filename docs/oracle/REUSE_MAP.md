# Oracle reuse map (P1 audit)

**Decision legend:** `REUSE` (use as-is), `ADAPT` (reuse pattern/machinery, change schema or
scope), `REPLACE` (build new, supersede), `RETAIN_HISTORICAL` (keep as evidence, do not
maintain). No code is moved by this document; it records ownership before P2 migration.

> **P2 status (2026-10-17):** the migration below is landing. The blueprint bytes are preserved
> verbatim and tagged (`oracle-v2-blueprint-20261016`, see
> [BLUEPRINT_PROVENANCE.md](BLUEPRINT_PROVENANCE.md)); the prototype is copied byte-identically
> into `oracle/core.py` (module unchanged for P2), its tests are in `tests/test_oracle_core.py`,
> and `tooling/common/check_oracle_bundle.py` verifies preservation in CI. The table below
> remains the P1 ownership record.

## Existing assets → Oracle needs

| Existing asset | Oracle need | Decision | Note |
| --- | --- | --- | --- |
| `reverse_engineering/target.json` + `paths.py`, `export_identity.py` | Pinned binary/identity provenance | **REUSE** | SHA-256 + GOG path pinning is the same identity discipline the acquisition needs. |
| `reverse_engineering/macho_rva.py` | Instruction/byte identity | **REUSE** | `VA = image_base + rva` byte verification; reuse for hook-site and instruction evidence. |
| `reverse_engineering/renderer_extract.py` | Content-addressed store + receipts | **ADAPT** | Create-only atomic publication (`os.link`), receipt identity, byte recomputation. Adopt the pattern; new Oracle schema/CAS. |
| `reverse_engineering/corpus.py` | Resumable acquisition + manifest archiving | **ADAPT** | Staging → validate → publish, `manifest --fresh` transactional archive. Model for recorder artifact sealing. |
| `reverse_engineering/renderer_scope.py`, `index_db.py`, `search.py`, `call_graph.py` | Static evidence for RE0 selection | **REUSE** | Frozen renderer scope + SQLite search supply RE0 candidates; do not duplicate. |
| `benchmark/frame_model_evidence.py`, `frame_model_gates.py` | Fixture sealing + verification gates | **ADAPT** | Seal/verify + `INCOMPLETE_*` fail-closed semantics reuse; fixture schema is new. |
| `benchmark/r0_measurement_session.py`, `r0_probe.py`, `cpu_reconcile.py`, `thread_cpu_*.py` | Epoch coordinator + invocation accounting | **ADAPT (co-design)** | R0-M continues independently; the Oracle adds a **separately-qualified** epoch. Measurement contracts are not merged. **P4.8** validates the R0-M control-word protocol offline (`oracle/recorder/epoch.py`), cross-checked field by field against `benchmark/eu4_r0_control.py`; R0-M's measurement path is untouched. |
| `benchmark/eu4_benchmark.py` | Authorized acquisition harness | **ADAPT** | Operator authorization + path guards + preflight; extend for the consolidated launch. |
| `benchmark/eu4_frame_counter.c`, `eu4_diagnostic.c`, `eu4_*_observer*` | Hook/instrumentation patterns | **ADAPT** | C instrumentation and detour patterns inform PR-C hook packs; the Oracle recorder is new. |
| Prototype `oracle_proto/core.py` (`ObservationLedger`, `compare_fixture`) | Accounting invariant + comparator skeleton | **ADAPT** | The four-way invariant is already encoded; promote into `oracle/store` + `oracle/compare`. |
| `tooling/common/check_doc_links.py`, `check_forbidden_tracked_paths.py` | CI guards | **REUSE** | Extend to the Oracle tree + manifest in P2 (not this PR). |
| `tests/` (flat `unittest`, `PYTHONPATH=benchmark:.`) | Portable test convention | **REUSE** | Oracle tests join `tests/` in P2. |
| `results/20261003T210202Z-intrusive-diagnostic/` sealed capsule | Retention model for capture store | **RETAIN_HISTORICAL** | Precedent for immutable sealed evidence; capture store follows the same policy. |
| `benchmark/` retired experiments (state-cache, sampler-uniform, border, observer) + `docs/wp*.md` | — | **RETAIN_HISTORICAL** | Preserve negative results and provenance; no deletion. Archival happens only after consumer checking in a later phase. |
| `reverse_engineering` corpus pipeline + renderer snapshot | Static evidence base | **REUSE** | Mandatory: keep the full-corpus pipeline and sealed snapshot validators live. |

## Ownership boundary: Oracle infrastructure vs RE0–RE3

| Oracle infrastructure (this tree) | Function lifecycle (reconstruction contract) |
| --- | --- |
| Recorder, counters, dedup, capture store, CAS, service, epochs | RE0 select/characterize, RE1 faithful C++ reconstruction, RE2 differential validation |
| Produces fixtures + comparator | Consumes them in RE2 |
| Infra PRs use descriptive names | RE0–RE3 terminology retained; RE3 optional |

No third reconstruction taxonomy is introduced. PR #44's three comparison modes
(faithful / GL-optimization / backend-redesign) are the equivalence hierarchy; the
Oracle's observable-domain adequacy is expressed in those terms.

## Deliberate non-goals in this audit

- No code moved, renamed, or deleted.
- No rewrite of the canonical strategy Mission in this PR (coordinated with PR #44).
- No new evidence-management system built to duplicate `reverse_engineering` provenance.
