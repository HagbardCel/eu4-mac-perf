# Gfx subrecord submission experiment (runbook)

Profiler-off harness for mesh subrecord optimization candidates. Static RE and go/no-go live under `analysis/`.

## Build

```bash
python3 -c "import submission_control as c; c.build()"
```

The production dylib is always **capability 1** at compile time. Python sets `requested_capability_id` in the mmap header and must match the built dylib.

## Observer smoke (capability 1)

Short **A–B–A** schedule (10 s phases, 3 s settle). Requires real mesh hook at `0x14c81e6` (not CGL flush). Each phase must pass control **and** absolute scene gates; B phases require `eligible_pair_hits > 0` with `effective_actions == 0`.

```bash
python3 benchmark/submission_experiment.py --output results --engagement-smoke
```

Invoking without `--engagement-smoke` exits with an error (no mutating candidate).

Check `validation.json` → `observer_gate` and `engagement_gate` (both per-phase). **Not** a Pareto optimization pass.

## Full ABABA (capability 2)

Not available in the current tree. Running `submission_experiment.py` without `--engagement-smoke` exits with an error. A future mutating dylib revision must advertise capability 2 intrinsically before full ABABA.

## PR1 reference

- Go/no-go: [`analysis/gfx-subrecord-pr1-gonogo.md`](../analysis/gfx-subrecord-pr1-gonogo.md)
- Hook contract: [`analysis/mesh-subrecord-hook-site.json`](../analysis/mesh-subrecord-hook-site.json)
