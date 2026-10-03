# Map render optimization hypothesis (pre-registered)

## Default target (Gfx layer)

Reduce **per 0xe8 subrecord** work from Clausewitz setup through `GfxDrawIndexed`, not generic GL setter suppression.

```text
FlushData / TransparentFlushData
  → 80-byte record
    → 0xe8 subrecord (effect, textures, buffers, object constants)
      → GfxDrawIndexed
```

## Mechanism (to refine after RE + optional TAIL salvage)

Consolidate or skip redundant **submission transactions** when consecutive subrecords share compatible effect, buffers, textures, and object-constant state (conservative; fail-open).

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
