# WP6 Phase A — REFERENCE path inventory (Tier-1 synthetic harness)

**Status:** complete for offline review (no new capture).  
**Parent:** [WP6 plan](wp6-reference-cpu-decomposition.md) · **Code anchor:** `benchmark/eu4_frame_model.c` (test frame path), `tests/frame_model_workload_harness.c`.

## Measurement window (what `cpu_ns` includes)

The workload harness starts `CLOCK_THREAD_CPUTIME_ID` **after** one un-timed `eu4_frame_model_test_frame` warm-up, `glFinish()`, and `eu4_frame_model_test_arm()`. It then runs **four** timed frames and stops the timer **before** `glReadPixels` validation.

```text
CGLSetCurrentContext(ctx)          # before timer — not in cpu_ns
warm-up frame + glFinish + arm()
START cpu_ns timer
4 × eu4_frame_model_test_frame(workload)
STOP cpu_ns timer
glReadPixels / teardown            # after timer
```

Tier-1 offline `reference` uses `MODE_REFERENCE`, `MEASURE_ENABLED`, and **does not** set `EU4_TEST_SAMPLED` (so `FORENSIC_CAPTURE` stays off after `test_arm(0)`).

## Synthetic frame call graph (one timed frame)

`eu4_frame_model_test_frame` only calls `hook_update(&test_self, true)`. The test stubs recurse through the same hook sites as the EU IV detour graph:

```text
hook_update
  └─ hook_idle          (REFERENCE: gl_measurement_active() == false → no idle scopes)
       └─ hook_render    (REFERENCE fast path)
            └─ hook_map  (intervention_active() == false → test_map only)
                 ├─ hook_add / hook_append  (passthrough — no scopes/events)
                 └─ workload()              (recipe GL commands)
            └─ hook_present                 (measured scopes + event)
```

**Not invoked in this harness during the timed window:** `hooked_flush` / swap, real EU IV map buckets, cadence sleep (`update_period_ns == 0` in offline control), forensic GL detail, GPU timestamp segments, shadow save/seed on render, `tracked_set_context` (no context switch inside the timed loop).

## REFERENCE vs counters — what changes on the hot path

| Mechanism | REFERENCE (Tier-1 harness) | counters (`MODE_PROFILE` + intervention) |
|-----------|----------------------------|------------------------------------------|
| `intervention_active()` | always **false** | **true** |
| `gl_measurement_active()` | **false** | **true** |
| GL draw wrappers | passthrough `real_fn` only | draw accounting, optional detail/GPU |
| `hook_render` | WP5 fast path (scopes + `real_render`) | shadow seed/prepare, GPU hooks, full render wrapper |
| `hook_idle` | passthrough to render chain | idle scopes + `event` |
| `hook_map` / add / append | passthrough stubs | intervention scopes + events |
| `timestamp_event` | no-op (`FORENSIC_CAPTURE` off) | no-op unless sampled forensic armed |
| Shadow / `seed_gl_state` on render | skipped | active |

The bare → REFERENCE CPU gap is therefore **not** explained by counters-stage forensic features (WP1 matrix); it is dylib presence, measurement scaffolding, REFERENCE render/update/present accounting, and **per-draw interposer dispatch** even when wrappers early-out.

## Hook inventory (REFERENCE + `measurement_active()`)

Functions below run during the **four timed frames** unless noted.

### `hook_update` (always entered once per frame)

| Step | Active in REFERENCE? | Notes |
|------|----------------------|-------|
| `snapshot_control` | yes | Loads `frame_control` from shared control page |
| `note_mode_transition` | once per mode change | `invalidate_shadow_cache_quiet` on REFERENCE transitions (usually outside timed window after `arm`) |
| `measurement_active()` | yes | calls `refresh_unowned_control()` when not `inside_update` |
| `scope_begin` LOOP, UPDATE | yes | REFERENCE allows UPDATE, LOOP, RENDER, PRESENT only |
| `timestamp_event(1, …)` | called | **no-op** (no `FORENSIC_CAPTURE`) |
| `real_update` → idle → render chain | yes | |
| `event(HOOK_UPDATE, …)` | yes | producer aggregation |
| `eu4_scope_snapshot` | yes | copies scope tree into `current_frame` |
| shadow stamp check before publish | runs | reads atomics; shadow inactive in REFERENCE — cheap guards |
| `snapshot_control` (after) | yes | publish gate |
| `publish_frame` | yes | see below |
| `ack_generation` / atomics | yes | if generation pending |

### `hook_render` (REFERENCE branch)

| Step | Notes |
|------|-------|
| `measurement_active()` | at entry (render metadata) |
| `timestamp_event(2/3, …)` | no-op |
| `scope_begin` / `scope_end` `SCOPE_RENDER` | 2× `clock_gettime` each at begin/end |
| `event(HOOK_RENDER, …)` | after render |

### `hook_present`

| Step | Notes |
|------|-------|
| `scope_begin` / `scope_end` `SCOPE_PRESENT` | |
| `event(HOOK_PRESENT_SCENE, …)` | |

### Passthrough (dylib loaded, no measurement hooks)

| Hook | REFERENCE behavior |
|------|-------------------|
| `hook_idle` | direct `test_idle` → `hook_render` |
| `hook_map` / `hook_add` / `hook_append` | direct test stubs — **no** `scope_*` / `event` |
| GL exports (`gl_draw_*`, state hooks) | `if (!intervention_active()) { real_fn(); return; }` — **trampolines still run** |

### Not on hot path (live / other stages)

| Item | Phase A disposition |
|------|---------------------|
| `tracked_set_context` / `context_stamp` | [WP6 plan — out of scope](wp6-reference-cpu-decomposition.md#not-active-in-current-tier-1-synthetic-measurement) for current harness |
| `write_record` / writer thread | out of scope for `cpu_ns`; main thread still queues via `publish_frame` |
| Cadence / intervention render modes | offline control uses mode REFERENCE, no cadence fields |

## Per-measured-frame call-count model

Counts are for **one timed frame** in Tier-1 `reference` (not `bare`). Let **N** = recipe commands per frame (`draws` in recipe metadata). Training fixtures (current tree):

| Recipe | N (commands/frame) | 4-frame window |
|--------|-------------------|----------------|
| mesh | 2774 | 11 096 workload commands |
| borders | 2285 | 9140 |
| text_ui | 632 | 2528 |

Symbols are **static** from code review (2026-10-03, `main` @ WP6 plan merge). Re-validate after hook changes.

### A. Frame hooks (independent of N)

| Symbol | Invocations / frame | Extra `clock_gettime` pairs (uptime + thread) |
|--------|---------------------|-----------------------------------------------|
| `snapshot_control` | 2 (update start + publish gate) | 0 (no clocks inside) |
| `measurement_active` → `refresh_unowned_control` | ~10–14 (scopes + hook guards) | 0 when sequence stable |
| `scope_begin` (LOOP, UPDATE, RENDER, PRESENT) | 4 | 4 × 2 = **8** timestamps passed in |
| `scope_end` (same four) | 4 | 4 × 2 = **8** |
| `timestamp_event` | 3 | 0 (early return) |
| `event` | 3 | 0 |
| `producer` | ≤6 (events + publish) | 0 after thread-local producer exists |
| `flush_counters` | 1 (inside `publish_frame`) | 0 |
| `publish_frame` | 1 | 0 |
| `eu4_scope_snapshot` | 1 | 0 |
| `eu4_spsc_reserve` / `eu4_spsc_publish` | 1 each | 0 |

Additional `now_ns` in `hook_update` for frame wall/cpu totals: **~8** calls (4 uptime + 4 thread) in the measuring block.

**Order-of-magnitude (N-independent):** ~**24** `clock_gettime` calls/frame from scope machinery + frame timing, plus **~12** `measurement_active` checks, **3** `event`, **1** publication.

### B. Workload loop (scales with N)

Each workload command issues one or more direct GL calls. With `intervention_active() == false`, draw/state hooks execute:

```c
if (!intervention_active()) { real_fn(...); return; }
```

So per command expect **1 interposed dispatch + 1 indirect call** to the real GL entry (mesh/borders/text_ui mix `glDrawElements`, `glDrawElementsBaseVertex`, `glDrawArrays`, and state setup).

| Symbol | Invocations / frame |
|--------|---------------------|
| GL interposer fast path | **N** (recipe-dependent) |
| `event` / `scope_*` on GL path | **0** in REFERENCE |

Dominant term for **mesh** is therefore **O(N)** trampolines (~2.8k/frame × 4 frames ≈ 11k), not scope clocks (~24/frame).

### C. Four-frame window totals (illustrative)

Multiply section A by 4; multiply section B by `4 × N`.

| Recipe | ~GL fast-path dispatches (4 frames) | ~scope `clock_gettime` calls (4 frames) |
|--------|-------------------------------------|----------------------------------------|
| mesh | ~11 096 | ~96 |
| borders | ~9140 | ~96 |
| text_ui | ~2528 | ~96 |

**Hypothesis for Phase B:** a large share of **active REFERENCE tax** on mesh/borders may be **loaded interposition + passthrough GL**, separable only with a **`loaded-disabled`** stage (dylib present, measurement off, same interposers) vs **`bare`**. Marginal ablations on scopes/events target the **~96** clock calls and hook scaffolding, not the **~10⁴** GL dispatches.

## Overlap map (for non-additive ablations)

| Component | Overlaps with |
|-----------|----------------|
| `scope_begin` / `scope_end` | `measurement_active()`, `now_ns` |
| `event` | `producer()`, optional `unowned_event` (skipped when `inside_update`) |
| `publish_frame` | `flush_counters`, `producer`, SPSC queue |
| `measurement_active` | `refresh_unowned_control` → `snapshot_control` on sequence change |
| GL fast path | dylib load / symbol interposition (present even when accounting off) |

Disabling scopes reduces some `measurement_active` calls inside `scope_begin`; disabling `event` does not remove `publish_frame` aggregation path.

## Phase B ablation mapping (proposed)

| Stage / knob | Intended isolation | Risk |
|--------------|-------------------|------|
| **`loaded-disabled`** | `fixed instrumentation tax` | Must keep dylib + interposers; only `MEASURE_ENABLED`/mode semantics off |
| **`minimal-reference`** | upper bound after disabling scopes + events + publish | must not collapse into `loaded-disabled` |
| REFERENCE ablation: scopes off | marginal scope + clock cost | also skips nested `measurement_active` in `scope_begin` |
| REFERENCE ablation: events off | `event` + counter flush side effects | `publish_frame` may still run |
| REFERENCE ablation: publish off | SPSC + frame copy | breaks writer pipeline — diagnostic only |
| REFERENCE ablation: GL interpose noop | dylib dispatch tax | may need test-only export, not Tier-1 `reference` identity |

Existing sampled ablations (`EU4_TEST_ABLATION=accounting|writer|preparation`) target **counters/forensic** paths, not REFERENCE scaffolding — **do not reuse verbatim** for WP6 REFERENCE decomposition.

## Phase A conclusion

1. **Inventory:** Timed REFERENCE work is concentrated in **one `hook_update` tree/frame**, with GL cost scaling as **N × interposer fast path**.
2. **Call-count model:** Hook scaffolding is **~O(1)** per frame; mesh/borders are **~O(N)** with N in the thousands — prioritize **`bare` / `loaded-disabled` / `reference`** ladder before fine-grained REFERENCE ablations.
3. **Next:** Phase B implements `loaded-disabled` and `minimal-reference` semantics per [WP6 plan](wp6-reference-cpu-decomposition.md), then REFERENCE-scoped ablation envs tied to the overlap map above.
