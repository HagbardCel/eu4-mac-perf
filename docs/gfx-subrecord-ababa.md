# Gfx subrecord submission experiment (runbook)

Profiler-off A-B-A-B-A harness for mesh subrecord optimization candidates. Static RE and go/no-go live under `analysis/`; this doc covers runtime control only.

## Prerequisites

- macOS, GOG EU IV 1.37.5, autonomous benchmark fixture installed (`benchmark/autonomous_runner.py` preflight).
- `sudo` nopasswd for powermetrics helper (same as Phase-C autonomous runs).

## Build control plane (local)

```bash
python3 -c "import submission_control as c; c.build()"
```

## Full ABABA (capability 0 default — no mutating candidate)

Default `EXPECTED_CANDIDATE_CAPABILITY_ID = 0`: candidate phases report `unsupported_candidate`; Pareto gate does not pass until a mutating capability is enabled.

```bash
python3 benchmark/submission_experiment.py --output results
```

Artifacts: `manifest.json`, `validation.json`, per-phase `control_validation` with protocol v2 counters (`control_ticks`, `candidate_site_entries`, `eligible_pair_hits`, `candidate_effective_actions`).

## Observer smoke (capability 1)

Dylib stub hook (`EU4_SUBMISSION_STUB_CANDIDATE_HOOK=1`, `EU4_SUBMISSION_STUB_CANDIDATE_SITE=1`) exercises ack + site counters without mutation. **Not** a Pareto optimization pass.

```bash
python3 benchmark/submission_experiment.py --output results --engagement-smoke
```

Expect `manifest.engagement_gate.status == engagement_only` and `pareto_gate.status == skipped`.

Before Stage 2 mutation (capability 2), compare `eligible_pair_hits` against the removed-work inventory in [`mesh-draw-dependency-map.md`](../analysis/mesh-draw-dependency-map.md).

## Capability 2 (mutating)

Set compiled/advertised capability to `2` in dylib build env and `SubmissionControl(candidate_capability_id=2)` (experiment CLI extension TBD). Requires real mesh hook at PR1 insertion site; stub increments counters only.

Full Pareto validation applies ([`submission_validation.py`](../benchmark/submission_validation.py)).

## PR1 reference

- Go/no-go: [`analysis/gfx-subrecord-pr1-gonogo.md`](../analysis/gfx-subrecord-pr1-gonogo.md)
- Hook contract: [`analysis/mesh-subrecord-hook-site.json`](../analysis/mesh-subrecord-hook-site.json)
