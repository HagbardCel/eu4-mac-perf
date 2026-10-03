# WP7 — training requalification (PR B)

**Depends on:** merge of PR A (`wp7-lean-reference` — lean production REFERENCE).

## Purpose

One Mac capture asking whether lean REFERENCE satisfies the **existing** Tier-1 training policy (`tier1_causal_rel3pct_abs50us_v2`).

Archive `purpose` remains `profiler_overhead_diagnosis_v1` (same as WP1 diagnostic captures). Provenance is expressed via `requalification_archives` with `work_package: WP7`, not a separate manifest bucket.

No decomposition ladder. No held-out recipes in this step.

## Preconditions

- `main` contains lean REFERENCE implementation + `wp7_lean_reference_validity_v1`
- Clean git tree
- Same pinned GOG binary and offline recipe bundle as prior Tier-1 training runs

## Command

On clean `main` that includes WP7 PR A:

```bash
python3 benchmark/eu4_frame_model.py wp7-causal-requalification --registry-json
```

This runs **causal stages only** (`bare` → `reference` → `counters`, seven interleaved trials × three training recipes). It does **not** run forensic ablations or the WP1 A–F diagnostic matrix (`diagnostic-matrix` is much broader and is not required for WP7).

After capture:

1. Append the archive to `requalification_archives` in `analysis/profiler-overhead-diagnosis-manifest.json` with `work_package: WP7` (same role as WP5 `37f57796`).
2. Record `tier1_causal_admission` outcome per recipe.
3. Short memo: compare to pre-WP7 requalification (`37f57796` baseline context) on `cpu_ns` only.

Do **not** register via `summarize_wp1_diagnosis.py --register-latest` (that targets authoritative `diagnostic_archives`).

## Merge sequence (stacked PR B)

PR B may stack on PR A during review. Before merging PR B to `main`:

1. Merge PR A (`wp7-lean-reference`) to `main`.
2. Retarget PR B base from `wp7-lean-reference` to `main`.
3. Verify the resulting diff (manifest/runbook only, plus any rebased commits).
4. Merge PR B.

## Success criteria

- Structural: all `reference` stages pass `wp7_lean_reference_validity_v1`
- Policy: Tier-1 causal admission **pass** on training recipes (all four gates: `reference_*` vs bare, `counters_*` vs reference)

## If training fails

| Pattern | Next action |
|---------|-------------|
| `reference` vs `bare` **pass**, `counters` vs `reference` **fail** | Counters/profile measurement path is now the dominant causal perturbation. Optimize counters or revisit required symmetry before held-out. |
| Large `reference − loaded-disabled` (if measured elsewhere) | Revisit lean path implementation (should be rare post-reconciliation) |
| Small active step, large `loaded-disabled − bare` | Interposer/pass-through optimization **or** relative-band policy review |
| Held-out / live | **Do not** proceed until training passes |
