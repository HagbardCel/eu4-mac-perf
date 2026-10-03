# WP7 — training requalification (PR B)

**Depends on:** merge of PR A (`wp7-lean-reference` — lean production REFERENCE).

## Purpose

`wp7_lean_reference_training_requalification_v1` — one Mac capture asking whether lean REFERENCE satisfies the **existing** Tier-1 training policy (`tier1_causal_rel3pct_abs50us_v2`).

No new decomposition ladder. No held-out recipes in this step.

## Preconditions

- `main` contains lean REFERENCE implementation + `wp7_lean_reference_validity_v1`
- Clean git tree
- Same pinned GOG binary and offline recipe bundle as prior Tier-1 training runs

## Command

On a clean `main` that includes WP7 PR A:

```bash
python3 benchmark/eu4_frame_model.py diagnostic-matrix --registry-json
```

This is the existing **training-only** seven-pair `bare` → `reference` → `counters` capture that emits `offline_causal_admission` for mesh, borders, and text_ui (see `docs/profiler-overhead-diagnosis-plan.md`). No decomposition or reconciliation subcommands.

After capture:

1. Register the archive in `analysis/profiler-overhead-diagnosis-manifest.json` under a new `wp7_requalification_archives` entry (or extend `requalification_archives` with `work_package: WP7`).
2. Record `tier1_causal_admission` outcome per recipe.
3. Short memo: compare to pre-WP7 requalification (`37f57796` baseline context) on `cpu_ns` only.

## Success criteria

- Structural: all `reference` stages pass `wp7_lean_reference_validity_v1`
- Policy: Tier-1 causal admission **pass** on training recipes (unchanged gates)

## If training fails

| Pattern | Next action |
|---------|-------------|
| Large `reference − loaded-disabled` remains | Revisit lean path implementation (should be rare post-reconciliation) |
| Small active step, large `loaded-disabled − bare` | Interposer/pass-through optimization **or** relative-band policy review |
| Held-out / live | **Do not** proceed until training passes |
