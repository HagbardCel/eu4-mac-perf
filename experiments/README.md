# Experiments

Isolated performance hypotheses: mutable protocols, implementations, and stopping criteria. Promoted optimizations eventually move to `patches/` with independent validation.

During migration, experiment code remains under `benchmark/`:

| Experiment | Current location |
| ---------- | ---------------- |
| State cache | `benchmark/eu4_state_cache.py` |
| Sampler uniforms | `benchmark/eu4_sampler_uniform.py` |
| Border multidraw / submission | `benchmark/eu4_submission_*.py`, `submission_experiment.py` |
| Frame / idle pacing | `benchmark/eu4_idle_pacer.py`, `eu4_frame_model.py` |

The historical M0a scaffold proposed a standalone x86-64 Rosetta harness to
trace one representative mesh draw, reproduce it in Metal with translated
MSL, benchmark equivalent GL and Metal work, and draft a command IR. Its
Makefile was a placeholder and implementation depended on the R0-A inventory.
[Scaffold README](https://github.com/HagbardCel/eu4-mac-perf/blob/6710a341d864466d7b0ab2d297ab513922d582b6/benchmark/m0a/README.md) ·
[Placeholder Makefile](https://github.com/HagbardCel/eu4-mac-perf/blob/6710a341d864466d7b0ab2d297ab513922d582b6/benchmark/m0a/Makefile)

Observations belong in `results/` (future `measurements/runs/` registry in PR B).
