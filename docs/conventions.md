# Documentation and repository conventions

## Five responsibilities

| Area | Path | Responsibility |
| ---- | ---- | -------------- |
| Reverse engineering | `reverse_engineering/` | Recover and understand the original EU IV implementation |
| Experiments | `experiments/` | Test isolated performance hypotheses |
| Measurements | `measurements/`, `results/` | Immutable observations and validation evidence |
| Findings | `docs/findings/` | Current conclusions and architectural decisions |
| Patches | `patches/` | Version-pinned modifications with validation |

During migration, profiler code remains under `benchmark/` and investigative artifacts under `analysis/`. New work should prefer the target paths when practical; note legacy usage in PR descriptions.

## Lifecycles

| Kind | Mutability | Examples |
| ---- | ---------- | -------- |
| Experiment | Mutable | Hypothesis, protocol, may fail or be abandoned |
| Measurement | Immutable once sealed | `results/<timestamp>/`, manifests, Phase C capsule |
| Finding | Revisable | `docs/eu4_macos_rendering_findings.md`, indexed under `docs/findings/` |
| Patch | Version-pinned | Future `patches/implementations/` |

## One authoritative source per conclusion

Write each conclusion in **one** place. Other documents **link** to it—do not maintain parallel summaries. Operational profiler status lives in [status.md](status.md); command details live in [reference/profiler-and-benchmark-operations.md](reference/profiler-and-benchmark-operations.md).

## Local-only artifacts

Game binaries, Ghidra projects, full decompiled pseudocode, and searchable corpora belong under `.local/eu4/<binary-sha256>/` (gitignored). The public repository tracks extraction tooling, aggregate metrics, and original analysis—not reconstructed game source.
