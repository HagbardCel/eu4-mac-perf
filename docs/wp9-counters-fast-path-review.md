# WP9 — counters steady-state cost (bounded fast-path review)

**Context:** WP8 **`3c1e0db7`** on the **v3 CPU admission variant** (`four_frame_post_arm_prime_1`) shows `counters − reference` CPU at roughly **~12–16%** relative and **~1.3–9.4 µs/frame** absolute (mesh ~13.5% / 9.4 µs; borders ~11.9% / 2.3 µs; text_ui ~16.3% / 1.3 µs). The 40-frame primed window is slightly lower (~11–12% / ~0.9–8 µs). Lean REFERENCE CPU after priming on the 4-frame variant is ~**0.4–5.3 µs/frame**.

**Goal:** Identify whether an **obvious** fast-path exists before committing to v3 hybrid floors or a counters optimization sprint. This is a code-path inventory, not a new decomposition work package.

## REFERENCE (lean) vs counters (profile) — what differs per frame

| Mechanism | Lean REFERENCE (`reference_lean_measurement`) | Counters / profile (`publish_frame`) |
|-----------|-----------------------------------------------|--------------------------------------|
| Update hook | `real_update` + `LeanReferenceFrame` timing only | Full `scope_tree` reset, `scope_begin/end`, `event(HOOK_UPDATE)` |
| GL draw hooks | Passive / minimal (`gl_measurement_active` false in REFERENCE mode) | `observe_draw_api`, per-draw accounting, optional detail/GPU |
| Frame publish | `publish_lean_reference_frame` — **no** `flush_counters()` | `publish_frame` → **`flush_counters()`** then SPSC enqueue full `Frame` |
| Flush hook | Fast path: real flush + swap probe only | Scope `SCOPE_FLUSH`, swap wait timing, `event(HOOK_SWAP)`, detail `P` records |
| Telemetry | Lean slot → writer expands to 60-field CSV | Full frame + scope tree `Q` rows + counter `C` rows + forensic families when enabled |
| Writer | Lean expansion in consumer | `write_record` / `eu4_writer_tick` on hot path for scope/counter lines |

Lean REFERENCE deliberately avoids the per-frame **`flush_counters()` → atomic hook totals → full frame serialization → scope tree reconciliation** stack. Counters mode pays that stack on every published frame.

## Likely cost centers (ordered)

1. **`flush_counters()` + `publish_frame` aggregation** — merges thread-local hook tallies into atomics every frame (`eu4_frame_model.c`).
2. **Scope tree maintenance** — `scope_tree` reset at update entry and `scope_begin`/`scope_end` on render/flush paths during measurement.
3. **`event()` / `gl_event()` on GL and swap** — per-call CPU clocks into producer buckets (counters mode).
4. **CSV writer pressure** — `write_record` for `Q`, `C`, and optional `D/S/U/...` families; consumer is off-thread but producer still formats and enqueues.

## Fast-path candidates (bounded)

| Idea | Effort | Risk |
|------|--------|------|
| **Defer `flush_counters` to frame boundary only** (already at publish; no win unless batching multiple frames) | Low | Low — likely no meaningful gain |
| **Counters mode scope tree sampling** (publish counters without full `Q` tree every frame) | High | Changes forensic/coverage semantics |
| **Lazy counter flush to consumer** (move atomics merge to consumer thread) | Medium | Atomic visibility / ordering for `C` rows |
| **Narrow counters telemetry** (omit scope tree rows when only hook totals required for Tier-1) | Medium | Needs contract version for “counters-lite” stage |
| **Reuse lean publish + side-channel hook totals** | Medium–high | Largest architectural overlap with WP7 lean path |

## Recommendation

1. After authoritative requalification **`a73ea57b`**, **mesh `counters_cpu_ns` is a real v3 blocker** (see `docs/wp9-tier1-v3-design.md`). Counters optimization is justified only via bounded WP10 diagnostics — not threshold relaxation.
2. WP10 **`d07347df`**: counters-lite and deferred flush showed **no measurable CPU gain** vs full counters on mesh — **do not** productionize lite from this evidence (`docs/wp10-evidence-memo-20261003.md`).
3. Remaining mesh v3 tension is plausibly **N=7 bootstrap instability** at ~9.5 µs/frame, not missing scope/flush optimizations — see v4 21-trial proposal.

## Related code

- `publish_frame` / `flush_counters`: `benchmark/eu4_frame_model.c`
- Lean path: `publish_lean_reference_frame`, `reference_lean_measurement`, `hook_update` early return
- Offline stage `counters` vs `reference`: `benchmark/eu4_frame_model.py` (`MODE["profile"]` vs lean reference validation)
