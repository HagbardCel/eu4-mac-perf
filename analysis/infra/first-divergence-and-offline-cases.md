# First-divergence diagnosis and offline cases (PR 4)

**Status: implemented; synthetic historical regressions pass, a real package
was produced, and one real correspondence failure was found and corrected.**

## Semantic operation comparison

`automation/gfx_semantic.py` normalizes only the fields the reconstructed
engine contracts depend on:

- draw/caller identity, topology, shader effect/variant;
- sampler-to-texture-unit mapping (`texN` must map to unit N);
- texture identities/digests, uniform values, render target and extent;
- viewport/scissor, raster/cull, depth, blend;
- clear flags/colour, resolve source/destination/orientation/scale;
- GPU completion/refusal and observer-state restoration.

It does not introduce a universal graphics IR. `first_divergence` walks the
aligned prefix and returns the earliest mismatched field, with both normalized
operations and the original operation pair. A missing side is `insufficient`,
not `match`.

## Historical regressions (no EU4 launch)

| Historical defect | Synthetic regression |
| --- | --- |
| Missing `texN` declaration shifted samplers | `test_tex7_bound_to_unit_6_is_the_first_divergence`, `test_missing_tex6_does_not_renumber_tex7` |
| Invalid binding rejected the draw | `test_rejected_draw_is_an_execution_divergence` |
| Incorrect clear semantics | `test_clear_semantics_flags_are_compared` |
| Incorrect cull-enable decoding | `test_incorrect_cull_enable_decoding` |
| Offscreen viewport scaled by backbuffer HiDPI | `test_offscreen_viewport_scaled_by_backbuffer_hidpi_ratio` |
| Wrong resolve orientation | `test_missing_resolve_orientation_flip` |
| Replay pre-image overwritten by synthetic clear | `test_replay_pre_image_ordering_violation`, `test_sampling_before_upload_is_preserved_as_uncertainty` |
| OpenGL observer state corruption | `test_observer_state_corruption_is_an_instrumentation_divergence` |
| Mirrored/misplaced UI text | PR 3 mirror-region regression |
| Wrong-frame/wrong-generation resource usage | `test_wrong_generation_resource_usage` |

The M6 fixes for the sampler mapping, offscreen viewport, resolve orientation
and pre-image ordering already have live/offline evidence in `docs/status.md`
and `analysis/m6/`. This PR makes the responsible **field** machine-checkable
without another launch.

## Resource lineage

`ResourceLineage` tracks stable logical keys (not cross-process pointers),
creation, uploads with digests, binding units, sampling generation, clears,
resolves, presentation and destruction. It records uncertainty instead of
guessing:

- sampled before upload;
- sampled generation lower than the current generation;
- bound before creation;
- destroyed after sampling.

## Flight recorder

`automation/gfx_flight.py` keeps bounded recent frame summaries and events. It
freezes on a terminal condition and writes `flight-recorder.json`; the dual
runner includes it in the preserved artifact store. It never archives every
resource for every frame.

## Diagnostic package

`automation/gfx_case.py` builds a self-contained directory:

- `manifest.json` (provenance, builds, processes, ledger, artifact budget)
- `result.json` (classification, stop, frame/health/comparison)
- `frames/` (both raw payloads, normalized previews)
- `difference/` (amplified difference image, exact statistics)
- `operations/` (alignment evidence; semantic prefix when available)
- `resources/` (lineage or an explicit missing-evidence record)
- `gpu/` (health and raw failure records)
- `analysis.md` (confirmed observations, excluded explanations, hypotheses,
  missing evidence, next inspection point, whether another launch is needed)
- `repro/` (the packaged comparison can be re-run with
  `python3 -m automation.gfx_case compare --case <dir>`)

Classification is one of `rendering_difference`, `unaligned`,
`gpu_execution_failure`, `instrumentation_failure`, `incomplete_evidence`,
`match`. A package with missing raws cannot claim a rendering difference.

### The loading-stage false positive

The real campaign package
(`20261114T134854Z-a56d76bf`) is retained as a cautionary case: two loading
frames with identical caller composition and draw count were compared and
produced a 13.9% difference, but the images showed different loading stages
(`Initializing Maplogic...` vs `Loading Databases...`). The settle-draw gate and
the case classifier now prevent that package from being labelled a renderer
defect. The remaining missing evidence is an update-state fingerprint; until
it exists, loading progress is excluded from comparison rather than trusted.

## Limits

- The live campaign captures frame payloads and health, not a per-draw
  operation prefix. Operation-level localization currently comes from the
  frame trace/selected-event replay path and from synthetic operations.
- A Ghidra-backed ABI is not available for every function; `eu4_re_query`
  reports `unavailable` instead of guessing.
- A case whose evidence is missing says so and requests a targeted capture;
  it does not speculate about a root cause.
