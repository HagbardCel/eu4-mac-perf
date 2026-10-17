# Experiments

Isolated performance hypotheses: mutable protocols, implementations, and stopping criteria. Promoted optimizations eventually move to `patches/` with independent validation.

During migration, experiment code remains under `benchmark/`:

| Experiment | Current location |
| ---------- | ---------------- |
| State cache | `benchmark/eu4_state_cache.py` |
| Sampler uniforms | `benchmark/eu4_sampler_uniform.py` |
| Border multidraw / submission | `benchmark/eu4_submission_*.py`, `submission_experiment.py` |
| Frame / idle pacing | `benchmark/eu4_idle_pacer.py`, `eu4_frame_model.py` |
| Metal prototypes | `benchmark/m0a/` |

Observations belong in `results/` (future `measurements/runs/` registry in PR B).
