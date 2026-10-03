# WP5b — offline completion / pacing diagnosis

**Goal:** Separate Tier-1 submission-window timing from post-window `glFinish()` drain without changing acceptance gates.

## Harness

When `EU4_TEST_COMPLETION_TIMING=1`, `tests/frame_model_workload_harness.c` records:

| Metric | Meaning |
|--------|---------|
| `submission_elapsed_ns` / `submission_cpu_ns` | Four measured frames (same window as Tier-1 `elapsed_ns` / `cpu_ns`) |
| `post_window_drain_*` | `glFinish()` immediately after the submission window |
| `submission_plus_drain_*` | Sum of submission + drain |

`glReadPixels()` validation remains after timing. Default harness behavior (env unset) is unchanged.

## Capture

```bash
python3 benchmark/eu4_frame_model.py completion-diagnosis --registry-json
```

Training recipes only (`mesh`, `borders`, `text_ui`), seven paired trials, stages `bare` / `reference` / `counters`. No A–F matrix, no held-out. Writes immutable evidence under `analysis/evidence/` with `purpose=wp5b_completion_diagnosis_v1`.

## Archives

`frame-model-offline-6e5d53c-20261003T072406.749030Z-f7cbf346.json` (`f7cbf346`) is the **pre-metadata-fix** immutable capture (`6e5d53c`). Treat it as historical:

- `comparisons.*.status` / `limit` are legacy diagnostic labels, not Tier-1 acceptance.
- Top-level `artifact_hashes.recipe_sha256` and source hashes were empty because metadata only read `representative_workloads`; recipe SHA-256 values inside each `completion_workloads.recipes[].recipe` remain valid, as do executed binary hashes.
- Persisted `recipe.path` values were ephemeral temp-dir paths and are non-semantic.

Later captures after the provenance fix should populate standard `artifact_hashes` and omit `path`.

## Interpretation

Compare `reference_*` and `counters_*` on:

- `submission_elapsed_ns` (current Tier-1 quantity)
- `submission_plus_drain_elapsed_ns` (submission + completion)

If REFERENCE is slow only on submission but `submission_plus_drain` aligns with bare, async queueing is supported. If both remain bad, investigate REFERENCE CPU paths — see [WP6 plan](wp6-reference-cpu-decomposition.md).
