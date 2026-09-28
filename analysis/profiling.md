# Paused GOG EU IV CPU profile

The 10-second `sample` capture at `results/20260926T210454Z-paused-cpu/` was
taken with the Venice save paused, Normal power mode, and EU IV foreground in
3456×2234 full-screen mode at a verified 120 Hz. The process was the GOG
x86-64 build running under Rosetta. The raw call graph is in `cpu-sample.txt`;
the profile captured 477 stack snapshots per thread.

On the main thread, 291 of 477 snapshots (61%) passed through
`CInGameIdler::Render()`. The largest branch was map drawing:
`CEU3GraphicalMap::Render()` → `CGraphics::RenderBuckets()` →
`CPdxMeshObject::RenderBuckets()` → OpenGL `glDrawElements` →
`AppleMetalOpenGLRenderer`. A smaller branch (12 snapshots) passed through
`CGraphics::PresentScene()` → `Cocoa_GL_SwapWindow()` →
`CGLFlushDrawable`. Many other threads were waiting on conditions or event
queues in this snapshot.

This supports CPU-side rendering work as a major contributor while paused.
The counts are **inclusive stack snapshots**, not exclusive CPU time, FPS, or
watts; a stack can include blocking time. The presence of a Rosetta thread
does not establish Rosetta as a significant power cost. No paused simulation
hot path stood out in the sampled main thread.

The power screens point in the same direction but also set a limit on simple
changes. The verified 120 Hz full-screen paused run had median estimated CPU
power 7.99 W, GPU power 1.67 W, and combined CPU + GPU power 9.70 W. The
60 Hz borderless run changed reported game resolution to 1728×1117 and cut
GPU power to 0.58 W, while CPU power remained 8.32 W; the combined figure was
8.92 W. Because refresh rate, window mode, and game resolution changed
together, this does not identify which factor caused the GPU reduction.

Preserve the user's full map appearance, original resolution, and 120 Hz
full-screen experience. Do not propose removing graphical features as the
optimization. The first FPS diagnostic was an untimed paused reading with the
[GOG GALAXY overlay](https://docs.gog.com/gc-overlay/) (its documented
FPS-counter shortcut is Control-Shift-Tab), if that overlay works for this
macOS game. The overlay may be unavailable for individual macOS titles. Its
reading is a quick screen, not an instrumented frame-time trace. A 120 Hz
display mode alone is not evidence of 120 rendered frames per second.

The first Control-Shift-Tab attempt showed no FPS information. Galaxy's local
user configuration has `featureOverlay: true`, but this does not show whether
the overlay initialized for EU IV. A single Shift-Tab check can distinguish an
unavailable overlay from an FPS counter that merely failed to toggle. The
installed Command Line Tools do not provide Instruments, and an unsandboxed
unprivileged DTrace probe listing reported that additional privileges are
required. The subsequent `sudo` check reached the process but failed with
`DTrace cannot instrument translated processes`. The installed GOG `eu4`
binary has only an x86_64 slice and runs under Rosetta on this Apple Silicon
Mac. This is a DTrace instrumentation limitation, not a missing tool or a
password problem; no further DTrace runs or macOS security changes are
warranted. The probe command remains available for a future native build or
Intel Mac, but is not a measurement option here. Do not ask for another timed
power run on this basis.

An experimental, logging-only alternative is in
`benchmark/eu4_frame_counter.c`. It interposes `CGLFlushDrawable` inside an
x86_64 process and writes one-second swap-call counts. An x86_64 test program
under Rosetta confirmed that macOS loaded the library and produced counts.
The subsequent guarded GOG capture also succeeded. Its binary, save, mods,
render settings, and VSync matched the earlier verified paused power run. It
measured 87.4 swap calls/s median at 120 Hz in the paused scene. The result
and its limits are in `analysis/frame-rate.md`. A 120 FPS cap has no paused
headroom; do not repeat this measurement or pursue a lower cap under the
user's current full-experience requirement.

The measured paused swap rate is below 120/s, so a 120 FPS cap would do
nothing in that scene. A lower cap would trade smoothness for power and does
not meet the user's criterion.
[Apple's OpenGL guidance](https://developer.apple.com/library/archive/documentation/GraphicsImaging/Conceptual/OpenGLProfilerUserGuide/Strategies/Strategies.html)
identifies `CGLFlushDrawable` as a typical double-buffered frame boundary,
and this call appears in EU IV's profile. A lower-level measurement of that
call is a fallback only if the GOG overlay is unavailable. No additional
60/120 Hz power repeats are warranted by the current modest net saving.

The current settings already disable shadows, reflections, water, sky,
particles, and moving units. Trees, city sprawl, ambient objects, post-effects,
bloom, and high-resolution terrain remain enabled. The user has previously
tried reducing drawing options and found the visual cost unacceptable, so
further graphical cuts are out of scope. If frame pacing has no headroom, any
later rendering work should target overhead without removing visual content,
and should be justified by a more precise trace before implementation.
