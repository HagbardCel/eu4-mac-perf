# EU IV macOS performance and rendering research

This repository measures and profiles the **GOG** macOS build of Europa Universalis IV (pinned x86-64 under Rosetta) and investigates rendering cost on Apple Silicon. It combines live measurement, static reverse engineering, and controlled experiments toward validated optimizations.

- **Current state:** [docs/status.md](docs/status.md)
- **Strategy (canonical):** [docs/eu4_recommended_strategy.md](docs/eu4_recommended_strategy.md) ([navigation index](docs/roadmap.md))
- **Full profiler and benchmark manual:** [docs/reference/profiler-and-benchmark-operations.md](docs/reference/profiler-and-benchmark-operations.md)

## Repository layout

| Area | Path | Role |
| ---- | ---- | ---- |
| Documentation | [docs/](docs/README.md) | Status, strategy links, findings indexes, reference |
| Reverse engineering | [reverse_engineering/](reverse_engineering/README.md) | RE tooling and reports (Ghidra Phase 1 in PR D) |
| Experiments | [experiments/](experiments/README.md) | Isolated performance hypotheses (code still under `benchmark/` during migration) |
| Tooling | `tooling/common/` | Shared guards and future measurement infrastructure |
| Measurements | [measurements/](measurements/INDEX.md) | Registry (PR B); historical runs in `results/` |
| Patches | [patches/](patches/README.md) | Version-pinned modifications (future) |
| Legacy profiler | [benchmark/](benchmark/README.md) | Current CLI entry points |
| Legacy analysis | [analysis/](analysis/README.md) | Investigations and generated evidence |
| Run outputs | [results/](results/README.md) | Timestamped captures and sealed Phase C capsule |

Local-only decompilation and Ghidra projects live under `.local/` (gitignored). See [reverse_engineering/README.md](reverse_engineering/README.md).

## Quick start

From the repository root, using Python 3.10+:

```sh
python3 benchmark/eu4_benchmark.py inspect
PYTHONPATH=benchmark:. python3 -m unittest discover -s tests
```

Phase C sealed evidence verification (CI gate; capsule is closed — see [docs/status.md](docs/status.md)):

```sh
PYTHONPATH=benchmark python3 benchmark/eu4_frame_model.py verify-evidence \
  results/20261003T210202Z-intrusive-diagnostic --profile intrusive_complete_v1
```

Live profiler and Phase C capture commands are documented in the [operations manual](docs/reference/profiler-and-benchmark-operations.md) (do not repeat Phase C live capture for new diagnostic data).

Conventions and document types: [docs/conventions.md](docs/conventions.md).
