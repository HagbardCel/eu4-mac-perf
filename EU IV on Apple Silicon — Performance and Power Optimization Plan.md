# EU IV on Apple Silicon — Performance and Power Optimization Plan

## 1. Problem Description

Europa Universalis IV is an old and graphically modest game, yet on a modern Apple Silicon Mac it can cause substantial heat generation and apparently disproportionate power consumption.

This is especially surprising on high-end Apple Silicon hardware, where the available CPU and GPU performance exceeds EU IV's original system requirements by a very large margin.

The objective of this project is therefore not primarily to increase maximum game performance, but to improve **performance efficiency**:

- reduce CPU and GPU power consumption,
- reduce heat and fan activity,
- preserve normal gameplay responsiveness,
- preserve acceptable simulation speed,
- avoid invasive modifications unless simpler approaches fail.

The investigation should be measurement-driven. The first goal is to determine **which subsystem actually causes the excessive power consumption** before attempting optimization.

Potential causes include:

- unnecessary rendering at high frame rates,
- inefficiencies in the legacy OpenGL graphics path,
- Retina/high-resolution rendering,
- CPU-heavy simulation,
- Rosetta x86-64 → ARM64 translation overhead,
- excessive CPU boost frequencies,
- engine synchronization or polling behavior,
- combinations of the above.

The desired end result is ideally a lightweight configuration, launcher, wrapper, or runtime modification that makes EU IV significantly more power-efficient on modern Macs.

---

# 2. Project Goals

## Primary goal

Reduce EU IV's sustained power consumption and thermal load on Apple Silicon without materially degrading the gameplay experience.

## Secondary goals

Establish a reproducible understanding of:

- CPU utilization,
- GPU utilization,
- CPU and GPU power consumption,
- frame rate,
- simulation throughput,
- thermal behavior,
- Rosetta-related overhead,
- behavior at different game speeds.

The project should also produce reusable profiling tooling that can be applied to other older games.

---

# 3. Guiding Principles

### Measure before modifying

No optimization should be pursued based purely on assumptions about the engine.

Each hypothesis should have:

1. an observable prediction,
2. an experiment,
3. measurable acceptance or rejection criteria.

### Optimize for efficiency rather than maximum throughput

EU IV does not need:

- 120 FPS,
- maximum GPU frequency,
- maximum CPU turbo frequencies,
- maximum possible speed-5 simulation throughput

to provide a good experience.

Reducing unnecessary work may therefore produce large efficiency improvements with negligible practical downside.

### Prefer increasingly invasive interventions

Use the following hierarchy:

```text
configuration
    ↓
OS-level controls
    ↓
launcher / wrapper
    ↓
API interception
    ↓
runtime patching
    ↓
binary reverse engineering
```

Do not move to the next level unless evidence shows that simpler approaches are insufficient.

---

# 4. Baseline Characterization

Before attempting optimization, establish how EU IV behaves under a controlled workload.

Use a fixed save game and repeatable test scenarios.

## Suggested scenarios

Measure approximately 60–120 seconds each:

1. Main menu
2. Game paused
3. Speed 1
4. Speed 3
5. Speed 5

For simulation tests, use the same save and camera position where practical.

Record at minimum:

- CPU utilization
- CPU power
- GPU utilization
- GPU power
- package/system power where available
- thermal pressure
- game frame rate
- simulation speed, e.g. in-game days per real second
- display refresh rate
- graphics settings
- resolution
- macOS power mode

The benchmark should eventually become automated or semi-automated.

---

# 5. Hypotheses

## H1 — Excessive Frame Rate

### Hypothesis

EU IV is rendering many more frames than are useful.

On a ProMotion display, the game may attempt to render substantially above 60 FPS, potentially approaching 120 FPS.

For a predominantly static strategy-game interface, this work provides very little perceptual benefit.

### Expected evidence

If this is important:

- GPU power remains relatively high while paused.
- CPU/GPU power falls substantially when display refresh rate is reduced.
- 120 Hz → 60 Hz → 48 Hz produces approximately monotonic power reductions.
- Gameplay remains essentially unchanged.

### Experiments

Compare:

```text
120 Hz
60 Hz
48 Hz
```

with otherwise identical settings.

Also test:

- VSync enabled
- VSync disabled
- borderless/fullscreen/windowed modes if relevant

### Possible solutions

In increasing order of complexity:

1. Force macOS display refresh rate to 60 Hz.
2. Force 48 Hz while playing EU IV.
3. Enable or correct EU IV's internal VSync settings.
4. Implement an external or injected frame limiter.

### Priority

**Very high**

This is likely the cheapest potential optimization.

---

# 6. H2 — Retina / Resolution Overhead

## Hypothesis

EU IV's legacy renderer may be pushing unnecessarily large framebuffers because of Retina scaling or high native display resolution.

The visual benefit may be minimal compared with the increase in rendered pixel count.

### Expected evidence

Reducing resolution substantially lowers:

- GPU utilization,
- GPU power,
- total package power.

### Experiments

Benchmark several resolutions or scaling modes.

For example:

```text
native / high-DPI
↓
intermediate scaled resolution
↓
1920×1200-equivalent
```

Keep UI usability in mind.

### Possible solutions

- lower game rendering resolution,
- use macOS scaled display modes,
- investigate whether EU IV uses Retina backing resolution unnecessarily,
- investigate configuration-level overrides.

### Priority

**High**

Especially if paused gameplay already produces significant GPU activity.

---

# 7. H3 — Inefficient Legacy OpenGL Rendering

## Hypothesis

EU IV's old macOS rendering stack may interact inefficiently with modern Apple Silicon graphics hardware and Apple's legacy OpenGL implementation.

The issue might not be raw graphical complexity but inefficient API behavior.

Examples could include:

- excessive draw calls,
- synchronization stalls,
- repeated state changes,
- inefficient buffer handling,
- excessive redraws,
- unnecessary map-layer updates.

### Expected evidence

Possible indicators:

- high GPU or CPU usage even when the visual scene barely changes,
- significant time spent inside OpenGL/CGL system calls,
- rendering workload remains expensive even at lower resolution,
- rendering dominates while paused.

### Investigation

Use Instruments to inspect:

- GPU activity,
- CPU call stacks,
- OpenGL/CGL calls,
- thread synchronization.

Determine whether significant CPU time is spent inside APIs such as:

```text
OpenGL.framework
CGL
IOSurface
CoreAnimation
```

### Possible solutions

Initially:

- reduce frame rate,
- disable unnecessary graphical effects,
- reduce resolution.

More advanced:

- intercept selected OpenGL/CGL calls,
- alter swap behavior,
- suppress unnecessary rendering,
- experiment with presentation pacing.

### Priority

**Medium–high**

Investigate after simple refresh-rate/resolution tests.

---

# 8. H4 — Speed-5 Simulation Dominates CPU Power

## Hypothesis

Heat comes primarily from EU IV's simulation rather than its graphics.

At speed 5 the game intentionally runs the simulation as quickly as the CPU allows.

A powerful Apple Silicon performance core may therefore run at very high frequency continuously.

Even if only a few cores are active, this can result in substantial power consumption.

### Expected evidence

Typical signature:

```text
paused       low power
speed 1      modest power
speed 3      moderate power
speed 5      large power increase
```

GPU consumption should remain comparatively stable.

### Experiments

Compare:

- paused,
- speed 1,
- speed 2,
- speed 3,
- speed 4,
- speed 5.

Measure:

- CPU watts,
- CPU utilization,
- frequency/residency if available,
- simulation days per second.

### Possible solutions

1. Avoid speed 5 where unnecessary.
2. Use macOS Low Power Mode.
3. Investigate CPU-frequency or scheduler behavior.
4. Implement optional simulation throttling if necessary.

### Priority

**Very high**

This is the main competing hypothesis to excessive rendering.

---

# 9. H5 — Excessive CPU Boost / Poor Efficiency Curve

## Hypothesis

EU IV may benefit little from maximum CPU frequency while consuming disproportionately more power.

Apple Silicon's highest performance states may have poor marginal efficiency for EU IV.

Example:

```text
CPU power       simulation speed
10 W            8 days/s
20 W           10 days/s
30 W           11 days/s
```

If so, allowing maximum boost is wasteful.

### Experiments

Compare EU IV under:

- normal mode,
- Low Power Mode.

Potentially also compare:

- battery,
- charger,
- different macOS performance modes where available.

Record:

```text
power consumption
vs
simulation days per second
```

A useful metric is:

```text
simulation efficiency =
days simulated / joule
```

### Possible solutions

If Low Power Mode performs well:

- enable it automatically when launching EU IV,
- restore normal mode when quitting EU IV.

A small launcher could eventually automate this.

### Priority

**Very high**

Low-cost experiment with potentially large benefits.

---

# 10. H6 — Rosetta Translation Overhead

## Hypothesis

The Intel macOS EU IV binary incurs meaningful CPU or power overhead because it executes through Rosetta.

### Important caveat

Rosetta is highly optimized and uses translated code caching.

Therefore the mere fact that EU IV is x86-64 does **not** imply that translation is the dominant source of power consumption.

### Expected evidence

Rosetta becomes a plausible bottleneck if:

- substantial CPU usage persists independently of rendering,
- profiling reveals translation-related overhead,
- system-level Rosetta activity is significant,
- comparable native ARM workloads behave much more efficiently.

### Investigation

First verify the executable architecture:

```bash
file /path/to/eu4
lipo -archs /path/to/eu4
```

Inspect linked frameworks:

```bash
otool -L /path/to/eu4
```

Profile actual execution before drawing conclusions.

### Possible solutions

There is no attractive simple fix.

Binary-level ARM translation or recompilation would be extremely complex and would largely duplicate functionality already provided by Rosetta.

Therefore this hypothesis should primarily be **measured rather than actively optimized**.

### Priority

**Low**

Only investigate deeply if simpler hypotheses fail.

---

# 11. H7 — Busy Waiting / Synchronization Inefficiency

## Hypothesis

EU IV may contain old synchronization logic that:

- busy-waits,
- polls repeatedly,
- wakes threads unnecessarily,
- causes inefficient CPU scheduling.

Older game engines were often optimized for very different CPU and operating-system behavior.

### Expected evidence

Profiling may show:

- threads consuming CPU without doing obvious simulation work,
- synchronization primitives dominating call stacks,
- frequent wakeups,
- spinning threads.

### Investigation

Use Instruments:

- CPU Profiler,
- System Trace,
- thread analysis.

Look for:

- repeated polling loops,
- locking,
- condition-variable behavior,
- frequent short wakeups,
- one permanently active main thread while paused.

### Possible solutions

If a specific hot polling loop can be identified:

- binary patch,
- function interception,
- altered sleep/yield behavior.

This would require careful correctness testing.

### Priority

**Low–medium**

Only pursue if profiling clearly identifies it.

---

# 12. Tooling

## Initial command-line tools

### Architecture inspection

```bash
file eu4
lipo -archs eu4
otool -L eu4
```

### Power profiling

Use Apple's:

```bash
powermetrics
```

Relevant categories include:

- CPU power,
- GPU power,
- task activity,
- thermal state.

Exact available samplers should be confirmed against the installed macOS version.

---

# 13. Instruments Profiling

After basic benchmarking, use Apple's Instruments.

Useful profiling modes include:

### CPU Profiler

Identify:

- hot threads,
- CPU-heavy functions,
- system-library calls,
- synchronization behavior.

Profile separately:

```text
paused
speed 1
speed 5
```

Differences between profiles may be more informative than absolute profiles.

### System Trace

Useful for:

- scheduling,
- thread wakeups,
- blocking,
- context switching,
- busy-wait behavior.

### GPU-related profiling

Determine:

- actual GPU utilization,
- frame presentation behavior,
- whether rendering work continues aggressively while paused.

---

# 14. Benchmark Harness

Create a small repository:

```text
eu4-mac-perf/
```

Suggested structure:

```text
eu4-mac-perf/
├── README.md
├── benchmark/
│   ├── run.sh
│   ├── powermetrics.sh
│   └── scenarios.yaml
├── analysis/
│   ├── parse_powermetrics.py
│   └── compare_runs.py
├── results/
│   └── ...
└── notes/
    └── profiling.md
```

Codex or Cursor can be used heavily for:

- shell scripts,
- log parsing,
- CSV generation,
- experiment automation,
- statistical summaries,
- plots.

The important engineering work remains experimental design and interpretation.

---

# 15. Metrics

For each experiment record:

| Metric             | Purpose                          |
| ------------------ | -------------------------------- |
| CPU watts          | Identify simulation cost         |
| GPU watts          | Identify rendering cost          |
| total/system watts | Measure practical efficiency     |
| CPU utilization    | Identify active threads          |
| GPU utilization    | Measure rendering load           |
| thermal pressure   | Measure sustained thermal impact |
| FPS                | Detect excessive rendering       |
| days/second        | Measure simulation performance   |
| resolution         | Control rendering workload       |
| refresh rate       | Test frame-rate hypothesis       |
| power mode         | Test CPU efficiency              |

Two particularly useful derived metrics are:

```text
energy per simulated day
```

and

```text
simulation days / joule
```

These allow optimization for **efficiency rather than raw speed**.

---

# 16. Suggested Experiment Matrix

Start small rather than attempting every possible configuration.

## Phase 1 — Establish dominant subsystem

Run:

```text
paused
speed 1
speed 3
speed 5
```

with default settings.

Goal:

Determine whether power scales primarily with:

- rendering,
- simulation,
- or both.

---

# 17. Phase 2 — Cheap System-Level Interventions

Test independently:

### Refresh rate

```text
120 Hz
60 Hz
48 Hz
```

### Power mode

```text
normal
Low Power Mode
```

### Resolution

```text
current
medium
low
```

### VSync

```text
on
off
```

Measure each independently before combining them.

---

# 18. Phase 3 — Determine Best Configuration

After identifying significant factors, test combinations.

For example:

```text
48 Hz
+
VSync
+
Low Power Mode
+
moderate resolution
```

Compare against baseline.

Target outcome:

```text
large reduction in watts
with
minimal degradation in simulation speed
and
no meaningful UX degradation
```

If this solves the problem, stop here.

---

# 19. Phase 4 — Investigate Rendering Internals

Proceed only if GPU/rendering remains disproportionately expensive.

Profile:

- frame presentation,
- OpenGL calls,
- paused rendering,
- rendering-thread behavior.

Determine whether a frame limiter would help.

---

# 20. Phase 5 — External Frame Limiter

If EU IV does not reliably limit its own frame rate, investigate a small injected library.

Concept:

```text
EU IV
  ↓
OpenGL
  ↓
frame presentation
  ↓
interposed function
  ↓
frame pacing
  ↓
real presentation call
```

Potential target area:

```text
CGLFlushDrawable
```

or whichever function EU IV actually uses.

Possible interface:

```bash
eu4-power --fps 60
eu4-power --fps 48
eu4-power --fps 40
```

The limiter should measure elapsed time and delay frame presentation when necessary.

### Validation

Ensure that limiting render FPS does **not** materially reduce:

- UI responsiveness,
- simulation throughput,
- multiplayer behavior,
- game stability.

---

# 21. Phase 6 — CPU-Level Profiling

If speed-5 simulation remains the dominant problem, profile CPU execution deeply.

Compare call stacks:

```text
paused
vs
speed 5
```

Classify hotspots into:

- game simulation,
- rendering,
- synchronization,
- OS calls,
- Rosetta-related behavior.

The key question is not initially what every function does, but:

> Which execution paths account for most CPU time and power?

---

# 22. Phase 7 — Narrow Runtime Optimizations

Only after identifying a specific pathological behavior should binary-level intervention be considered.

Examples might include:

- unnecessary graphical updates,
- excessive polling,
- overly aggressive redraw loops,
- avoidable high-frequency update routines.

Possible techniques:

- dynamic library interposition,
- function hooking,
- runtime patching,
- LLDB experimentation,
- Ghidra/Hopper static analysis.

Any modification must be tested against:

- save integrity,
- simulation correctness,
- UI behavior,
- crashes,
- multiplayer behavior if relevant.

---

# 23. Explicitly Deprioritized Approaches

## Full ARM64 recompilation

Attempting to translate or recompile the complete EU IV binary into ARM64 is unlikely to be worthwhile.

Reasons include:

- loss of source-level semantics,
- difficult C++ reconstruction,
- engine complexity,
- Rosetta already performing efficient translation,
- likely unrelated graphics inefficiencies remaining afterward.

## Large-scale reverse engineering

Avoid reverse engineering large parts of Clausewitz unless profiling demonstrates a specific reason.

The goal is not to understand EU IV completely.

The goal is to identify and remove a **small number of expensive behaviors**.

---

# 24. Recommended Order of Attack

The suggested order is:

```text
1. Verify architecture and rendering APIs
        ↓
2. Establish baseline power profiles
        ↓
3. Compare paused / speed 1 / speed 3 / speed 5
        ↓
4. Test 120 / 60 / 48 Hz
        ↓
5. Test Low Power Mode
        ↓
6. Test rendering resolution
        ↓
7. Test VSync / display mode
        ↓
8. Combine best configuration options
        ↓
9. Profile rendering with Instruments
        ↓
10. Build frame limiter if justified
        ↓
11. Profile CPU simulation
        ↓
12. Investigate narrow runtime patches
        ↓
13. Consider binary reverse engineering only if evidence warrants it
```

---

# 25. Decision Tree

```text
                    EU IV runs hot
                          │
                          ▼
                 establish baseline
                          │
              ┌───────────┴───────────┐
              │                       │
       hot while paused?        mainly speed-5 heat?
              │                       │
             yes                     yes
              │                       │
              ▼                       ▼
       rendering likely          simulation likely
              │                       │
       test 48/60 Hz            Low Power Mode
       resolution              CPU profiling
       VSync                   efficiency curve
              │                       │
              ▼                       ▼
      large improvement?       large improvement?
          │        │               │        │
         yes       no              yes       no
          │        │               │        │
        stop    profile         stop     profile CPU
                OpenGL                    internals
                  │
                  ▼
            frame limiter /
            narrow patches
```

---

# 26. Success Criteria

A successful configuration or intervention should ideally achieve:

### Minimum success

At least:

- clearly measurable power reduction,
- lower sustained temperature,
- no game instability.

### Strong success

Approximately:

- 30%+ reduction in sustained power consumption,
- little or no perceptible gameplay degradation.

### Excellent outcome

Approximately:

- 50%+ power reduction,
- normal UI responsiveness,
- similar speed-1–4 simulation performance,
- tolerable speed-5 reduction,
- automated configuration through a launcher.

The exact percentages are targets rather than requirements; the experimentally observed efficiency curve should determine the final configuration.

---

# 27. Potential End Product

If configuration changes alone are insufficient, the eventual deliverable could become a lightweight utility such as:

```text
eu4-power
```

Responsibilities could include:

- selecting an FPS limit,
- applying recommended game configuration,
- optionally enabling Low Power Mode,
- starting EU IV,
- collecting performance metrics,
- restoring system settings when the game exits.

Example:

```bash
eu4-power run --fps 48 --low-power
```

Potential diagnostic mode:

```bash
eu4-power benchmark
```

which could produce:

```text
EU IV Power Report

Paused
CPU:   4.2 W
GPU:   5.7 W
FPS:   117

Speed 5
CPU:  18.6 W
GPU:   5.8 W
FPS:   116

Recommended configuration:
48 Hz + Low Power Mode

Estimated power reduction:
42%
```

This would turn the investigation into a small but well-defined systems/performance-engineering project rather than an open-ended reverse-engineering exercise.

---

# 28. Immediate Next Step

The first implementation milestone should be deliberately small:

> **Build a reproducible baseline benchmark for EU IV and determine whether rendering or simulation dominates power consumption.**

Specifically:

1. inspect the EU IV executable and linked frameworks,
2. create the `powermetrics` collection script,
3. define the fixed benchmark save/scenario,
4. benchmark paused, speed 1, speed 3, and speed 5,
5. benchmark 120, 60, and 48 Hz,
6. benchmark Normal vs Low Power Mode,
7. summarize the results in a small table and plots.

Only after this data exists should any runtime modification be designed.
