# Current project status

Authoritative summary of what is established, qualified, and in flight. Operational commands live in [profiler and benchmark operations](reference/profiler-and-benchmark-operations.md).

## Closed

- **Phase C intrusive profiling and forensic investigation** — complete. Do not repeat live intrusive capture (`run --diagnostic-only`) merely to collect more diagnostic data.
- Sealed record: [Phase C capsule report](../results/20261003T210202Z-intrusive-diagnostic/report.md). Offline verification: [`analysis/frame-model-verification.md`](../analysis/frame-model-verification.md).

## Still usable (within qualification limits)

- **R-mode** segments and structural evidence from the Phase C capsule support baseline and contract interpretation.
- **C-mode** absolute timings are **not** quantitatively qualified for production claims (same tier as post-WP11 / Tier-1 admission failure).
- **Tier-1 / WP11** (`33339073`, failed v4 admission): profiler absolute timings are not production-qualified. See [live diagnostic measurement contract](live-diagnostic-measurement-contract.md) and [post-WP11 diagnostic roadmap](eu4-live-diagnostic-roadmap.md).

Future **targeted** measurements (including further R0-M work) remain possible when separately justified; this does not reopen the Phase C investigation.

## R0-M integrated session (`20261012T210806Z`) — partial only

[Results index](../results/INDEX.md), [validation](../results/20261012T210806Z-r0-measurement/validation.md), [R0-M protocol](r0-measurement.md):

- CPU reconciliation obtained in epoch A.
- Drawable census **not evaluated** (`unsupported_drawable` readback policy).
- Automated stack attribution failed on an unsupported `sample` formatting variant.
- Acquisition status **`incomplete_gates`**; frame staticity and lossless-skip candidates were **not** evaluated.

## Current technical direction

- Renderer **reconstruction**, **differential validation**, and **elimination of unnecessary rendering work** per the canonical [rendering strategy](eu4_recommended_strategy.md) ([navigation](roadmap.md)).
- **Function-first reconstruction (2026-10-18; amended 2026-10-23).** The acquisition gate is split: **P5a** (first real-function acquisition) and **P5b** (frame-level). The first target is `_GfxSetConstantBuffer` (21 instructions, 0 selected direct callees); RE0 (contract) and a bounded offline original-code execution pilot precede the function-oriented P4.10, and frame-level P4.11 no longer blocks P5a. The workstream is now **two-tier** — Tier 1 offline replay against qualified fixtures, Tier 2 live shadow replay in an isolated worker with the original authoritative — and **RE3 is deferred** until both tiers succeed. Stage 1 is **PR A1** (P4.9 export → sealed records and whole-version blobs) then **PR A2** (function-fixture contract); the next hook (PR B) is observation-only. Progress is measured by qualified real EU IV functions, not synthetic infrastructure volume. See the [Oracle plan of record](oracle/PLAN_OF_RECORD.md).
- **Next phase (primary capability):** ground-truth acquisition + agent-driven C++ reconstruction of the graphics stack, per the [Oracle plan of record](oracle/PLAN_OF_RECORD.md) ([program index](oracle/README.md)). The Oracle **P3** slice is complete and released (`oracle-v2-p3b-20261017`), and so is **P4/P4b** — the offline recorder plus synthetic target, then the synthetic graphics replay — released as annotated tag `oracle-v2-p4b-20261018`. **P4r-A**, **P4r-B** and **P4r-C** (the record schema and immutable evidence store; exact capture accounting, a declared capture policy, offline finalization, concurrency and bounded capture; the resource registry/closure verifier and offline R0-M protocol compatibility) and **P4b** (the independent synthetic graphics replay) are all merged. No EU IV launch is authorized until P4/P4b pass **and the applicable offline half-gate passes**; passing P4/P4b does not satisfy that gate. That gate is sliced **P4.9-P4.11** ([P4_INTEGRATION.md](oracle/P4_INTEGRATION.md)): **P4.9** — bounded resource-content capture, whole-version content semantics, content provenance, fail-closed malformed declarations and the versioned observation export — is implemented. The remaining gate is split by acquisition scope (see above): the function-oriented **P4.10** authorizes **P5a**, and **P4.11** authorizes **P5b**.
- Consolidated rendering knowledge: [macOS rendering findings](eu4_macos_rendering_findings.md), [alternatives ranking](eu4_rendering_alternatives_ranked.md).

## Reverse engineering (PR D tooling)

- **Ghidra Phase 1 automation** is in [`reverse_engineering/`](../reverse_engineering/README.md): headless import/inventory/decompile orchestration, SQLite search CLI, and census reports.
- **Local full export** requires `GHIDRA_INSTALL` and a maintainer run of D1–D5; committed aggregate reports are integration-fixture samples until `measurement_source` is `local_ghidra_export`.

## Pinned environment

GOG EU IV v1.37.5.0 x86-64 at `/Applications/EuropaUniversalisIV`, Venice paused fixture, documented in the [operations manual](reference/profiler-and-benchmark-operations.md).
