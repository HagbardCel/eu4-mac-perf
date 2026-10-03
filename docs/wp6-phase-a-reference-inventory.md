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
| GL draw wrappers | `intervention_active()` fast path → `real_fn` | draw accounting, optional detail/GPU |
| `hook_render` | WP5 fast path (scopes + `real_render`) | shadow seed/prepare, GPU hooks, full render wrapper |
| `hook_idle` | passthrough to render chain | idle scopes + `event` |
| `hook_map` / add / append | passthrough stubs | intervention scopes + events |
| `timestamp_event` | no-op (`FORENSIC_CAPTURE` off) | no-op unless sampled forensic armed |
| Shadow / `seed_gl_state` on render | skipped | active |

The bare → REFERENCE CPU gap is therefore **not** explained by counters-stage forensic features (WP1 matrix). Candidate costs are dylib presence, REFERENCE update/render/present accounting, and **many interposed GL entry points per recipe command** (see below). **Whether those invocations dominate measured CPU is a Phase B hypothesis**, not established by Phase A.

### GL wrapper entry points (REFERENCE fast paths differ)

Each harness `Command` can emit more than one interposed GL call (`tests/frame_model_workload_harness.c`). Wrappers do not share one uniform cost:

| GL call (when flag set) | Gate in REFERENCE |
|-------------------------|-------------------|
| `glDraw*` | `intervention_active()` → passthrough |
| `glUniform4fv` | `intervention_active()` → passthrough |
| `glBlendFunc` (with `glEnable`) | `intervention_active()` via state hooks |
| `glBindTexture` | `gl_measurement_active()` only (false → direct `real_fn`) |
| `glEnable` | `gl_measurement_active()` only |
| `glUseProgram`, `glBindBuffer`, `glVertexAttribPointer` | `shadow_tracking_active()` false in REFERENCE; still `save_gl_state()` no-op + `real_fn` on program/buffer paths |

All paths pay at least **trampolined dispatch** and their guard predicate (`intervention_active`, `gl_measurement_active`, or `shadow_tracking_active` / `gl_shadow_needed`).

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
| GL exports | see table above — always interposed, not always `intervention_active()` draw path |

### Not on hot path (live / other stages)

| Item | Phase A disposition |
|------|---------------------|
| `tracked_set_context` / `context_stamp` | [WP6 plan — out of scope](wp6-reference-cpu-decomposition.md#not-active-in-current-tier-1-synthetic-measurement) for current harness |
| `write_record` / writer thread | out of scope for `cpu_ns`; main thread still queues via `publish_frame` |
| Cadence / intervention render modes | offline control uses mode REFERENCE, no cadence fields |

## Per-measured-frame call-count model

Counts are for **one timed frame** in Tier-1 `reference` (not `bare`). Recipe bytes use `frame_model_workload.RECORD` (`<8I`): per command, harness flags `program`, `uniform`, `texture`, `state`, `vertex` are 1 when that draw changes the corresponding trace signature (`benchmark/frame_model_workload.py` `_build_recipe_payload`).

### Workload GL interposer invocations (scales with recipe)

Per harness command (one draw always):

```text
GL_wrappers/command =
    1                           # draw (glDrawElements / BaseVertex / Arrays)
  + program                     # glUseProgram if flag
  + uniform                     # glUniform4fv if flag
  + texture                     # glBindTexture if flag
  + 2 × state                   # glEnable + glBlendFunc if flag
  + 3 × vertex                  # 2× glBindBuffer + glVertexAttribPointer if flag
```

Let **N** = commands/frame (`draws` in recipe metadata). Then **GL_wrappers/frame = Σ above**.

**Derived from committed recipe bytes** (regenerate with the snippet in [Reproducing wrapper counts](#reproducing-wrapper-counts)):

| Recipe | N | Nprogram | Nuniform | Ntexture | Nstate | Nvertex | **GL_wrappers / frame** | **× 4 frames** |
|--------|---|----------|----------|----------|--------|---------|-------------------------|----------------|
| mesh | 2774 | 924 | 2765 | 1180 | 1 | 2417 | **14 896** | **59 584** |
| borders | 2285 | 1 | 739 | 4 | 1 | 1 | **3034** | **12 136** |
| text_ui | 632 | 148 | 234 | 226 | 42 | 149 | **1771** | **7084** |

| Symbol | Invocations / frame |
|--------|---------------------|
| Interposed GL entry (total) | **GL_wrappers** (table) |
| `event` / `scope_*` on GL path | **0** in REFERENCE (measurement hooks off on GL) |

**Invocation-count conclusion:** the dominant **count** term is **O(GL_wrappers)** per frame (thousands–tens of thousands over four frames on mesh), while frame-hook scaffolding is **O(1)**. **CPU dominance of GL vs scopes is not proven here** — thousands of cheap checks could still lose to dozens of `clock_gettime()` calls until `bare` / `loaded-disabled` / `reference` timings exist.

### Frame hooks (approximately independent of N)

| Symbol | Invocations / frame |
|--------|---------------------|
| `snapshot_control` | 2 |
| `measurement_active()` (hook scaffolding only) | **~14** (scopes, render/present guards, publish path; `refresh_unowned_control` runs only when `!inside_update`) |
| `scope_begin` / `scope_end` (LOOP, UPDATE, RENDER, PRESENT) | 4 / 4 |
| `timestamp_event` | 3 (no-op body) |
| `event` | 3 |
| `publish_frame` (+ `flush_counters`, SPSC) | 1 |

### Workload-driven `measurement_active()` (scales with recipe)

`glBindTexture` and `glEnable` call `gl_measurement_active()` → `measurement_active()` even in REFERENCE (`glBlendFunc` uses `intervention_active()` only and fast-paths). The harness sets `texture` / `state` flags per command, so steady-state:

```text
workload measurement_active/frame ≈ Ntexture + Nstate
total measurement_active/frame    ≈ ~14 + Ntexture + Nstate
```

During `workload()`, `inside_update` is true, so `refresh_unowned_control()` returns immediately — these calls **do not** re-read the shared control sequence. They still pay function entry, branches, and mode/flag checks.

| Recipe | Ntexture | Nstate | Workload `measurement_active()` / frame | **Total ≈ 14 + col** |
|--------|---------:|-------:|----------------------------------------:|---------------------:|
| mesh | 1180 | 1 | **1181** | **~1195** |
| borders | 4 | 1 | **5** | **~19** |
| text_ui | 226 | 42 | **268** | **~282** |

Phase B should treat this as a second **recipe-dependent O(N) guard path**, alongside interposer trampolines.

### `clock_gettime` via `now_ns` (~35 / frame)

Each `now_ns` is one `clock_gettime`. Static trace of the REFERENCE synthetic path (four scopes + frame/render/present timing + ack):

| Site | ~`now_ns` calls / frame |
|------|-------------------------|
| `hook_update` (scopes, frame interval, publish block) | **~15** |
| `hook_render` (REFERENCE: metadata + render scope) | **~12** |
| `hook_present` | **~8** |
| **Total** | **~35** |

Four-frame window: **~140** clock calls (plus any one-off ack outside the steady-state per-frame pattern).

Re-validate after hook changes; this is a **call-count model**, not a profiler sample.

### Reproducing wrapper counts

```bash
python3 -c "
import struct, tempfile
from pathlib import Path
import sys; sys.path.insert(0,'benchmark')
import frame_model_workload as w
R=struct.Struct('<8I')
def count(payload):
    n=len(payload)//R.size; p=u=t=s=v=0
    for i in range(n):
        *_,uniform,texture,state,vertex,program=R.unpack_from(payload,i*R.size)
        p+=program; u+=uniform; t+=texture; s+=state; v+=vertex
    return n+p+u+t+2*s+3*v
with tempfile.TemporaryDirectory() as d:
    root=Path(d)
    for r in w.recipes(root, include_held_out=False):
        payload=(root/f\"{r['name']}.recipe\").read_bytes()
        print(r['name'], count(payload))
"
```

## `loaded-disabled` contract (Phase B)

`loaded-disabled − bare` is only meaningful if the stage is **stable** and **comparable** to `reference`.

**Do not** define `loaded-disabled` as naïve `MODE_OFF`. Example: `shadow_tracking_active()` can call `invalidate_gl_state()` when `frame_control.mode==OFF`, which REFERENCE does not do on the same GL fast paths.

**Do not** assume `MODE_REFERENCE` with `MEASURE_ENABLED=0` is zero-cost: parts of `hook_update` and the REFERENCE render branch still run timing/scope machinery unless explicitly gated for this stage.

**Phase B contract (test-only configuration):**

1. Same dylib and interposed symbols as timed WP6 captures.
2. Same synthetic hook topology (`eu4_frame_model_test_frame` path).
3. **No measured-accounting activity** (no frame publication intended for Tier-1 admission semantics).
4. **No GL shadow invalidation/seed side effects** beyond what a neutral loaded pass-through requires.
5. **No intervention behavior** (same as REFERENCE for gameplay hooks).

Exact control bits / env name are implementation details; the capture must record the contract version in evidence metadata.

## Overlap map (for non-additive ablations)

| Component | Overlaps with |
|-----------|----------------|
| `scope_begin` / `scope_end` | `measurement_active()`, `now_ns` |
| `event` | `producer()`, optional `unowned_event` (skipped when `inside_update`) |
| `publish_frame` | `flush_counters`, `producer`, SPSC queue |
| `measurement_active` | `refresh_unowned_control` → `snapshot_control` on sequence change |
| GL interposer entry | dylib load / symbol interposition (present even when accounting off) |

Disabling scopes reduces some `measurement_active` calls inside `scope_begin`; disabling `event` does not remove `publish_frame` aggregation path.

## Phase B ablation mapping (proposed)

| Stage / knob | Intended isolation | Risk |
|--------------|-------------------|------|
| **`loaded-disabled`** | `fixed instrumentation tax` per [contract](#loaded-disabled-contract-phase-b) | Must not use raw `MODE_OFF` or ambiguous MEASURE-only toggles |
| **`minimal-reference`** | upper bound after disabling scopes + events + publish | must not collapse into `loaded-disabled` |
| REFERENCE ablation: scopes off | marginal scope + clock cost | also skips nested `measurement_active` in `scope_begin` |
| REFERENCE ablation: events off | `event` + counter flush side effects | `publish_frame` may still run |
| REFERENCE ablation: publish off | SPSC + frame copy | breaks writer pipeline — diagnostic only |
| REFERENCE ablation: GL interpose noop | dylib dispatch tax | may need test-only export, not Tier-1 `reference` identity |

Existing sampled ablations (`EU4_TEST_ABLATION=accounting|writer|preparation`) target **counters/forensic** paths, not REFERENCE scaffolding — **do not reuse verbatim** for WP6 REFERENCE decomposition.

## Phase A conclusion

1. **Inventory:** Timed REFERENCE work is **one `hook_update` tree/frame** plus **GL_wrappers/frame** interposed calls (formula above; mesh **~15k/frame**, not **N** draws alone).
2. **Call-count model:** Frame-hook and clock work is **O(1)** (~35 `clock_gettime`/frame, ~14 hook-level `measurement_active`/frame). GL interposer **invocation count** is **O(GL_wrappers)** (~**5.4×** draw-command count **N** on mesh; formula cap **9× N**). Workload `measurement_active()` adds **Ntexture + Nstate** per frame (~**1195** on mesh). Those counts are orders of magnitude above individual O(1) frame-hook operations. **Whether CPU follows invocation/guard count** is for Phase B (`bare` → `loaded-disabled` → `reference`).
3. **Next:** Run `reference-cpu-decomposition` for the three-stage ladder; then `minimal-reference` and REFERENCE-scoped ablations as needed.
