# Paused full-screen frame-swap result

The passive OpenGL interposer run at
`results/20260926T220508Z-paused-interpose/` completed with the disposable
Venice save paused, GOG EU IV v1.37.5.0 Inca, Normal power mode on AC,
3456×2234 full-screen graphics, VSync enabled, and a verified 120 Hz display.
The executable hash, save hash, mod-selection hash, render-settings hash, and
VSync setting match the earlier verified paused power run
`results/20260926T200550Z-paused-t2/`. Direct game launch did not change
those recorded controls.

The 10-second capture yielded nine complete one-second buckets after dropping
the boundary bucket. The median was **87.4 OpenGL swap calls/s**; the bucket
range was **77.4–88.3/s**. The source is `frame-swaps.csv`, with the capture
summary in `metadata.json`. These count `CGLFlushDrawable` calls inside the
game, rather than display refreshes or independently observed on-screen
frames. The counter passed each call to the original OpenGL function. Its
small per-call overhead was not quantified, so treat the rate as approximate.

This rejects a **120 FPS limiter** as a power fix for the measured paused
direct-launch state: the game was already making fewer than 120 swap calls
per second. Starting directly with the interposer differs from starting via
Galaxy, although the recorded binary, save, mods, and graphics controls
matched; an unmeasured launcher effect cannot be excluded. The rest of the
launch log includes menus, transitions, and activity after the measured
window; some buckets reached roughly 115/s, but they are not a controlled
paused comparison. Do not infer a steady gameplay FPS from them. A cap below
the observed paused rate would reduce smoothness, contrary to the user's
full-experience requirement, and is not warranted by the current evidence.

The older paused CPU profile places much of main-thread activity in map mesh
rendering and OpenGL draw submission. Apple notes that many draw calls can be
CPU-bound and that its multithreaded OpenGL engine may improve throughput in
some applications, but also adds copying and thread overhead and requires
measurement. The GOG game accepted `CGLEnable(..., kCGLCEMPEngine)` on both
observed contexts: `results/20260927T062445Z-paused-mp/` records a successful
enable result and an enabled state afterward for each. It used the same GOG
binary, disposable save, mod selection, render settings, Normal power mode,
3456×2234 fullscreen, and verified 120 Hz display as the passive direct-launch
baseline. The nine complete paused buckets had median **102.4 swap calls/s**,
range **89.7–102.6/s**, or **17.2% above** the 87.4/s baseline. The last six
buckets clustered around 102.2–102.6/s. The earlier attempt at
`results/20260927T062356Z-paused-mp/` is incomplete because the operator
confirmed the game state before the first full logging interval had elapsed;
its context-enable log is not a frame-rate measurement. The launcher now waits
briefly for a first bucket after confirmation.

This met the prespecified ~15% paused swap-rate screen, but did not reproduce.
The operator relaunched with multithreaded OpenGL for the power test. The
second completed frame screen, `results/20260927T064044Z-paused-mp/`, reported
both contexts enabled yet measured median **66.9 swap calls/s** in nine
complete paused buckets. Its longer raw log remained near 67/s through the
following power capture. The two multithreaded launches had the same recorded
binary, save hash, mod selection, render-settings hash, Normal power mode, and
verified 120 Hz full-screen display. Camera position, map contents, transient
load, and thermal state were not independently controlled. The difference
therefore prevents a claim that multithreading caused the first launch's
apparent improvement. These are swap calls, not independently observed frames.

The guarded 60-second paused power capture
`results/20260927T064212Z-paused-t3/` used that second multithreaded process
and matched its PID and recorded controls. It measured median estimated CPU
power **11.95 W**, GPU power **2.84 W**, and combined CPU + GPU power **14.80 W**;
EU IV process CPU activity was **175.2%**. The earlier verified 120 Hz standard
paused run `results/20260926T200550Z-paused-t2/` measured **7.99 W CPU**, **1.67
W GPU**, **9.70 W combined**, and **125.8%** EU IV CPU. The combined difference
is **+5.10 W** (about **+52.5%**), far larger than the spread of the standard
paused observations. These are system-wide SoC estimates, not EU IV-only
watts. The baseline launched through Galaxy, so the power difference is not
strictly attributable to the OpenGL option; the unverified camera/map state
also limits interpretation.

The candidate has neither a reproducible paused speed benefit nor a favorable
power screen. Stop here, use the ordinary GOG launch, and do not ask for a
speed-5 or repeat power run on this branch. The report now labels the
multithreaded run separately so it cannot be pooled with standard graphics
engine runs.

References: [Apple on OpenGL draw-call overhead](https://developer.apple.com/library/archive/documentation/3DDrawing/Conceptual/OpenGLES_ProgrammingGuide/Performance/Performance.html),
[Apple on the multithreaded OpenGL engine](https://developer.apple.com/library/archive/documentation/GraphicsImaging/Conceptual/OpenGL-MacProgGuide/opengl_threading/opengl_threading.html).
