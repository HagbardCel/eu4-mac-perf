# EU IV macOS Performance Optimization — Phase II Plan

## 1. Current State of Knowledge

The investigation has progressed far enough that several initially plausible explanations can now be substantially narrowed.

### Established findings

EU IV's macOS renderer is heavily CPU-bound even while the game is paused.

The paused rendering path is approximately:

```text
CInGameIdler::Render
    ↓
CEU3GraphicalMap::Render
    ↓
CGraphics::RenderBuckets
    ↓
CPdxMeshObject::RenderBuckets
    ↓
glDrawElements*
    ↓
AppleMetalOpenGLRenderer
    ↓
Metal command/state construction
```

The renderer executes roughly:

```text
~50–60 rendered frames / second

~6,000 draw calls / frame

≈ 250,000–300,000 draw submissions / second
```

Most draws are small.

The passive diagnostic also found millions of OpenGL state-setting calls per second.

However, causal experiments have shown that suppressing very large numbers of redundant:

- texture bindings/state operations,
- buffer bindings,
- vertex-attribute enable/disable operations,
- vertex-attribute pointer setup

does **not** materially reduce EU IV CPU usage or power consumption.

This strongly suggests that:

> The dominant cost is not the setter calls themselves, but the actual fine-grained draw submission and subsequent state validation performed by Apple's legacy OpenGL compatibility implementation.

One important hypothesis remains unresolved:

> `SShaderOpenGL::SetAll()` appears to upload 17 sampler uniforms every time a shader is selected, producing approximately 900,000 `glUniform1i` calls per second.

The earlier generic uniform experiment was invalid because of instrumentation overhead.

---

# 2. Project Goal

The target is no longer simply:

> reduce the number of OpenGL calls.

The goal is now:

> **identify and eliminate the highest-cost architectural inefficiencies in EU IV's OpenGL renderer while preserving rendering correctness and normal game behavior.**

The preferred final solution is a direct, version-pinned modification of the EU IV executable rather than a permanent generic wrapper.

Wrappers and interposers should be treated primarily as:

- measurement tools,
- causal experiments,
- prototypes of candidate engine fixes.

---

# 3. Strategic Sequence

The work should proceed through five major stages:

```text
Stage 1
Autonomous experiment infrastructure
        ↓
Stage 2
Finish sampler-uniform hypothesis
        ↓
Stage 3
Reverse-engineer draw submission architecture
        ↓
Stage 4
Prototype structural renderer optimizations
        ↓
Stage 5
Move validated optimization into EU IV binary
```

Each stage has an explicit decision gate.

---

# 4. Stage 1 — Build a Fully Autonomous Experiment Runner

## Objective

Remove human interaction from almost all future benchmarking.

The agent should be able to:

```text
modify/build experiment
        ↓
launch EU IV
        ↓
load fixed save automatically
        ↓
wait for stable game state
        ↓
run benchmark
        ↓
collect power/performance data
        ↓
terminate EU IV
        ↓
analyse result
```

without requiring manual input.

This is now important enough to implement **before further renderer experiments**.

---

## 4.1 Fixed benchmark fixture

Use a disposable, deterministic non-Ironman save.

Requirements:

- autosave disabled,
- known camera location,
- game initially paused,
- fixed graphics settings,
- fixed resolution,
- fixed map mode,
- no mods except explicitly approved visual-only components,
- no game state that may cause dialogs immediately after loading.

Keep the save under version control or maintain a verified checksum.

Example:

```text
fixtures/
    venice_paused.eu4
```

Record:

```text
save SHA-256
EU IV executable SHA-256
settings.txt SHA-256
DLC configuration
```

---

# 5. Automatic Save Loading

Use EU IV's continuation mechanism.

The runner should:

1. back up the existing `continue_game.json`,
2. point it at the benchmark fixture,
3. launch:

```bash
eu4 --continuelastsave
```

4. restore the original continuation file after the experiment.

Restoration must happen even if:

- EU IV crashes,
- the test fails,
- the agent process is interrupted.

Use a cleanup handler/finalizer.

---

# 6. Ready-State Detection

Do not use a fixed 60-second `sleep` as the primary mechanism.

Create a robust readiness detector.

Preferred hierarchy:

### Level 1 — Renderer signal

The injected diagnostic shim detects:

```text
CGLFlushDrawable / equivalent frame presentation
```

and reports when:

- swaps have started,
- swap rate has stabilized,
- EU IV has continuously rendered for a defined interval.

### Level 2 — CPU stabilization

Require EU IV CPU activity to fall within a stable paused-game band.

For example:

```text
rendering observed
+
10 s of stable swap activity
+
CPU variance below threshold
```

### Level 3 — Future direct engine signal

If static analysis identifies a reliable game-load-complete function, instrument it and emit:

```text
GAME_READY
```

This is the preferred long-term solution.

---

# 7. Warm-Up Policy

Previous measurements showed substantial early-run drift.

Therefore every experiment should contain an explicit warm-up phase before collecting any usable samples.

Initial recommendation:

```text
campaign loaded
    ↓
90 s warm-up
    ↓
measurement begins
```

Once automated datasets show that stabilization reliably occurs faster, this may be shortened.

Warm-up measurements should still be logged but excluded from causal comparisons.

---

# 8. Privilege Separation

Do **not** run the complete agent or benchmark harness as root.

Architecture:

```text
normal user
│
├── EU IV
├── experiment controller
├── instrumentation shim
├── save/config management
└── analysis
       │
       └── sudo -n
             ↓
       narrowly-scoped
       powermetrics helper
```

Create one root-owned helper script for the exact required `powermetrics` invocation.

Allow only this helper through `sudoers`.

The agent must not receive unrestricted passwordless `sudo`.

---

# 9. Experiment Metadata

Every autonomous run should generate a machine-readable manifest:

```json
{
  "eu4_sha256": "...",
  "save_sha256": "...",
  "shim_sha256": "...",
  "experiment": "...",
  "resolution": "...",
  "refresh_rate": "...",
  "settings_sha256": "...",
  "start_time": "...",
  "git_commit": "..."
}
```

This makes later comparisons trustworthy.

---

# 10. Stage 1 Success Criterion

Stage 1 is complete when the agent can perform:

```text
launch fixed save
→ detect ready
→ warm up
→ measure 30 s
→ terminate
→ produce power/CPU/swap summary
```

without human interaction.

Run this smoke test several times.

Require stable baseline results before proceeding.

Suggested reproducibility target:

```text
CPU time variation      < ~3%
combined power          < ~5%
swap rate               < ~3%
```

between settled identical runs.

---

# 11. Stage 2 — Final State-Cache Experiment: Sampler Uniforms

This is the one unresolved fine-grained state hypothesis.

Do **not** repeat texture or vertex-state caching.

Those have already provided sufficient negative evidence.

---

# 12. Static Hypothesis

`SShaderOpenGL::SetAll()` appears to perform approximately:

```text
glUseProgramObjectARB(program)

glUniform1i(location0, 0)
glUniform1i(location1, 1)
...
glUniform1i(location16, 16)
```

every time the shader becomes active.

Observed rates approximately agree:

```text
~53k program selections / second
×
17 sampler assignments
≈
~900k glUniform1i calls / second
```

Sampler uniforms are program state.

They do not need to be reassigned merely because another program temporarily becomes active.

Therefore the conceptual source-level optimization is:

```text
shader linked
    ↓
initialize sampler locations once
    ↓
subsequent SetAll()
    ↓
only bind/use program
```

---

# 13. Build a Call-Site-Specific Experimental Cache

Do **not** implement another generic uniform cache.

Restrict optimization to the exact sampler-uniform upload call path originating from:

```text
SShaderOpenGL::SetAll()
```

Track:

```text
(program object, uniform location, integer value)
```

Persistent across frames.

Invalidate on:

```text
program relink
program deletion
verified modification of same location
```

If anything is uncertain:

```text
forward the original call
```

The experiment should fail open rather than risk incorrect rendering.

---

# 14. Sampler Experiment Protocol

Use:

```text
A – U – A – U – A
```

where:

```text
A = exact pass-through
U = sampler-uniform suppression
```

Recommended timing:

```text
90 s initial warm-up

A 30 s
U 30 s
A 30 s
U 30 s
A 30 s
```

Discard the first ~5 seconds after every state transition.

Cache state may simply be invalidated on every A↔U transition.

---

# 15. Sampler Experiment Measurements

Collect:

- EU IV CPU seconds / real second,
- CPU power,
- GPU power,
- combined CPU+GPU power,
- swap rate,
- CPU milliseconds / swap,
- energy / swap,
- attempted sampler-uniform calls,
- forwarded sampler-uniform calls,
- suppressed sampler-uniform calls.

No call stacks.

No symbolization.

No expensive instrumentation.

---

# 16. Sampler Decision Rule

### Strong positive result

Proceed directly toward an engine patch if:

```text
CPU or combined power improves ≥5%
```

with:

- no visible rendering error,
- no material simulation/render-rate regression.

### Moderate positive result

If improvement is approximately:

```text
2–5%
```

consider an engine patch only if the patch is extremely small and low-risk.

### Negative result

If improvement is:

```text
<2%
```

after repeated autonomous runs:

> close fine-grained state caching as a major optimization strategy.

Do not continue chasing individual OpenGL setters.

---

# 17. Stage 3 — Reverse-Engineer the Draw Path

Whether the sampler experiment succeeds or not, draw-submission analysis should become the main research path.

Target functions:

```text
CGraphics::RenderBuckets
CPdxMeshObject::RenderBuckets
```

plus directly adjacent helpers.

---

# 18. Main Questions

Determine:

1. What does one `RenderBuckets` invocation represent?
2. What does one draw call represent?
3. Which game objects dominate draw count?
4. Why are there ~6,000 draws/frame?
5. How are render buckets grouped?
6. What state changes between consecutive draws?
7. Are multiple draws referencing contiguous regions of the same index buffer?
8. How often do consecutive draws share all meaningful rendering state?
9. Are transforms/uniforms the main reason draws remain separate?
10. Could batches be combined before entering OpenGL?

---

# 19. Static Analysis First

Before collecting another dynamic trace, use the unusually rich symbols in the executable.

Recover pseudocode/control flow for:

```text
CGraphics::RenderBuckets
CPdxMeshObject::RenderBuckets
```

and relevant subordinate functions.

Map:

```text
engine object
    ↓
render bucket
    ↓
mesh/submesh
    ↓
buffer selection
    ↓
state application
    ↓
glDrawElements*
```

Record relevant object layouts wherever they can be inferred.

Useful fields may include:

```text
program/shader pointer
texture/sampler array
vertex buffer
index buffer
primitive mode
index count
index offset
base vertex
transform/material
render-layer identifier
```

---

# 20. Identify Individual Draw Categories

Use return-address attribution or very lightweight hooks to determine what fraction of draw calls come from:

- terrain,
- province fills,
- borders,
- coastlines,
- rivers,
- trade routes,
- units,
- city/map objects,
- text,
- UI,
- post-processing,
- other categories.

The objective is to turn:

```text
6,000 draws/frame
```

into something like:

```text
terrain                  1,800
province/border           900
map objects              1,200
units                      400
UI/text                  1,500
other                      200
```

Numbers above are illustrative only.

This tells us where optimization matters.

---

# 21. Draw-Run Analysis

Build a compact trace of draws containing:

```text
draw number
program
FBO
primitive type
index buffer
vertex buffer
index count
index offset
base vertex
texture-state fingerprint
uniform-generation fingerprint
render-state fingerprint
call site
```

Avoid expensive full state snapshots.

Hash relevant state.

Then classify consecutive draws.

---

# 22. Batchability Metrics

Report:

```text
total draws/frame

same program as previous
same buffers
same textures
same render state
same uniforms
same everything except index range

contiguous index ranges
compatible primitive types

batch run lengths:
2+
4+
8+
16+
```

Most important metric:

> **How many actual OpenGL draw submissions could theoretically be replaced by one larger submission without altering rendered output?**

---

# 23. Candidate Structural Optimization A — Merge Consecutive Draws

If multiple consecutive calls have identical rendering state and compatible index data, combine them.

Potential APIs include:

```text
glMultiDrawElements
```

or engine-level index-range aggregation.

Concept:

```text
before

Draw A
Draw B
Draw C
Draw D
```

becomes:

```text
MultiDraw(A,B,C,D)
```

or:

```text
one aggregated Draw
```

depending on buffer layout.

The purpose is not merely eliminating four function-call overheads.

The bigger gain is:

```text
four driver validation/submission events
        ↓
one driver validation/submission event
```

which directly targets the observed GL→Metal bottleneck.

---

# 24. Prototype Batching with an Experimental Shim/Hook

Before modifying the binary permanently, construct a controlled proof-of-concept.

Prefer hooking the **Clausewitz render-bucket level**, not generic `glDrawElements`.

Reason:

At the engine layer we may still know:

- object identity,
- bucket boundaries,
- mesh metadata,
- buffer ownership,
- intended ordering.

Generic OpenGL interception sees less semantic context.

If necessary, begin with a GL-level experiment to establish feasibility, but migrate upward quickly.

---

# 25. Batching Safety Constraints

Never merge draws across:

- shader/program change,
- texture state change,
- render-state change,
- uniform update that changes output,
- framebuffer change,
- depth/stencil state change,
- ordering-sensitive transparency,
- incompatible primitive type,
- non-compatible buffers.

Err strongly toward preserving separate draws.

Even a conservative 10–20% reduction could be useful.

---

# 26. Batching Success Criteria

Candidate is worth pursuing if it produces at least one of:

```text
draw submissions ↓ ≥15%
```

and preferably:

```text
CPU time ↓ ≥5%
combined power ↓ ≥5%
```

without:

- image corruption,
- incorrect layering,
- interaction artifacts,
- crashes.

Measure both:

```text
absolute power
```

and:

```text
CPU ms / rendered frame
```

---

# 27. Stage 4 — Adaptive Render Scheduling

This is the other major structural candidate and likely the best practical efficiency improvement.

The fundamental problem is:

> EU IV submits its full render workload dozens of times per second even while the game is paused and visually almost static.

Instead of limiting the game permanently to a low frame rate, make rendering frequency conditional on activity.

---

# 28. Desired Behavior

Example target:

```text
active gameplay / camera movement
    60 FPS

mouse movement / UI interaction
    60 FPS

paused + recently interacted
    60 FPS

paused + idle >250 ms
    30 FPS

paused + prolonged inactivity
    optionally 20–30 FPS

new input
    immediately return to 60 FPS
```

This should preserve normal responsiveness.

---

# 29. Preferred Hook Location

Do not leave the final implementation at `CGLFlushDrawable`.

Instead locate an appropriate point around:

```text
CInGameIdler::Render()
```

or its caller.

Best-case behavior:

```text
if (!render_due)
    skip renderer entirely

game event loop continues
simulation state remains correct
input remains responsive
```

This is superior to sleeping after rendering because it avoids generating the GL work in the first place.

---

# 30. Identify Render/Simulation Coupling

Before patching, determine whether:

```text
Update
Render
Present
```

are structurally separate.

Desired architecture:

```text
process input
update game/simulation
if render_due:
    render
present
```

Potentially problematic architecture:

```text
Render() also performs hidden state updates
```

Static analysis and a temporary function hook should test this.

Skipping render calls must not:

- break UI state,
- suppress input,
- affect simulation timing,
- disturb audio,
- alter save behavior.

---

# 31. Dynamic Render-Skip Prototype

Before binary patching:

```text
normal rendering
        ↓
optimization ON
        ↓
skip N eligible Render() calls
        ↓
optimization OFF
```

Start conservatively:

```text
paused + idle only
```

No render skipping while the simulation is running.

This isolates correctness.

Measure:

```text
CPU time
CPU power
GPU power
render rate
input latency
```

If paused CPU power drops strongly, we have confirmed the largest practical opportunity.

---

# 32. Extend Adaptive Policy Gradually

Only after paused-idle behavior is correct consider:

### Paused but active

Keep full rate.

### Speed 1

Potentially cap rendering independently from simulation.

For example:

```text
simulation updates continuously
render at 30–60 FPS
```

### Speed 5

This may be particularly valuable.

At speed 5 the user often cares more about simulation throughput than visual fluidity.

Potential policy:

```text
speed 5:
render 20–30 FPS
let CPU spend more resources on simulation
```

This might simultaneously:

- lower power,
- increase simulation efficiency,
- possibly increase days/second if rendering competes with simulation CPU.

This should be tested rather than assumed.

---

# 33. Stage 5 — Convert Validated Experiments into Binary Patches

Interposers are not the desired final architecture.

Once a candidate optimization clearly works:

1. identify the exact Clausewitz code responsible,
2. reproduce the logic directly at the engine level,
3. remove unnecessary generic API interception.

---

# 34. Binary Patch Strategy

Use three patch classes in order of preference.

### Type 1 — Minimal control-flow patch

Examples:

```text
skip redundant code block
change conditional branch
alter loop behavior
reuse existing cache flag
```

Best option whenever possible.

### Type 2 — Function trampoline

Patch the original function entry to jump to an optimized implementation.

Example:

```text
SShaderOpenGL::SetAll
        ↓
optimized implementation
        ↓
return
```

Useful when additional state is required.

### Type 3 — Reimplementation of small engine function

For a well-understood function such as:

```text
InternalSetTextureAndSamplerState
SShaderOpenGL::SetAll
```

replace most or all behavior with a better implementation.

Avoid replacing large rendering subsystems.

---

# 35. Binary Safety

Never modify the original executable in place.

Workflow:

```text
original EU4 binary
      ↓
verify SHA-256
      ↓
copy
      ↓
verify patch-site bytes
      ↓
apply patch
      ↓
ad-hoc re-sign
      ↓
launch patched copy
```

The patcher should refuse to proceed if:

- executable hash differs,
- expected bytes differ,
- architecture differs,
- game version is unsupported.

---

# 36. Regression Suite

Once autonomous execution works, build a repeatable rendering regression suite.

Use several fixed saves/scenes:

```text
paused Europe map
zoomed-out political map
zoomed-in terrain
trade map mode
large war with units
busy UI screen
speed 5
```

For every optimization compare:

- screenshots,
- swap/frame behavior,
- CPU/power,
- crashes,
- logs.

For image comparison, tolerate known animations but detect structural rendering differences.

---

# 37. Statistical Policy

Automation means there is no longer a strong reason to trust one measurement.

For important decisions:

```text
≥3 autonomous repetitions
```

Prefer:

```text
A/B randomized ordering
```

where feasible.

For subtle improvements below ~5%, use more runs rather than relying on one trace.

Report:

```text
median
mean
standard deviation
effect size
```

rather than only one percentage change.

---

# 38. Stop Conditions

This project should not become unlimited reverse engineering.

Stop a branch when evidence says its expected value is poor.

### Fine-grained state caching

Stop after sampler-uniform experiment if total achievable improvement remains negligible.

### Draw batching

Stop if:

- fewer than ~10–15% of draws appear safely mergeable,
- or batching produces <~3% CPU improvement despite substantial complexity.

### Adaptive rendering

Continue if it delivers large idle savings with no perceptible responsiveness problem.

### Map framebuffer caching

Do not pursue unless simpler structural changes fail.

### Full OpenGL→Metal replacement

Explicitly out of scope unless the project changes from:

> make EU IV efficient

to:

> re-engineer EU IV's graphics backend as a research project.

---

# 39. Expected Decision Tree

```text
                  autonomous runner
                         │
                         ▼
                sampler-uniform test
                         │
             ┌───────────┴───────────┐
             │                       │
         ≥5% gain                 little gain
             │                       │
             ▼                       │
     patch SetAll() directly         │
             │                       │
             └────────────┬──────────┘
                          ▼
                  draw-path analysis
                          │
               significant batching?
                    │           │
                   yes          no
                    │           │
                    ▼           │
             prototype batch    │
                    │           │
                    └─────┬─────┘
                          ▼
                adaptive renderer
                          │
                  strong idle gain?
                    │           │
                   yes          no
                    │           │
                    ▼           ▼
            patch render loop   reassess ROI /
                                likely stop
```

---

# 40. Concrete Work Packages

## WP1 — Autonomous runner

Deliverables:

```text
benchmark/autonomous_runner.py
benchmark/powermetrics_helper
benchmark/fixture_manager.py
analysis/autonomous_report.py
```

Features:

- executable hash verification,
- save setup/restoration,
- automatic launch,
- readiness detection,
- warm-up,
- experiment scheduling,
- power collection,
- safe shutdown,
- failure recovery.

---

## WP2 — Sampler-uniform proof

Deliverables:

```text
sampler_cache.dylib
offline GL correctness tests
A-U-A-U-A controller
sampler_validation.md
```

Decision:

```text
patch / close branch
```

---

## WP3 — Draw-path reverse engineering

Deliverables:

```text
notes/render_buckets.md
notes/mesh_render_path.md
analysis/draw_classes.json
analysis/batchability_report.md
```

Required outcome:

A sufficiently precise explanation of why EU IV emits thousands of draws/frame.

---

## WP4 — Draw batching prototype

Conditional on WP3.

Deliverables:

```text
experimental batching hook
offline correctness harness
A-B-A validation
```

---

## WP5 — Adaptive rendering prototype

Deliverables:

```text
render-loop hook
input/activity detector
dynamic render-frequency controller
```

Initial scope:

```text
paused + idle only
```

Later scope:

```text
speed-dependent rendering
```

---

## WP6 — Permanent patcher

Once one or more optimizations are validated:

```text
patch-eu4
```

Responsibilities:

- identify supported executable,
- copy original,
- patch verified locations,
- add required trampoline/helper code,
- sign executable,
- produce manifest,
- provide restore/uninstall operation.

---

# 41. Priorities

The practical order should now be:

### P0
**Phase-C evidence closure + bounded TAIL salvage**

Integrity-sealed gzip telemetry on git; ordinal salvage with retention gates; outcome memo.

### P0
**RenderBuckets / submission-transaction static RE**

`_FlushData`, `_TransparentFlushData`, and 0xe8 subrecord construction (see `analysis/flush-data-re.md`).

### P1
**One narrow Map::Render / Gfx-layer optimization**

Pre-registered in `analysis/map-render-optimization-hypothesis.md`.

### P1
**Profiler-off autonomous A/B validation**

`benchmark/submission_experiment.py` + `submission_validation.py` (auto probe only; no frame-model profiler).

### P2
**Adaptive render policy (paused idle)**

Separate success criteria from submission optimization.

### P3
**Permanent EU IV binary patch**

Only after a validated win.

### Completed / closed

- Autonomous runner (WP1).
- Sampler-uniform experiment (negative; see `analysis/sampler-uniform-validation.md`).

### Deprioritized

- texture-state caching,
- vertex-state caching,
- generic GL setter optimization,
- profiler Tier-1 requalification loops,
- timer tuning,
- framebuffer/map caching,
- ARM64 binary translation,
- full graphics-backend rewrite.

---

# 42. Target End State

The ideal final result is not a diagnostic launcher or large interposition layer.

It is something like:

```text
EU IV 1.37.5 GOG
        ↓
patch-eu4
        ↓
EU IV Optimized
```

containing one or more targeted fixes such as:

```text
✓ initialize sampler uniforms only when necessary

✓ reduce/merge redundant render-bucket submissions

✓ avoid full-rate rendering while paused and idle
```

with normal gameplay behavior preserved.

The experiment framework remains useful for:

- validation,
- future EU IV versions,
- regression testing,
- exploring further optimizations.

---

# 43. Immediate Next Actions

The next development iteration should contain **no broad new profiling**.

Proceed in this order:

1. **Implement the autonomous fixed-save runner.**
2. Verify reproducible unattended baseline runs.
3. Finish the corrected, call-site-specific sampler-uniform optimizer.
4. Run the autonomous `A–U–A–U–A` experiment.
5. In parallel, reverse-engineer `CGraphics::RenderBuckets` and `CPdxMeshObject::RenderBuckets`.
6. If sampler optimization works, design the minimal direct executable patch.
7. Regardless of sampler outcome, use the draw-path analysis to determine whether batching is feasible.
8. Prototype an adaptive `CInGameIdler::Render` policy once its control flow and side effects are sufficiently understood.

The strategic shift is:

> **Stop optimizing the volume of inexpensive API chatter and start reducing the number of expensive rendering decisions that cross the Clausewitz → OpenGL → Metal boundary.**

That is now the most evidence-supported route toward making EU IV materially cooler and more efficient on modern Apple Silicon.
