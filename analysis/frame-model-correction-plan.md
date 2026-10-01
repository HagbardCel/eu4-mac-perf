# Paused-frame profiler correction plan

The design below remains the acceptance contract. The status table describes
implementation and verification on 2026-10-01; passing native tests does not
mean the live release gates have passed. The original review was against
`54cbcc43`; corrective work builds on `96a9d2d` and is packaged in the
release-gate follow-up. Publication and CI evidence are recorded separately in
[the verification record](frame-model-verification.md).

| Area | Status | Evidence / remaining work |
|---|---|---|
| Parent generation, recursion, path identities | DONE | Shared C producer/serializer; injected clocks and native tests |
| Measurement-off settling and enable acknowledgement | DONE | Epochs, boundary timestamps, calibrated clock uncertainty; native/Python checks |
| Semantic coverage and residuals | DONE | Envelopes do not count; independent aggregate CPU/wall Update and Render gates |
| Long-run and recommendation gates | DONE | Missing evidence fails closed; baseline coverage checked before interventions |
| Matched A/B/C/D/E cadence, ANATIVE | DONE | Existing phase IDs preserved; unrestricted ANATIVE ID 110 |
| E deadline/rate acceptance | DONE | Absolute deadlines, lateness, missed deadlines, skip fraction, render overruns |
| D forced discard | DONE / live unmeasured | Engine requests retained while discard stays forced; exact final state restored; failures invalidate D |
| GPU allocation and segmentation | DONE / driver partial | Shared lifetime registry; context/pass segments; ownership and migration tests; sampled driver timing only |
| Bounded per-thread publication | DONE | Shared SPSC protocol; saturation/reuse and shutdown drain tests; worker control refresh and explicit unassociated origins |
| Production detours and argument forwarding | PARTIAL | Pinned prologue lengths, native rollback; test-only library exercises seven production wrappers; installed-game ABI/live behavior still unmeasured |
| Counters ≤3%, sampled ≤5% overhead | **BLOCKING** | Valid structural recipes fail; raw seven-pair evidence retained; no threshold relaxed |
| Live reference calibration | OPEN | Reference/counters/reference and six four-render windows implemented; no game launched |
| Residual-discovery pilot | **BLOCKING** | Offline overhead gate failed; installed power helper also stale |
| Semantic hook expansion | OPEN | Seven hooks; add 3–8 verified hooks per residual-driven iteration, separately reviewable |
| Recursive static Gfx / GL evidence | PARTIAL | Bounded traversal, aliases, caller/callee addresses, unresolved indirect edges |
| Shader and Metal mapping | PARTIAL | Static shader hashes/includes/features and concrete Gfx evidence; resource dataflow, live frequency and translation remain open |
| Natural/fixed LPM, restoration | DONE / live unmeasured | Journaled exact restoration; bounded helper source; oversized combined schedule rejected |
| CI | IMPLEMENTED | Portable Python 3.10/3.14 and native producer checks; remote execution status recorded separately |

**Next authorized live action:** the short residual-discovery pilot, after
mandatory offline overhead and exact helper checks pass. Stop on any mandatory
failure and retain a partial report. No definitive diagnosis or Metal go/no-go
can follow from reconciliation alone. Historical border batching is superseded
as the immediate next action.

## 1. Repair phase membership and control publication first

Files: `benchmark/eu4_frame_model.py`, `benchmark/eu4_frame_model.c`.

Replace implicit phase membership with an explicit lifecycle:

```text
transition (measurement disabled)
→ acknowledged mode/cadence change
→ settling (measurement disabled)
→ measurement enabled
→ measurement disabled and acknowledged
→ restoration/next transition
```

Record update start/end timestamps, generation, measurement epoch, mode, and
measurement eligibility. Snapshot the active control once at an update
boundary; hooks inside that update use that snapshot. Discard frames spanning
a measurement or mode boundary. Delayed trace/GPU results retain their original
epoch and render ID, rather than inheriting the phase when they are written.

Use one documented clock mapping between C telemetry and Python event/power
timestamps. Do not assume `CLOCK_UPTIME_RAW` equals Python's monotonic clock.
Filter frames and detailed records against actual acknowledged measurement
windows; compute rates using their measured duration, rather than the nominal
20/30 seconds. Transition and settling records may be retained for diagnostics
but never enter measurement summaries.

Version the shared-control and telemetry formats. Use a coherent command
publication protocol with release/acquire ordering and an acknowledged stable
generation. Split controller-owned commands from probe-owned acknowledgments,
failure counts, dropped-record counts, and detail budgets. `SharedControl.set()`
must not zero hook failures or overwrite probe-owned fields. Reset cadence and
sampling state explicitly on mode/generation changes.

Validation: deterministic five-second settling contamination case; records
crossing either window boundary; delayed GPU completion; acknowledgment timeout;
failed mode transition; and Normal/Low Power transitions with no measurement
epoch active. An E30 population must contain only its measurement window.

## 2. Separate update pacing from render eligibility

Files: controller, C update/render hooks, report schema.

Use distinct `update_period_ns` and `render_period_ns` controls. Calibrate native
update, attempted-render, executed-render, and present rates independently;
the swap rate is not automatically the update rate.

| Phase | Update/input policy | Render policy |
|---|---|---|
| A controls | Same calibrated update period as B/C/D/E | Original renderer |
| B | Hold adjacent native update cadence | Record attempts; skip renderer |
| C | Hold adjacent native update cadence | Run renderer; suppress covered draws |
| D | Hold adjacent native update cadence | Run renderer with validated raster suppression |
| E30/E15 | Hold adjacent native update cadence | Execute renderer only at 30/15 Hz deadlines |
| Natural LPM controls | Original native loop in each power mode | Original renderer |

E30/E15 must not sleep at 33/67 ms after `UpdateOneFrame`. They gate execution
inside `hook_render`, retaining update/input/idle work at the measured native
rate and pacing cheap updates to prevent a busy loop. Use absolute deadlines,
no catch-up render bursts, and explicit late-deadline counters. Report achieved
cadence; a deadline cannot guarantee FPS if the renderer overruns it.

Preserve `update_id`, attempted `render_id`, and `present_id`; add executed and
skipped render counters with skip reasons. Clear detail-active state on every
exit path. Report work per update, per executed render, and per second. E phase
medians over all updates cannot stand in for the cost of a rendered frame.

Validation: a fake-clock harness proves updates continue at native cadence,
render counts hit 30/15 Hz when work permits, and no throughput expansion occurs
in B/C/D. Use a 3% cadence tolerance with explicit overrun status; failure to
hold cadence invalidates the affected causal comparison.

## 3. Establish engine detour and API forwarding safety offline

Files: C detour implementation, `eu4_engine_inventory.py`, harness sources,
controller preflight.

Extract the production detour installer/restorer into a reusable component
tested by an x86-64 executable under Rosetta. Use synthetic functions with
verified instruction boundaries and the actual hooked signatures: pointer,
bool, integer, camera/context pointer, and float arguments. Test argument
values, stack alignment, callee-saved registers, trampoline continuation,
nested/recursive scopes, repeated install/restore, bad expected bytes, unsafe
prologues, and rollback after a partial install failure. Test the production
hook forwarding paths as well as the jump primitive. No benchmark launch may
be the first exercise of those paths.

Add a dedicated `GLhandleARB` wrapper for `glUseProgramObjectARB`, forwarding
to that exact entry point. Store handles without truncation and distinguish
core/ARB identities in telemetry. Test both imported and `dlsym` paths; use a
mock forwarding target with a handle above 32 bits to detect truncation without
passing an invented handle to OpenGL.

Preflight must surface every detour failure before EU IV runs. Static inventory
checks remain necessary, but do not substitute for executable ABI validation.

## 4. Implement an exclusive CPU/wall tree with honest residuals

Files: C scopes, engine inventory, analyzer, report.

Add a bounded thread-local scope stack. Each scope tracks inclusive wall/CPU,
direct-child wall/CPU, exclusive wall/CPU, call count, and actual parent. Handle
recursion and repeated calls by accumulation; do not overwrite earlier timing.
Associate scopes with the active update/render, and flag stack imbalance,
overflow, wrong-thread events, and missing records.

Start with update, Idle, Render, map, PresentScene, CGL flush, bucket construction
and submission. Expand to approximately 20–40 ABI-verified major functions from
the pinned call graph: meshes, borders, UI/text, postprocessing, visibility/LOD,
bucket creation, sorting/grouping, and Gfx entry points. Record the first backend
call to quantify preparation before graphics submission. Choose hooks by
observed residual and overhead, rather than instrumenting every function.

Keep residuals explicit at every node. Broad `UpdateOneFrame`, Idle, Render,
map, and generic “other” time do not automatically count as explained merely
because their boundaries were timed. Maintain an attribution policy defining
which specific leaves have a verified meaning. Report identified exclusive
CPU, unattributed CPU, localized wall-minus-thread-CPU, and unattributed wall.
Wall-minus-CPU can include blocking and descheduling; label it accordingly.
Never add CPU and GPU milliseconds or subtract overlapping inclusive scopes.

The 95% target applies separately to attributable CPU and wall in native A
frames. Sampled GL timing remains an estimate with sample counts and uncertainty;
it cannot be used as an exact child subtraction in the measured scope tree.
Insufficient coverage produces a partial report and blocks a definitive
architectural diagnosis. It does not fail an otherwise valid GPU-partial run.

Validation: synthetic nested scopes with known work/waits, repeated children,
recursion, sibling scopes, intentional uninstrumented work, and malformed scope
events. Parent totals must reconcile with children plus residual within timer
tolerance, without clipping invalid accounting to 100%.

## 5. Correct causal contrasts and recommendation eligibility

Files: analyzer, report, Python tests.

Implement structured contrasts with both neighboring A controls, actual rates,
absolute ms and watts, relative changes, control drift, and validity reasons:

* A−B: consequence of removing rendering under matched update cadence.
* A−C: consequence of suppressing draw submission and downstream work.
* C−B: preparation/state/bucket work remaining when draws are suppressed.
* A−D: consequence of the raster-work suppression intervention.
* A/E30/E15: actual rendered-FPS versus CPU/second and system power curve.

C and B occur at different times. Show their raw difference and a sensitivity
analysis using their local A controls; do not portray drift adjustment as an
exact partition. Suppressing draws can alter driver validation, queuing, waits,
and caches. Its effect is not an additive component of A.

Remove the C→bucket-cache recommendation. Preparation caching requires C−B
plus engine timing and demonstrated bucket redundancy. Backend replacement
requires Gfx/driver attribution and a feasibility inventory. Scheduling requires
cadence results and an explicit invalidation model. Draw shape alone cannot
establish command reuse, unchanged pixels, or a safe cache boundary.

Suppress decisive recommendations when controls drift materially, cadence or
coverage fails, trace records are incomplete, or an intervention is ineffective.
Report no more than two supported candidates, distinguishing observed causal
effects from an estimate of achievable production savings.

## 6. Make GPU timing and sampled state context-aware

Files: C GL wrappers/query manager, temporal/GPU analyzers, GL harness.

Maintain bounded context registries with lifecycle identities, capability state,
query pools, and context-local GL state. Poll and delete queries only in their
owning current context; retain pending results across switches. Context changes
must not permanently disable all GPU measurement. Handle null contexts,
unsupported contexts, destruction/recreation, capacity limits, and stale queries
explicitly. Never synchronously fetch an unavailable query result.

A pass spanning contexts needs context segments with timestamps valid within
each context. Report segments/partial coverage; do not label their sum as whole
frame GPU busy time without proving that interpretation. Even a same-context
timestamp interval is GPU timeline elapsed time, not automatically active GPU
execution time. Keep coarse pass boundaries and CPU/GPU overlap visible.

Maintain or reseed shadow state across pass-through phases. Current program
and buffer tracking can otherwise be stale when profiling resumes. Track VAO
and per-attribute buffer/format/offset state, not just the latest array-buffer
binding. Include API, caller, context, full program identity, vertex sources,
IBO, topology, count, index type and offset/first, signed base vertex, and
instance count in sampled draw identity. Track resource generations and content
versions so reused GL names do not imply identical geometry.

Temporal comparison must preserve call order and repeated occurrences rather
than collapse multiple updates to a dictionary key. Report added/removed records,
changed payload ranges, comparison denominators, and sample omissions. Join
draw, state, uniform, and resource evidence for reuse assessment; retain unknown
client arrays/mapped writes/PBO payloads as coverage gaps.

Validate D across multiple contexts and engine attempts to change discard state.
Confirm forwarding counts and exact restoration on every exit path. Existing D
already uses rasterizer discard; validate this implementation rather than replace
it with zero-area scissor. If the mechanism is unsupported, report D unavailable.

## 7. Expand GL accounting and hierarchical redundancy evidence

Files: wrappers, static inventory, sampled records, analyzer.

Build a coverage manifest from pinned imports, dynamically resolved functions,
and prior diagnostic traffic. Add `glUniform1i` and all observed uniform families,
vertex-attrib operations, texture parameters, depth/stencil/blend/raster state,
viewport/scissor, render targets, resource lifecycle, uploads/copies, and observed
draw families. Preserve exact extension signatures and forwarding targets.
Require C to suppress every observed draw entry point; check totals, not medians.

Use per-render-thread counters with bounded sampled payload capture. Track call
frequency separately from timing and resource byte estimates. Mark unsupported
formats, mapped writes, unpack/PBO sources, and unobserved operations explicitly.
Do not hash or unwind every call continuously.

Add sampled records for the objects→culling→LOD→bucket→flush records→sorting→
submission pipeline as layouts/ABIs are verified. Compare semantic records;
normalize proven transient fields and exclude padding/pointers only when their
meaning is established. Unknown layout bytes remain unknown. Identify the
highest demonstrated reuse boundary without inferring bucket equality from GL
draw equality. Render-thread allocation counts use no stack unwinding and can
move to a separate sampled diagnostic if they breach the overhead gate.

## 8. Separate natural LPM throughput from fixed-throughput DVFS

Files: controller power workflow, privilege helper/recovery, report, tests.

Run a short A-only Normal→Low Power→Normal follow-up with original natural
cadence, the same fixture and profiler, and measurement disabled throughout
transitions. Compare update/render/present rate, CPU/wall per executed render,
GPU intervals, work/render, frequency, power, and thermal state. This directly
tests whether identical work simply takes longer and yields fewer frames.

Provide a separately named optional fixed-cadence DVFS comparison. Never mix
these populations or use the fixed-cadence result as throughput evidence.

Avoid depending on an early `sudo -v` cache eight minutes later. Prepare a narrow
privileged power-mode controller before launch, with a fixed command set,
bounded lifetime, and restoration ownership. Verify authorization for transition
and restoration before starting LPM. Journal the exact prior AC setting and
restore it rather than assuming every cleanup should write Normal. Arrange
restoration on controller disconnect/failure and provide journal-based recovery
for abrupt interruption. Tests cover denial, failed verification, signals,
restore failure, and corrupted/stale journals. Any helper installation is a
separate concrete privileged action subject to the environment's approval.

## 9. Turn the backend seed into a feasibility matrix

Files: `eu4_engine_inventory.py`, generated analysis artifacts, report.

Enumerate Gfx and OpenGL implementations for context/device lifecycle, shaders
and GLSL features, textures/formats/sampling, vertex/index buffers, uniforms,
render targets/FBOs, depth/stencil/blend/raster state, draws/instancing, copies/
readbacks, synchronization, and presentation. Traverse direct callees recursively;
mark indirect/dynamic edges unresolved.

For each row retain caller→Gfx implementation→verified GL operation, resources
and state dependencies, runtime frequency or “not observed,” prospective Metal
mapping, compatibility difficulty, and source evidence/confidence. Inventory
the shader corpus and compatibility-profile dependencies. Candidate mappings
remain candidates; static enumeration is not proof of runtime semantics. The
output should identify contained replacement boundaries and unresolved renderer
changes, rather than attach generic draw equivalents to every operation.

## 10. Implementation sequence and release gates

Use reviewable commits in dependency order:

1. Control/telemetry schema, measurement windows, scheduling, and deterministic
   controller tests.
2. Shared production detour harness, exact core/ARB forwarding, and preflight.
3. Exclusive scopes, residual policy, causal contrasts, and recommendation tests.
4. Context query/state management, richer temporal records, GL coverage, and
   multi-context/state-restoration tests.
5. Natural LPM/recovery workflow, bucket evidence, Metal matrix, and documentation.

Python unit tests cover filtering/rates, control layout/publication, temporal
diffs with repeated records, causal signs and missing phases, drifting controls,
recommendation rejection, recovery, and JSON/Markdown report generation. C/ABI
tests and synthetic GL tests exercise the production implementation, not a
parallel reimplementation. Run these before live calibration.

Then run a short autonomous validation with separate counters-only and sampled
windows bracketed by pass-through controls. Measure render-thread CPU per
update/render and rates, with process CPU/second as supporting evidence. Require
≤3% counters-only perturbation in CPU and update/render/present rate, and ≤5%
sampled-window perturbation. Two sample frames alone are inadequate to establish
that gate: use enough bounded windows to show repeatability and uncertainty.
Check clean restoration, persistent failure counters, record loss, phase
membership, observed draw coverage, and effective B/C/D/E behavior.

Proceed to the long experiment only when correctness and overhead gates pass.
Use residuals from short validation to improve attribution before claiming ≥95%
coverage. Unsupported GPU queries yield CPU-complete/GPU-partial status; missing
CPU attribution yields a partial diagnosis. The final report must answer the
original fifteen questions with evidence or an explicit unresolved status,
and recommend at most two architectural changes when the evidence supports them.
