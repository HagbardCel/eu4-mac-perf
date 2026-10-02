# Held-out selection spec (Stage 2 validation)

**Rule ID:** `terrain_odd_ordinal_within_frame_v1`

**Purpose:** Produce held-out recipe bytes that are **not** the exploratory
`ab00679` workload (SHA denylisted in `EXPLORATORY_HELD_OUT_SHA256S`).

## Selection (frozen before any held-out timing)

On the passive draw trace complete frame (`5863` draws):

1. Restrict to records in that frame whose direct-site category is `terrain`.
2. Preserve passive trace order within that subset.
3. Keep records whose **0-based ordinal** within the terrain subset is **odd**
   (1st terrain draw discarded, 2nd kept, 3rd discarded, …).

Materialize with:

```bash
python3 benchmark/freeze_held_out_fixture.py
```

Commit `analysis/held-out/frame-model-held-out-terrain-surrogate.{recipe,json}`
**before** running `python3 benchmark/eu4_frame_model.py preflight`.

## Anti-peeking

- Do not run workload harness on held-out bytes before the fixture commit.
- Do not change this rule after observing held-out timings.
- If validation fails under `tier1_causal_rel3pct_abs50us_v2`, treat that as the
  Stage-2 result; do not select a different held-out slice to retry pass.
