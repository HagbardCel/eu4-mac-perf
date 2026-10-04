# EU IV macOS performance — active plan

## Status

- **Gfx v3 merged** (#32 static RE, #33 protocol v3 multi-hypothesis observer) on `main` (`ee31bab`).
- **Venice v3 smoke:** `20261004T131931Z` — infrastructure **valid**; v3.0 **pair segmentation invalid** (see [mesh-observer-chain-semantics-20261004.md](analysis/mesh-observer-chain-semantics-20261004.md)). Immutable [validation.md](results/20261004T131931Z-submission-experiment/validation.md); amended interpretation in evidence JSON only.
- **Next:** Observer **v3.1** (entry TLS context, epoch-only pair chain, schema + experiment kind v2) → one corrected `--multi-hypothesis-smoke` on Venice (`submission_observer_multi_hypothesis_v2`).
- **Parallel:** border multidraw RE ([border-multidraw-re.md](analysis/border-multidraw-re.md)); texture-ID mapping optional before next live run.

## PR1 (complete)

Offline mesh subrecord RE — [gfx-subrecord-pr1-gonogo.md](analysis/gfx-subrecord-pr1-gonogo.md).

## Observer trajectory

| Version | Experiment kind | Adjacency semantics |
|---------|-----------------|---------------------|
| v3.0 | `submission_observer_multi_hypothesis_v1` | Reset on mid-loop layer/flush (invalid) |
| v3.1 | `submission_observer_multi_hypothesis_v2` | Consecutive sites within TLS RenderBuckets epoch |

## Canonical Phase-C (frozen)

```text
python3 benchmark/eu4_frame_model.py verify-evidence \
  results/20261003T210202Z-intrusive-diagnostic --profile intrusive_complete_v1
```

## Out of scope

Mutation dylib until v3.1 Venice; Stage-2 on safety mask zero. Long roadmap: [historical-roadmap.md](analysis/historical-roadmap.md).
