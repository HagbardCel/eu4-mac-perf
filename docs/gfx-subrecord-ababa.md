# Gfx subrecord submission experiment (runbook)

Profiler-off harness for mesh subrecord optimization candidates. Static RE and go/no-go live under `analysis/`.

## Build

```bash
python3 -c "import submission_control as c; c.build(compiled_capability=1)"
```

Capability is fixed at **dylib compile time** (`EU4_SUBMISSION_COMPILED_CAPABILITY`); Python only sets `requested_capability_id` in the mmap header and must match the built dylib.

## Observer smoke (capability 1)

Short **A–B–A** schedule (10 s phases, 3 s settle). Requires real mesh hook at `0x14c81e6` (not CGL flush). Each phase must pass control **and** absolute scene gates; B phases require `eligible_pair_hits > 0` with `effective_actions == 0`.

```bash
python3 benchmark/submission_experiment.py --output results --engagement-smoke
```

Check `validation.json` → `observer_gate` and `engagement_gate` (both per-phase). **Not** a Pareto optimization pass.

## Full ABABA (capability 2 mutating only)

Build `c.build(compiled_capability=2)` and extend the experiment CLI when a mutating implementation exists. Full Pareto gate applies only after ROI from observer smoke.

## PR1 reference

- Go/no-go: [`analysis/gfx-subrecord-pr1-gonogo.md`](../analysis/gfx-subrecord-pr1-gonogo.md)
- Hook contract: [`analysis/mesh-subrecord-hook-site.json`](../analysis/mesh-subrecord-hook-site.json)
