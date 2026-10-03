# EU IV GOG power benchmark

## Current profiler status

The paused-frame investigation supersedes border batching as the immediate next
action. The profiler now reports semantic attribution and envelope residuals,
uses acknowledged measurement epochs, and segments GPU timestamps by context
and pass. Sample windows now require complete plain neighbors, state/query
preparation runs unmeasured, GL accounting is aggregated per frame, and writer
output is batched. Draw API coverage fails closed for unknown submission paths.
**Tier-1 qualification ended with WP11** (`33339073`, failed v4 admission). Absolute
timings are **not** quantitatively qualified for production claims. The **intrusive diagnostic contract** is available via preflight; **Phase C** live
capture uses `run --diagnostic-only` (R–C–R–C–R after warm-up):

```sh
python3 benchmark/eu4_frame_model.py preflight --intrusive-diagnostic-contract
python3 benchmark/eu4_frame_model.py run --diagnostic-only --output results
```

See [live diagnostic measurement contract](docs/live-diagnostic-measurement-contract.md),
[post-WP11 roadmap](docs/eu4-live-diagnostic-roadmap.md), and
[the verification record](analysis/frame-model-verification.md).
Default `run` (without `--diagnostic-only`) still requires passing offline causal admission.

The [Phase II draw-path decision](analysis/draw-path-decision.md) remains
historical screening evidence: 31 complete paused frames at 5,863 draws/frame
identified a recurring border run. Its trace failed its intrusion gate, so it
does not establish an optimization benefit. The local trace can be re-screened:

```sh
python3 benchmark/eu4_draw_trace.py screen-partial results/20260928T113359Z-draw-trace
```

The pinned offline inventory and synthetic GL harness can be verified with
`python3 benchmark/eu4_draw_static.py verify` and
`python3 benchmark/eu4_draw_trace.py preflight`.

This repository measures the **GOG** copy of Europa Universalis IV at
`/Applications/EuropaUniversalisIV`. The collector refuses to record a game run
unless the running `eu4` process has that exact executable path. It reads the
GOG launcher's separate user-data folder, currently
`~/Documents/Paradox Interactive/Europa Universalis IV GOG`.

The installed build inspected on 26 September 2026 is EU4 v1.37.5.0 Inca,
x86-64, linked to OpenGL/GLUT and GOG Galaxy. The default fixture is the GOG
folder's disposable `save games/Venice1444_11_11.eu4`. Its presence and hash
are checked at run time. The save metadata identifies Venice on 1444.11.11,
and GOG `settings.txt` currently reports `autosave="NEVER"`.
The active GOG playset currently includes Proper 2K UI Enhanced; keep the same
playset and DLC selection throughout a comparison.

## Prepare

Run from an interactive macOS Terminal, using Python 3.10 or later:

```sh
python3 benchmark/eu4_benchmark.py inspect
```

Check that the displayed GOG path, game version, save, settings, and power mode
are correct. Load `Venice1444_11_11.eu4` in the GOG version once to confirm
compatibility.
Run `inspect` again while the game is open: the desktop currently reports
120 Hz, while the game's saved graphics setting says 60 Hz, so fullscreen may
change the actual display mode. The recorder checks the mode again after you
switch back to EU IV and before sampling. Match the idle control to that
verified in-game mode. Earlier game captures checked the mode before switching
back; the report marks their refresh rate unverified. The display reports fixed
120, 60, and 48 Hz modes at the current
resolution, with fractional 59.94 and 47.95 Hz variants.
Keep its camera position and map mode fixed. Use the disposable Venice save for
all new game runs. The earlier Lübeck Ironman paused run is still useful as a
standalone observation but is not a paired baseline for the Venice fixture.
The collector blocks speed runs with autosave enabled by default, rejects
unexpected save changes, and keeps one recovery copy per save hash under
`results/_save_backups/`.

If you deliberately benchmark the Ironman campaign itself, pass
`--allow-autosave` on speed runs. An Ironman speed run advances and may overwrite
that campaign. The collector records the save hash before and after capture and
observed save-file changes; autosave stalls are then part of the measured
workload. Reload the same starting state before each paired run if the game
permits it. Do not assume that copying or renaming an Ironman save preserves
achievements or protects the original campaign.
Keep the Mac connected to the same power source, let it reach a stable
temperature, close unrelated heavy apps, and leave GOG Galaxy running during
both idle and game measurements so its background activity is comparable.

`powermetrics` requires administrator access. The `record` command asks `sudo`
to authenticate **before** the timed scenario starts. Enter the password only
in your own Terminal; it is never stored by this project. The standard
`eu4_benchmark.py record` command does not alter system power or display
settings. The separate paused-frame profiler temporarily switches to Low Power
Mode for its final comparison and restores Normal mode in cleanup.

Before the full matrix, verify the local `powermetrics` format with a short
idle smoke test (**EU IV closed**). This command does not launch the game:

```sh
python3 benchmark/eu4_benchmark.py record --scenario idle --trial 1 --seconds 5 --output /tmp/eu4-power-smoke
```

Inspect the resulting `samples.csv` for CPU and GPU watts. Use the full
90-second idle runs under `results/` for analysis; the smoke test is too short
to include in a report.

## Record

Launch the GOG copy through GOG Galaxy yourself, load the fixture, warm up the
game for about two minutes, and screen paused, speed 1, speed 3, and speed 5
once each. Reload the same starting save before each game run. The first pass
uses 90-second runs; do not automatically repeat every condition or close the
game for an idle control. First use these runs to choose a practical intervention.
The disposable Venice fixture is now the default. Use `--save` only to select
another fixture explicitly.

```sh
python3 benchmark/eu4_benchmark.py record --scenario paused --trial 1
python3 benchmark/eu4_benchmark.py record --scenario speed1 --trial 1
python3 benchmark/eu4_benchmark.py record --scenario speed3 --trial 1
python3 benchmark/eu4_benchmark.py record --scenario speed5 --trial 1
```

Repeat a condition only when a configuration looks promising or the result is
too small to distinguish from drift. The script prompts for confirmation
that the GOG save is loaded, the in-game start and end dates for running-speed
scenarios, and when to start capture. After pressing Enter for a game scenario,
switch straight back to EU IV; sampling starts after a five-second lead-in so
the game remains foreground. A system sound marks the start and end of capture;
resume a speed scenario at the first sound and pause at the second. You can
change the lead-in with `--lead-in`.
Pause promptly at the end of each timed
speed run before entering its end date. Days per second is approximate because
manual switching takes time; keep that delay similar across runs. Optional
observed FPS can be supplied with `--fps` or added later:

```sh
python3 benchmark/eu4_benchmark.py annotate results/RUN_DIRECTORY --fps 60
```

If a reliable FPS counter is unavailable, leave FPS blank. Do not infer FPS
from the display's refresh setting.

The current one-pass screen shows that speed 5 raises CPU power substantially,
while the paused game still uses about 9.7 W of estimated CPU + GPU SoC power.
Changing the macOS desktop to 60 Hz did **not** change this build's full-screen
EU IV mode: the collector observed 120 Hz after the game regained focus and
stopped before sampling. Do not repeat that timed run. Use the quick
`probe-display` command, which needs no `sudo` or power capture, to test any
future display-mode workaround before benchmarking it:

```sh
python3 benchmark/eu4_benchmark.py probe-display --expect-hz 60
```

The Low Power Mode speed-5 screen at 120 Hz reduced estimated CPU + GPU power
from 12.45 to 5.86 W, while throughput fell from 10.72 to 7.16 days/s. The
power drop was steady, but the 60- and 90-second captures cover different
amounts of game time. Low Power Mode also dims this user's screen, making it
an unsuitable everyday solution despite its power result. Do not spend another
run testing speed 3 in Low Power Mode; restore Normal mode.

For a non-dimming intervention, borderless/windowed-fullscreen with the macOS
desktop at 60 Hz passed the untimed `probe-display` check: EU IV remained at
60 Hz in the foreground. It also changed the game's reported resolution from
3456×2234 to 1728×1117, while the display retained 3456×2234 physical pixels.
The 60-second paused screen found median estimated GPU power of 0.58 W versus
1.67 W in the verified 120 Hz full-screen baseline, but CPU power remained
about 8 W. Combined CPU + GPU power was 8.92 versus 9.70 W; the 0.79 W
difference is close to the spread between the two 120 Hz Venice paused runs.
Thus the configuration reduces GPU work, but its overall SoC saving is not yet
convincing. It also changes refresh rate, window mode, and game resolution
together. Do not spend another timed run separating those factors unless the
borderless view is preferable for daily use. Restore the original 120 Hz
full-screen configuration for the next investigation, which inspects paused
CPU call stacks with a **10-second profile**, using the same foreground
lead-in but no save progression or power run:

```sh
python3 benchmark/eu4_benchmark.py profile-cpu
```

This authenticates `sudo` before the lead-in because macOS restricts sampling
another process. The call stacks and metadata are saved under `results/`. The
first 120 Hz full-screen profile is interpreted in `analysis/profiling.md`.
It found most sampled main-thread activity in paused map rendering and OpenGL
draw submission. The user has found that reducing map drawing options hurts
the experience, so retain the current visuals and 120 Hz full-screen mode.
The GOG overlay shortcut yielded no FPS information. DTrace could not
instrument this x86_64 build under Rosetta, even with `sudo`; there is no
reason to retry it or change macOS security settings. A passive x86_64
`CGLFlushDrawable` interposer did work in a local test and in the GOG game.
Its guarded direct-launch workflow is available as
`python3 benchmark/eu4_frame_counter.py` for a different future condition,
but **do not repeat the current capture**. It logs OpenGL swap calls rather
than guaranteed displayed frames and changes no game settings or executable.

The guarded GOG capture succeeded: while the Venice save was paused at
verified 120 Hz full screen, it recorded a median **87.4 swap calls/s** over
nine complete buckets. Its binary, save, mod selection, and graphics settings
matched the verified paused power baseline. A 120 FPS cap therefore has no
headroom to save energy in this measured paused direct-launch state. Do not
repeat this frame-count run or attempt a lower cap under the current
full-experience requirement. The result
and limits are in `analysis/frame-rate.md`.

Apple's multithreaded OpenGL engine was tested as a reversible, full-visual-
quality candidate. The first controlled GOG paused screen recorded **102.4
swap calls/s**, apparently 17.2% above the 87.4/s standard direct-launch
screen. This did **not reproduce**: a second multithreaded launch with the
same recorded binary, save, mods, render settings, Normal power mode, and
120 Hz full-screen display measured **66.9 swap calls/s**. A guarded 60-second
power capture of that same second process measured median estimated CPU + GPU
power of **14.80 W**, versus **9.70 W** in the verified standard paused run.
The earlier power run used Galaxy, and camera/map state was not independently
verified, so the difference is a screening observation rather than a causal
power estimate. It is much larger than the variation between standard paused
runs and provides no reason to spend another manual run on speed 5. Stop this
candidate and use the ordinary GOG launch. The full evidence and limits are
in `analysis/frame-rate.md`. [Apple's documentation](https://developer.apple.com/library/archive/documentation/GraphicsImaging/Conceptual/OpenGL-MacProgGuide/opengl_threading/opengl_threading.html)
notes that the engine can add copying and synchronization costs.

## One-launch root-cause diagnostic

`benchmark/eu4_diagnostic.py` prepares a passive x86-64 interposer for the
next investigation. It covers all 44 GL entry points directly imported by the
installed GOG executable, wraps a broader set of modern GL functions obtained
through `dlsym`, and counts selected timer/wait calls. It records bounded
per-thread counters and caller/pass summaries once per second, not one log
line per GL call. It does not enable multithreaded OpenGL or suppress any game
call. The offline Rosetta/OpenGL harness and static coverage check run before
the game launches. Keep the disposable Venice save and original 120 Hz
full-screen settings.

Close EU IV, then run this from an interactive Terminal:

```sh
python3 benchmark/eu4_diagnostic.py run
```

Enter the administrator password when prompted. The command directly launches
the installed GOG executable. Load and pause the Venice save, confirm the
expected DLC/mods, then follow the Enter prompt and return to the game. Leave
the game untouched until the second sound. The script runs 10 seconds of
near-pass-through calibration, 10 seconds of full telemetry, then a 60-second
paused capture. It asks for a 20-second paused map-interaction phase **within
the same launch** only when repeated pass structure and screen images make
that comparison useful; return to Terminal after the second sound to see that
prompt. If screen capture is unavailable, the power and GL diagnosis proceeds
without images. The diagnostic stays paused; prior runs already measured
simulation at speeds 1, 3, and 5.

The timestamped `results/*-diagnostic/` directory holds raw periodic telemetry,
power samples, phase markers, optional screen images, metadata, and
`root-cause.md` / `root-cause.json`. The report separates observed counts from
sampled elapsed times and marks unknown GL lookups, bounded-table overflow,
and calibration changes. A pass fingerprint is not proof of identical pixels;
matching draw state is only an upper bound for batching. If the diagnostic
identifies a safe reversible intervention, its validation will use a later
single launch with OFF → ON → OFF → ON periods.

The [updated diagnostic interpretation](analysis/diagnostic.md) explains why
the original state-cache dismissal was premature and records the static GOG
renderer findings.

## Unattended paused baseline (P0)

The [autonomous runner](benchmark/autonomous_runner.py) pins the GOG executable,
the [Venice fixture](fixtures/venice_paused.eu4), render settings, and DLC/mod
selection. It starts the disposable save with `--continuelastsave`, checks a
fresh Venice game log plus the in-game paused signal, activates the EU IV
window, warms up for at least 90 seconds, and records 30 seconds of CPU,
power, and swap samples. It restores the original `continue_game.json` after
the game exits. An interrupted run leaves a recovery journal that is applied
before the next run, once EU IV is closed.
The runner parks the mouse pointer at screen center as EU IV comes forward and
checks that it stays away from the screen edges. EU IV scrolls the map when the
pointer rests at an edge, which caused camera drift in earlier unattended runs.

The runner uses a narrowly scoped, root-owned `powermetrics` helper. Review
[`benchmark/powermetrics_helper`](benchmark/powermetrics_helper) and
[`benchmark/install_powermetrics_helper.sh`](benchmark/install_powermetrics_helper.sh)
before the **one-time** administrator setup:

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install -r benchmark/requirements.txt
python3 benchmark/autonomous_runner.py preflight
sudo sh benchmark/install_powermetrics_helper.sh
```

The installer allows passwordless `sudo` only for the installed, no-argument
helper. Subsequent runs do not request an administrator password. Run from the
logged-in macOS desktop session with the display at 120 Hz, Normal power mode,
and EU IV closed. The first run establishes a visual scene reference:

```sh
python3 benchmark/autonomous_runner.py run --bootstrap
```

Review the resulting `ready-scene.png`. If it shows the intended paused Venice
map, register it and collect unattended baseline repetitions:

```sh
python3 benchmark/autonomous_runner.py register-scene results/RUN_DIRECTORY
python3 benchmark/autonomous_runner.py repeat
```

If three successful runs already exist, including the registered bootstrap run,
assess them without launching EU IV again:

```sh
python3 benchmark/autonomous_runner.py assess results/RUN_1 results/RUN_2 results/RUN_3
```

Each run writes `manifest.json`, `events.jsonl`, the original power stream,
the passive swap/paused-state log, a screenshot, and `summary.json`/`.md`.
`repeat` and `assess` write `results/autonomous-reproducibility.json` and accept a settled
baseline only below 3% EU IV CPU-time variation, 5% combined-power variation,
and 3% swap-rate variation. The visual guard records small startup camera
offsets and rejects a substantially different camera or map mode. The registered
bootstrap run and two later completed runs yielded 2.31% CPU-time, 1.07%
combined-power, and 0.66% swap-rate variation. Their [reproducibility report](results/autonomous-reproducibility.json)
marks the paused baseline stable. Existing manually staged benchmark commands
remain available. Those three results predate pointer parking; the updated
pointer handling passed offline preflight but has not yet been measured in a
new game run.

## State-cache validation

The final fine-grained state hypothesis is tested separately by the
[sampler-uniform controller](benchmark/eu4_sampler_uniform.py). It uses the
same unattended Venice fixture and installed power helper, then runs a paused
`A–U–A–U–A` comparison with 30-second phases after a 90-second warm-up. `A`
forwards every sampler assignment; `U` suppresses duplicate assignments only
from the 16 verified `SShaderOpenGL::SetAll()` loop calls. The seventeenth
tail-called assignment always forwards. The shim discards its cache on GL
context switches, which occur frequently in EU IV.

```sh
python3 benchmark/eu4_sampler_uniform.py preflight
python3 benchmark/eu4_sampler_uniform.py run
```

The run saves a manifest, aligned power/swap/sampler counts, five phase
screenshots and `validation.json`/`.md`. The completed
[validation](results/20260928T081714Z-sampler-uniform/validation.md) suppressed
about 68% of the targeted calls in both U phases, but found no repeatable CPU
or power saving. The five captured screenshots show the same paused scene;
the [interpretation](analysis/sampler-uniform-validation.md) explains why this
does not justify a direct executable patch. The related
[draw-bucket analysis](analysis/render-buckets.md) uses the pinned binary only;
it does not require another game launch.

## Earlier state-cache validation

The state-cache experiment uses a single GOG launch with a lightweight, switchable
[state-cache controller](benchmark/eu4_state_cache.py). The executable hash is
pinned to the installed GOG v1.37.5 build. The shim starts in pass-through
mode and leaves the game executable, graphics settings, and simulation intact.
Its preflight rejects uncovered GOG GL state call sites. The shim clears its
shadow state when the rendering context changes and forwards calls from other
threads while invalidating the render thread's cache.

The offline preflight builds the x86-64 dylib and harness, compares rendered
pixels and GL state across all modes, tests mode transitions and shared-context
correctness, and estimates pass-through cost. It does not launch EU IV:

```sh
python3 benchmark/eu4_state_cache.py preflight
```

For the one manual validation, close EU IV and run:

```sh
python3 benchmark/eu4_state_cache.py run
```

The script directly launches GOG EU IV. Load and pause the disposable Venice
save, confirm the intended DLC/mods, then press Enter and return to fullscreen
EU IV. Leave the game untouched for six 20-second phases, marked by sounds:
pass-through → texture cache → pass-through → texture+vertex cache →
pass-through → texture+vertex+uniform cache. A seventh sound marks completion.
Return to Terminal to report whether you saw a rendering defect. If the texture
shim sees no GL calls eight seconds into its first enabled phase, the controller
stops early instead of spending the rest of the manual session. A change to
the disposable save is recorded but does not discard completed capture phases.
The script restores pass-through mode even if capture is interrupted. The
report appears in `results/*-state-cache/validation.md`; the adjacent pass-through phases
help distinguish cache effects from drift. Keep the display at 120 Hz and
Normal power mode; no drawing option or FPS cap is changed.

The first [validation capture](results/20260927T135005Z-state-cache/validation.md)
completed all six phases but measured **zero suppressed calls**: the original
global context/thread guard switched the cache off. Its power differences do
not test state caching. The disposable save changed after capture and is
accepted for that report. The shim now invalidates state on context switches
and on GL writes from another thread, and the offline shared-context harness
passes. The second [capture](results/20260927T141040Z-state-cache/validation.md)
engaged the texture and vertex caches. The [interpretation](analysis/state-cache-validation.md)
finds no convincing CPU benefit from either stage after accounting for baseline
drift. Its uniform phase was invalid because a broad invalidation scan inside
the shim cut swaps in half. That scan has since been replaced with a targeted
lookup, and the offline harness verifies the fix. The corrected uniform cache
has not been measured in EU IV; no further manual run is currently requested.

## Paused-idle pacing validation

The optional [idle pacer](benchmark/eu4_idle_pacer.py) remains a fallback
candidate. It keeps the screen at 120 Hz and preserves all drawing
options. Its ON state limits OpenGL swaps to 60/s only when the exact GOG game
binary reports paused and there has been no keyboard or mouse input for three
seconds. Input, unpausing, an unknown game state, and OFF all disable the cap.
This can change the smoothness of idle animation, so the validation is a
measurement and visual check, not an automatic recommendation.

**The captured paused scene was already about 60 swaps/s with light counting,
so this run is not currently recommended.** The controller has a ten-second
OFF baseline gate and stops before the long ABAB capture if the same scene is
below 70 swaps/s.

Close EU IV, then start the single ABAB launch from Terminal:

```sh
python3 benchmark/eu4_idle_pacer.py run
```

Load the disposable Venice save and pause. After confirming the save and
DLC/mods, press Enter and return to the game. Keep it foreground and paused
through five sounds: OFF → ON → OFF → ON, each for 25 seconds, then completion.
Do not touch the input during measurement; watch whether ON animation looks
less smooth. The script writes `results/*-idle-pace/validation.md` and
automatically leaves the pacer OFF. The Rosetta/OpenGL pause-gate harness runs
before the game launches and the validation stops early if pacing does not
engage.

Defer a full idle run until game-attributable incremental power is needed; the earlier five-second
idle capture only validated the parser. Restore your preferred system settings
after the experiment.

## Paused-frame causal profiler

`benchmark/eu4_frame_model.py` builds the pinned x86-64 profiler and engine
inventory. Protocol, telemetry, and reports are version 3. The shared production
scope stack and GPU segment manager have native and x86-64/Rosetta harnesses.
The analyzer verifies the production scope serializer. Legacy telemetry remains
readable, but cannot satisfy the current coverage gates.

After offline overhead acceptance, use the residual-discovery pilot:

```sh
python3 benchmark/eu4_frame_model.py preflight --static-only
python3 benchmark/eu4_frame_model.py preflight
python3 benchmark/eu4_frame_model.py run --residual-discovery
```

The pilot keeps warm-up and calibration, then captures 20 seconds of unrestricted
`ANATIVE` (ID 110) and 20 seconds of paced A0 with two windows of two consecutive
sampled render frames. It omits interventions and power-mode transitions. Its
report kind is `residual_discovery`, with ranked envelope residuals. A balanced
timing tree does not establish semantic coverage or a definitive diagnosis.

Live runs require the registered paused Venice scene, a verified 120 Hz display,
Normal power mode, AC power, and the exact reviewed root-owned powermetrics
helper with noninteractive authorization. The current helper collects up to 1,200
one-second samples. Refresh an older installation from an interactive Terminal:

```sh
sudo sh benchmark/install_powermetrics_helper.sh
```

**Qualified/default `run`** (no `--diagnostic-only`): offline Tier-1 causal admission
must pass preflight. Live P0/P1/P2 calibration enforces ~3% reference→counters
observer-effect limits before causal phases; failed structural gates still stop
the run. Forensic sampling runs in a separate tail after causal and power-mode
phases. The `--calibration-only` option stops after bounded calibration and
forensic capture.

**Intrusive diagnostic contract** (`preflight --intrusive-diagnostic-contract`):
records failed Tier-1 admission but does not block preflight. Live intrusive
capture (`run --diagnostic-only`) runs the Phase C R–C–R–C–R schedule; live
observer effect is measured and reported, not used as a hard 3% admission gate. Do not treat profiler absolute timings as uninstrumented EU IV
measurements in either mode.

Static preflight alone cannot measure overhead. See
[the correction verification record](analysis/frame-model-verification.md)
for checks actually performed and current blockers.

Every A0–A5 control and B/C/D/E intervention holds the same calibrated update
period. ANATIVE and natural Low Power comparisons retain unrestricted cadence.
The controller rejects schedules whose worst-case budget exceeds 1,140 seconds;
the helper reserves a further minute for restoration. The combined natural and
fixed LPM schedule currently exceeds this bound and is rejected before launch.
E30/E15 report achieved rate, skip fraction, lateness, missed deadlines, and
render overruns, and require the achieved rate within 3% of target. They gate
renderer execution at absolute 30/15 Hz deadlines while updates
continue at the calibrated rate. The controller awaits measurement-enable
acknowledgement before starting its window. Detail rearming changes command
generation, while retaining the measurement epoch. Stop is acknowledged before
any power-mode transition; restoration uses the exact journaled prior setting.
There is no `sudo -v` credential-cache dependency.

The [protocol reference](analysis/frame-model-protocol.md) documents timestamps,
clock calibration, uncertainty, and the shared frame eligibility policy. Cost
statistics and all scope/detail/GPU analyses exclude boundary-crossing and
invalid frames. Rates count individual timestamped events inside the same
window used for power analysis.

Coverage counts only exclusive intervals in explicitly classified semantic
scopes: bucket insertion, flush-record creation, presentation, and drawable
flush. Pacing is reported separately and never improves acceptance. Update,
Idle, Render, graphical-map Render, and the hooked loop are envelopes; their
exclusive CPU and wall remain residual. The aggregate CPU and wall gates must
reach 95% independently for UpdateOneFrame and executed Render in baseline
controls. Per-frame distributions accompany the ratios. Wall minus thread CPU
is non-CPU elapsed time, which can include waiting and descheduling.

GPU timestamps are sampled context-lifetime/pass segments, with measurement epoch,
render identity, and sequence. Active queries are never polled. Completed
results are collected only while their owning context is current; collection
never changes contexts or waits synchronously. Missing segments are explicit.
Different context timelines are never summed, and segment intervals are never
reported as whole-render GPU busy time. Partial GPU evidence is compatible with
complete CPU accounting.

The [static evidence](analysis/frame-model-static.json) records bounded recursive
call/tail traversal, aliases, indirect edges, shader hashes/includes/features,
and enabled-mod shader override status. It stores no shader payloads. The
[Metal matrix](analysis/frame-model-backend.json) records concrete Gfx symbols
and evidence addresses while marking unresolved pointer-call dependencies and
unmeasured runtime frequencies. Metal go/no-go remains undetermined.

After residual discovery, add 3–8 verified semantic hooks targeting the largest
residuals per iteration, with ABI and overhead validation repeated. Keep these
hook additions separately reviewable. Only after integrity, cadence, overhead,
and semantic coverage pass should the long causal run be used:

```sh
python3 benchmark/eu4_frame_model.py run
```

The [Gfx-to-backend matrix](analysis/frame-model-backend.json) is a **feasibility
seed**; Metal go/no-go remains undetermined. Further reconstruction needs concrete
caller → Gfx implementation → GL evidence, resource lifecycle and state contracts,
shader features, measured frequencies, prospective Metal mappings, and unresolved
edges. The current seed does not establish a replacement boundary.

Raw `powermetrics` plists, stderr, metadata, parsed samples, and EU IV process
CPU samples are retained in a timestamped directory under `results/`.
Interrupted or failed runs remain there with `status: incomplete` and are
excluded from reports. Power figures are estimated **system-wide SoC** values,
not watts attributable solely to EU IV. The thermal sampler reports pressure,
not temperature.

## Report

```sh
python3 benchmark/eu4_benchmark.py report
```

The report writes `analysis/output/report.md`, `summary.csv`, `runs.csv`,
`power.svg`, and a `throughput.svg` plot when game-date measurements exist.
Its formal attribution rule requires three repeat runs of idle, paused, and
speed 5 at a verified common refresh rate; this is a confidence rule, **not** a
required run count for screening configurations. A paused GPU rise
supports a rendering contribution; a speed-5 CPU rise accompanied by greater
EU IV CPU activity supports a simulation contribution. Weak or mixed results
are reported as inconclusive.

Run parser and report tests with:

```sh
python3 -m unittest discover -s tests -v
```
