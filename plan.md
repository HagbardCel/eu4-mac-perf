# EU IV macOS performance — active plan

**Updated 2026-10-18.** This is the short project entry point. The **canonical architecture roadmap** is [docs/eu4_recommended_strategy.md](docs/eu4_recommended_strategy.md); the new **source reconstruction and differential validation gates** are [docs/renderer-reconstruction-and-differential-validation.md](docs/renderer-reconstruction-and-differential-validation.md).

## What the existing evidence supports

- **Phase C is frozen:** use the canonical intrusive diagnostic at [results/20261003T210202Z-intrusive-diagnostic/](results/20261003T210202Z-intrusive-diagnostic/). R-mode is the accepted baseline; C-mode counts are structural, **not trustworthy optimization timings**. Do **not** restart the intrusive profiler-qualification project.
- **Draw topology:** approximately 5,863 draws per paused Venice frame in intrusive screening, including approximately 2,774 mesh and 2,285 border; **do not infer proportional CPU-cost savings from draw counts**. See [rendering findings](docs/eu4_macos_rendering_findings.md).
- **Border hypothesis:** mode-0 border multi-draw is closed for paused Venice, and generic GL setter caching and simple mesh adjacency batching are not priority optimizations; avoid reopening these without genuinely new evidence.
- **Long-term ambition:** substantially reduce work on unchanged frames where full-resolution frame evolution permits it; improve active-scene cost using a better Gfx/command/resource architecture and potentially Metal. The ~80–90% static-work objective is **conditional**, not a promised or measured result.

## Active execution tracks (parallel where independent)

| Track | Immediate action | Gate |
|---|---|---|
| **R0-M measurement** | [PR #43](https://github.com/HagbardCel/eu4-mac-perf/pull/43) (measurement infrastructure) is **merged**; next, **separately** capture the approved paused-Venice live evidence in its evidence PR | Full-resolution frame-evolution, per-thread CPU, stack attribution; no timing claims from intrusive traces |
| **R0-A architecture** | Offline Gfx/OpenGL minimum-cut mapping; shader inventory; render-pass/depth graph | Tractable high-cost **vertical** domain, no assumed clean global backend seam |
| **RE0–RE3 reconstruction pilot** | **Function-first.** **RE0 now** on `_GfxSetConstantBuffer` (entry RVA `0x15ec36a`; 21 instructions, 0 selected direct callees): instruction-level contract, then a bounded offline original-code execution pilot (Ghidra P-code) to test the original binary as an offline behavioral oracle. Then faithful x86-64 C++20 (RE1), differential original-function oracle (RE2), reversible in-game replacement only if offline gates pass (RE3) | Faithful behavior before optimization; captured fixtures have bounded coverage; independent game safety gate |
| **M0a → M0b → M0c Metal research** | Continue standalone equivalent single-draw probe; then ordered command-stream/state-entropy analysis; then Metal-native resource/caller redesign replay | Do not block these studies on RE3 or a full renderer reconstruction |
| **R1/R2/R3/R4** | Pursue cursor/idle work if admitted by R0-M; offscreen map seam and interop; choose first B0/C0 domain by measured cost and pass/depth compatibility | Profiler-off whole-frame ABABA, exact/parity criteria where applicable, GPU/CPU safety |

## RE execution rule

**Understand → reconstruct faithfully → validate → integrate reversibly → optimize → compare.** Work bottom-up **but demand-driven**. Use Ghidra disassembly/P-code and symbols as evidence; the reconstructed C++ is not assumed accurate merely because it compiles. Prefer a callable original-function oracle; distinguish exact GL command traces (faithful reconstruction) from semantic GL state/pixel comparison (optimized/Metal). The first RE pilot is **a feasibility test, not a return to GL state-cache optimization**.

Keep live EU IV launches rare: finish offline gates before any new session, reuse/batch bounded captures only where the measurement protocol remains valid, and retain an original-code fallback. A failed RE integration gate does not block offline M0 studies. The Oracle acquisition gate is split: **P5a** (function-level) is gated by the offline **function** gate alone; **P5b** (frame-level) by the frame gate ([Oracle plan of record](docs/oracle/PLAN_OF_RECORD.md)).

## Source of truth / historical record

- [Canonical rendering strategy](docs/eu4_recommended_strategy.md)
- [Renderer reconstruction validation plan](docs/renderer-reconstruction-and-differential-validation.md)
- [Cost-based alternatives ranking](docs/eu4_rendering_alternatives_ranked.md)
- [Phase-C evidence and qualifications](docs/phase-c-outcome-memo.md)
- [Historical roadmap](analysis/historical-roadmap.md)

## Phase-C verification command

`python3 benchmark/eu4_frame_model.py verify-evidence results/20261003T210202Z-intrusive-diagnostic --profile intrusive_complete_v1`
