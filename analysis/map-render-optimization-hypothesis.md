# Map render optimization hypothesis (pre-registered)

## Default target (Gfx layer)

Reduce **per 0xe8 subrecord** work from Clausewitz setup through `GfxDrawIndexed`, not generic GL setter suppression.

```text
FlushData / TransparentFlushData
  → 80-byte record
    → 0xe8 subrecord (effect, textures, buffers, object constants)
      → GfxDrawIndexed
```

## Mechanism (PR1 static RE — `gfx-flushdata-re-predicate`)

Split predicates in [`subrecord_equivalence.c`](../benchmark/subrecord_equivalence.c) (layouts in
[`sflushdata-0x50-layout.json`](sflushdata-0x50-layout.json),
[`mesh-draw-subrecord-0xe8-layout.json`](mesh-draw-subrecord-0xe8-layout.json)):

- `submission_state_equivalent` — EQUAL fields per layout JSON; BARRIER bytes break the chain.
- `buffer_bind_elision_eligible` — proven VBO/IBO/gate fields only (observer hit signal).
- `setup_elision_eligible` — requires texture + object-constant predicates; **false** until barrier fields are resolved.
- `draw_batch_eligible` — false until geometry merge is proven ([`gfx-subrecord-pr1-gonogo.md`](gfx-subrecord-pr1-gonogo.md)).

Hook insertion contract: [`mesh-subrecord-hook-site.json`](mesh-subrecord-hook-site.json) (before setup; draw landmark documentation only).

Runtime validation uses protocol v2 ([`submission_control.py`](../benchmark/submission_control.py)): capability **1** observer (`candidate_site_entries`, optional `eligible_pair_hits`, `effective_actions == 0`, engagement-only); capability **2** mutating (`effective_actions > 0`) with full ABABA Pareto gate only after ROI.

## Safety

- Forward original draw path when any compatibility check is uncertain.
- No change to simulation, save, or input handling.
- Optimization shim (if used) must toggle at runtime for A–B–A–B–A validation.

## Falsification (profiler-off)

Use [submission_validation.py](../benchmark/submission_validation.py) Pareto gate on autonomous probe metrics:

- Pass requires rendering correctness (phase screenshots) **and** one of: ≥5% combined power ↓, ≥5% EU IV CPU ↓, or ≥5% swaps/s ↑ with other metrics non-inferior (~2%).

Failure closes this specific mechanism; proceed to next structural candidate from flush-data RE.

## Explicitly deprioritized

- `SShaderOpenGL::SetAll` sampler replay (closed negative).
- Generic texture/vertex GL caches (closed negative).
- `CGLFlushDrawable`, `PresentScene`, `AddToBucket`, `Append` as first targets.

## Vertex-attrib path

Only if TAIL salvage (with adequate retention) **and** static RE implicate a redundant setup sequence at the Gfx/subrecord layer—not as a default GL interceptor.
