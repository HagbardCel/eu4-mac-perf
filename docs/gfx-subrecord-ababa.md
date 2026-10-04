# Gfx subrecord submission experiment (runbook)

Profiler-off harness for mesh subrecord optimization candidates. Static RE and go/no-go live under `analysis/`.

## Build

```bash
PYTHONPATH=benchmark python3 benchmark/codegen_submission_counters.py
PYTHONPATH=benchmark python3 -c "import submission_control as c; c.build()"
```

The production dylib is always **capability 1** at compile time. Python sets `requested_capability_id` in the mmap header and must match the built dylib.

## Observer smoke (capability 1)

Short **A–B–A** schedule (10 s phases, 3 s settle). Requires real mesh hook at `0x14c81e6` (not CGL flush). Each phase must pass control **and** absolute scene gates; B phases require `eligible_pair_hits > 0` with `effective_actions == 0`.

```bash
python3 benchmark/submission_experiment.py --output results --engagement-smoke
```

Pass either `--engagement-smoke` or `--multi-hypothesis-smoke` (not both).

Candidate phases use protocol **v3** observation **ARM → measure → FREEZE** so candidate-bank counters are coherent at phase end.

Check `validation.json` → `observer_gate` and `engagement_gate` (both per-phase). **Not** a Pareto optimization pass.

## Multi-hypothesis observer smoke (protocol v3)

Same **A–B–A** schedule as engagement smoke. B1 uses explicit ARM/FREEZE (FREEZE ack before `measurement_end`); interpretation is in `phases[].multi_hypothesis` and `multi_hypothesis_gate`. `hits_per_swap` uses the **`candidate_swaps`** counter (same ARM/FREEZE transaction as hypothesis hits). Auto-probe paused swaps are auxiliary scene-health evidence only.

```bash
python3 benchmark/submission_experiment.py --output results --multi-hypothesis-smoke
```

**Pass criteria:** A1/A2/B1 absolute scene gates (`observer_scene_gate`); reference health (site + renderbuckets invocations, no effective actions); B1 `control_validation.status == passed` **and** `multi_hypothesis.harness_ok` (includes `effective_actions_delta == 0`); **no** v1 pair-hit requirement. Stage-2 uses `safety_qualified_hypothesis_mask` and materiality only.

**Pre-Venice local (Rosetta):**

```bash
PYTHONPATH=benchmark python3 -m unittest \
  tests.test_mesh_observer_gateway \
  tests.test_renderbuckets_entry_gateway \
  tests.test_submission_control -q
```

Static RE matrix: [`analysis/gfx-subrecord-hypothesis-matrix.md`](../analysis/gfx-subrecord-hypothesis-matrix.md). Cross-parent continuity: [`analysis/cross-parent-buffer-bind-re.md`](../analysis/cross-parent-buffer-bind-re.md).

### Venice registration (manual)

After a local run, register under `results/<timestamp>-submission-experiment/` and add evidence JSON under `analysis/evidence/` with `posthoc_interpretation` (do not rewrite immutable `validation.md` from prior runs).

Observer phases are 10 s; per-phase metrics use phase-scaled sample floors in `summarize()` (not the 30 s autonomous baseline defaults). A failed run with “Insufficient fully paused swap samples in the 30-second phase” on smoke is the pre-fix harness bug — see `results/20261004T111812Z-submission-experiment/validation.md`.

## Full ABABA (capability 2)

Not available in the current tree. Running `submission_experiment.py` without `--engagement-smoke` exits with an error. A future mutating dylib revision must advertise capability 2 intrinsically before full ABABA.

## PR1 reference

- Go/no-go: [`analysis/gfx-subrecord-pr1-gonogo.md`](../analysis/gfx-subrecord-pr1-gonogo.md)
- Hook contract: [`analysis/mesh-subrecord-hook-site.json`](../analysis/mesh-subrecord-hook-site.json)
