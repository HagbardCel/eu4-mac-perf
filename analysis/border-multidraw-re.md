# Border path multidraw RE (parallel track)

**Priority:** after v3.1 mesh observer fix; **do not** block corrected Venice on this work.

## Motivation (Venice draw-mix, approximate)

| Path | ~draws/frame (paused Venice) | Structure |
|------|------------------------------|-----------|
| Mesh subrecord (`0x14c81e6`) | ~2,421 site entries/swap | Per-subrecord loop; corrected v3.1 adjacency TBD |
| Border | ~2,285 | Single IBO, varying base vertex, one program, few texture signatures |

Border batching may offer larger structural leverage if mesh cross-parent recurrence remains sparse after v3.1.

## Open items

1. Pin border draw site(s) and hook safety (prefix + displaced insns).
2. Confirm single-IBO multidraw pattern and uniform signature cardinality (~500) from draw-trace screening.
3. Texture-ID mapping (lower priority than multidraw geometry).

## Status

Placeholder for PR2 parallel RE; update with pinned offsets when static work lands.
