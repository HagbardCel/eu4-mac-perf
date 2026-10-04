# Map-text multidraw RE (PR D)

**Priority:** After border PR B/C or border NO-GO.

## Screening anchor

Paused intrusive draw trace ([draw-screening.md](../results/20260928T113359Z-draw-trace/draw-screening.md)):

- **11,744** same-state adjacent pairs over 31 frames → **~379/frame**
- Category: `map_text` via [draw-callers.json](draw-callers.json) — `GfxDrawIndexed` callsite image offset `0xe0e887` (preferred `0x100e0e887`), return `0x100e0e88c` (`CCountryNameCollection::RenderNames`)

## Open RE tasks

1. Pin inner loop and `GfxDrawIndexed` / `GfxDraw` sites for country name rendering.
2. Barrier taxonomy (textures, uniforms, text constants) — mirror border template.
3. Mutation topology: likely **no 943-scale runs**; expect smaller batches.
4. Gross screen: 379 eliminations/frame × ~55 swaps/s ≈ **21k draw_calls_eliminated/s** → below PR B **40k/s** floor unless cadence or adjacency underestimates.

## Status

Charter only — **no mutation** until border track completes.
