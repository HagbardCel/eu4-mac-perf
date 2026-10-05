# EU IV macOS performance — active plan

## Status

- **PR #34 merged** — mesh observer **v3.1** on `main` (`3b49bf5`): entry TLS context, schema v2, epoch-based pair chain.
- **Venice v3.1 smoke:** `20261004T152246Z` — gates **passed**; cross-parent denominator **1,290,465** pairs; **0** buffer-signature hits (same- and cross-parent). See [validation.md](results/20261004T152246Z-submission-experiment/validation.md) and [evidence JSON](analysis/evidence/gfx-subrecord-multi-hypothesis-smoke-20261004T152246Z.json).
- **Prior v3.0 run** `20261004T131931Z` — pair segmentation invalid; see [mesh-observer-chain-semantics-20261004.md](analysis/mesh-observer-chain-semantics-20261004.md).
- **#35–#38 merged** — border static RE, observer harness (no Venice ABABA), map-text charter.
- **Next:** Offline border **loop-head feasibility** @ `0x1010cbe55` ([border-loop-head-feasibility.md](analysis/border-loop-head-feasibility.md)); derive semantic/implementable ceiling before authorizing another Venice run. Mesh observer **maintenance-only**.

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

Stage-2 on safety mask zero without safety-qualified hypotheses. Long roadmap: [historical-roadmap.md](analysis/historical-roadmap.md).
