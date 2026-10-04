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

## Multi-hypothesis observer smoke (protocol v3, schema v2)

Same **A–B–A** schedule as engagement smoke. B1 uses explicit ARM/FREEZE (FREEZE ack before `measurement_end`); interpretation is in `phases[].multi_hypothesis` and `multi_hypothesis_gate`. Manifest experiment kind: **`submission_observer_multi_hypothesis_v2`**.

```bash
python3 benchmark/submission_experiment.py --output results --multi-hypothesis-smoke
```

**Pass criteria (v3.1):** A1/A2/B1 absolute scene gates (`observer_scene_gate`); reference health (health `site_entries` + `renderbuckets_invocations`, no effective actions); B1 `control_validation.status == passed` **and** `multi_hypothesis.harness_ok` (includes frozen-bank invariants below). **Do not** require `cross_parent_pairs > 0`.

**Frozen candidate bank identities (exact):**

```text
candidate_nonempty_renderbuckets <= candidate_renderbuckets_invocations
candidate_site_entries - candidate_nonempty_renderbuckets == adjacent_within_invocation
same_parent_pairs + cross_parent_pairs == adjacent_within_invocation
```

**Continuity reports (not pass/fail):** `derived_metrics` in B1 multi_hypothesis (`sites_per_nonempty_renderbuckets`, `candidate_site_entries_per_swap`, etc.).

Historical **v3.0** run `20261004T131931Z` used kind **v1** and invalid pair segmentation — see [`analysis/mesh-observer-chain-semantics-20261004.md`](../analysis/mesh-observer-chain-semantics-20261004.md).

**Pre-Venice local:**

```bash
PYTHONPATH=benchmark python3 -m unittest \
  tests.test_mesh_observer_chain_fsm \
  tests.test_mesh_observer_gateway \
  tests.test_renderbuckets_entry_gateway \
  tests.test_submission_control \
  tests.test_submission_multi_hypothesis -q
```

Static RE: [`analysis/gfx-subrecord-hypothesis-matrix.md`](../analysis/gfx-subrecord-hypothesis-matrix.md), [`analysis/renderbuckets-entry-abi-v31.md`](../analysis/renderbuckets-entry-abi-v31.md), [`analysis/cross-parent-buffer-bind-re.md`](../analysis/cross-parent-buffer-bind-re.md).

### Venice registration (manual)

After a corrected v3.1 run:

1. Register under `results/<timestamp>-submission-experiment/` (manifest `experiment` = `submission_observer_multi_hypothesis_v2`).
2. Add `analysis/evidence/gfx-subrecord-multi-hypothesis-smoke-<timestamp>.json` using [`analysis/evidence/gfx-subrecord-multi-hypothesis-smoke-TEMPLATE.json`](../analysis/evidence/gfx-subrecord-multi-hypothesis-smoke-TEMPLATE.json).
3. Do **not** rewrite immutable `validation.md` from prior runs (e.g. `131931Z`).

Observer phases are 10 s; per-phase metrics use phase-scaled sample floors in `summarize()` (not the 30 s autonomous baseline defaults).

## Full ABABA (capability 2)

Not available in the current tree. `submission_experiment.py` requires **`--engagement-smoke`** or **`--multi-hypothesis-smoke`** (mutually exclusive). A future mutating dylib revision must advertise capability 2 intrinsically before full ABABA.

## PR1 reference

- Go/no-go: [`analysis/gfx-subrecord-pr1-gonogo.md`](../analysis/gfx-subrecord-pr1-gonogo.md)
- Hook contract: [`analysis/mesh-subrecord-hook-site.json`](../analysis/mesh-subrecord-hook-site.json)
