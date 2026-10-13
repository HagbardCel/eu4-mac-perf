# R0 gate memo (live session — PR B)

This memo records whether R0-M evidence supports **`candidate_for_lossless_skip`** scenarios and refreshes CPU/stack baselines. It does **not** authorize skipping `CInGameIdler::Render` — R1 must prove invalidation and correctness.

## Session

| Field | Value |
|---|---|
| Run directory | `results/20261012T210806Z-r0-measurement/` |
| Fixture / save SHA | `a04bc2e86eeda8025d7a914efb11b18b82dea423631fdcf48c50471578369607` |
| GOG executable SHA | `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` |
| Manifest | `manifest.json` (`status: incomplete_gates`) |
| `thread_cpu_backend` | `libproc` |
| Thread CPU preflight | offline + target entries in manifest |
| Acquisition commit | `a9048e19e80eb391feea91f3890940a9307dfd9d` (PR A / `r0-m-measurement-pr-a`) |

## Evidence matrix (scientific interpretation)

Raw `acceptance_gates` in `manifest.json` are preserved. Several `true` values are **policy bypasses** when `unsupported_drawable` is set—not passed pixel/census experiments.

| Evidence | Result | Notes |
|---|---|---|
| Frame readback | **Failed** | `UNSUPPORTED_DRAWABLE`; conservative dimension discovery |
| Mode provenance | **Passed** | ACK / control generations in probe log |
| Pixel equality | **Not evaluated** | Epoch C not run |
| Classification | **Not evaluated** | Frame evolution skipped |
| Thread CPU | **Passed** | Epoch A reconcile residual 0.14 ms/s |
| Stack attribution | **Passed (requalification)** | PR #43 column-depth parser; 1744 rows, root 791; see [`r0-requalification-20261012T210806Z.json`](evidence/r0-requalification-20261012T210806Z.json) |
| Positive control | **Not evaluated** | Skipped with drawable |
| R1 decision | **No candidates evaluated** | Dirty-state **unresolved** |

## Candidates for lossless skip (screening only)

No scenarios were evaluated for lossless frame skipping. There is insufficient evidence either to establish or to exclude candidates.

See [`analysis/evidence/r0-frame-evolution-20261012T210806Z.json`](evidence/r0-frame-evolution-20261012T210806Z.json) (`screening_performed: false`, `candidate_for_lossless_skip: null`).

## CPU baseline (epoch A)

| Metric | Value |
|---|---|
| Process CPU-ms/s | 1065.28 |
| Main-thread CPU-ms/s | 773.58 (~72.6% of process) |
| Thread-total CPU-ms/s | 1065.13 |
| Reconciliation residual | 0.14 ms/s (`reconcile_ok`) |
| Readiness swap rate | 57.25 swaps/s |

Reproducibility: [`results/20261012T210806Z-r0-measurement/epoch_a_process_cpu_samples.json`](../results/20261012T210806Z-r0-measurement/epoch_a_process_cpu_samples.json) plus local `powermetrics.pliststream` SHA-256 in that file.

Instrumented paused-Venice baseline; not an uninstrumented EU IV overhead estimate.

## Stack attribution (epoch B)

[`epoch_b.sample.txt`](../results/20261012T210806Z-r0-measurement/epoch_b.sample.txt) committed. Acquisition-time analyzer failed on Rosetta whitespace ([`r0-stack-analyzer-failure-20261012T210806Z.json`](evidence/r0-stack-analyzer-failure-20261012T210806Z.json)). **Offline requalification** on a working copy with PR #43 tooling yields `partial_complete`: [`r0-stack-report-20261012T210806Z.json`](evidence/r0-stack-report-20261012T210806Z.json), [`r0-main-thread-cost-model-20261012T210806Z.json`](evidence/r0-main-thread-cost-model-20261012T210806Z.json). Sample-weight `unattributed` share ≈22.9% — structurally valid but limited optimization guidance.

Retrospective process CPU (29 plist samples) is documented separately and does **not** replace the acquisition 27-sample gate: [`r0-cpu-reconciliation-20261012T210806Z.json`](evidence/r0-cpu-reconciliation-20261012T210806Z.json).

## Explicit non-claims

- Pass-level map cacheability (R3) is **not** inferred from screen-space diffs.
- Captured pixels are **OpenGL back buffer before flush**, not the WindowServer-composited desktop image.
- `UNSUPPORTED_DRAWABLE` is an instrumentation/policy limitation on dimension discovery, not proof that drawable readback is impossible on this platform.

## R1 recommendation

- [ ] Proceed to implement lossless idle skip for scenario(s): …
- [x] Do not implement whole-frame skip on present evidence; consider map-layer cache only after temporal analysis and drawable census.
- [ ] S2b (reduced idle rate) remains an explicit product decision.

**NO-GO** for whole-frame lossless skip implementation based on this session—not because opportunities are absent, but because screening was never performed.
