# GOG EU IV one-launch diagnostic: interpretation

The completed [root-cause report](../results/20260927T102247Z-diagnostic/root-cause.md)
comes from the paused disposable Venice save, with GOG EU IV v1.37.5.0,
3456×2234 full-screen output at 120 Hz, Normal power mode, and the recorded
DLC/mod selection. After excluding snapshots that straddled mode changes,
the paused phase has 57 clean one-second telemetry snapshots and 59 aligned
power samples. The two captures were compared offline because the operator's
Python lacked Pillow; the regenerated report includes pixel differences.

The main thread submitted **16.44 million draws in 57 seconds**, about
**6,000 draws per swap call**. Nearly all went directly to the default
framebuffer. The earlier [CPU profile](profiling.md) independently found
`CInGameIdler::Render()` → map mesh rendering → OpenGL draw submission.
Symbolization of the new call sites places repeated shader and vertex setup
inside `SShaderOpenGL`, `GfxSetVertexBuffers`, and
`InternalSetTextureAndSamplerState`. The most likely power/performance source
in this paused scene is continuing full map submission, rather than paused
simulation.

The telemetry itself affects throughput. In the same launch, near-pass-through
counting measured a median **60 swap calls/s**; full telemetry measured **48/s**.
The controller therefore disabled sampled timing for the 60-second paused
phase. This is a screen for measurement intrusion, not a clean A/B estimate:
the calibration windows are short and the game scene may drift. The full
phase's per-call timings must not be treated as uninstrumented driver costs.
Its reported combined power was lower than the near-pass-through phase
(9.56 W versus 10.87 W), alongside lower frame throughput and GPU activity;
that is no evidence of an optimization. The earlier uninstrumented 87.4/s
reading was another session and is not a controlled comparison with this one.

The original redundancy accounting omits `glTexEnvf`, `glTexParameteri`, and
`glTexParameterf`, together about 615,000 calls/s in the paused phase. It also
cannot bound the benefit of suppressing known repeats: a setter can mark driver
state dirty and incur part of its cost at the following draw. Of the calls it
does track, 29.5 million of 191.0 million state calls and 15.2 million of
77.1 million uniform calls repeated a tracked value. The roughly 0.54 ms/frame
of sampled inclusive setter time is **not an upper bound** on potential savings
inside Apple's later draw validation. Full telemetry itself reduced throughput,
so those timings are only diagnostic clues.

Static inspection of the installed GOG x86-64 executable (SHA-256
`b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`)
narrows the hypothesis. `GfxSetTextures` at image offset `+0x15ecbf2` calls
`InternalSetTextureAndSamplerState` at `+0x15ecce2`. The helper unconditionally
issues `glActiveTextureARB` and `glBindTexture`; when a sampler is present it
also issues `glTexEnvf`. It compares the texture's recorded sampler ID with the
requested sampler ID and issues the `glTexParameter*` sequence **only when
they differ**. The final `bool` gates anisotropy, not a general force update.
Consequently the 1:1:1 active/bind/environment pattern does not mean every
setup rewrites all texture parameters. The best first target is redundant
binding and environment state, with texture-parameter caching measured too.

`SShaderOpenGL::SetAll()` at `+0x15ebc40` unconditionally uploads 17 sampler
uniforms when selecting a valid shader. `GfxSetVertexBuffers` at `+0x15ebf41`
disables attributes from the previous layout; `EnableVertexAttribArrays` at
`+0x15ea970` binds vertex buffers and issues pointer, divisor, and enable calls
for the new layout. These paths explain the measured uniform and vertex churn
and give a bounded set of calls for an experimental state cache.
The exact GOG executable uses direct imports for the texture/environment
setters and resolves the shader and vertex extension functions through GLEW.
An offline disassembly audit distinguishes GLEW's mass initialization lookups
from executable call sites and rejects this build if an additional relevant
state-changing call site appears. A lookup alone is not evidence that EU IV
invokes that GL function.

Render-target caching is also a poor transparent intervention here. Roughly
99.9% of observed draws targeted the default framebuffer, so there is no
large isolated map pass to reuse. The pass fingerprint did not repeat, though
hashing and shadow overflow limit that test. The two screen captures differ
in 62.6% of map-center pixels at a small color threshold; hover
highlight and animated scene content contribute, so this does not quantify
how much map geometry truly changed. Adjacent draws share the tracked full
state signature only 25.8% of the time; that is an upper bound on batching,
not permission to reorder or merge geometry.

The roughly 1,569 EU IV short timer wakeups/s remained almost identical while
swap calls fell from 60/s to 48/s. About 750/s of the observed waits during
full telemetry were `pthread_cond_timedwait` through `libc++`, and about
130/s were `nanosleep` from `SDL_Delay_REAL`; the interposer did not explain
every powermetrics wakeup. Wait duration is not CPU execution time, and the
unchanged wakeup rate does not make a timer patch a credible power fix.

**Revised decision:** use the next manual launch for a lightweight
pass-through / texture / vertex / uniform state-cache comparison. The
[version-pinned controller](../benchmark/eu4_state_cache.py) requires offline
GL and pixel-equivalence checks first. Static inspection also found
that `CInGameIdler::Render()` clears the default framebuffer and then calls
`CEU3GraphicalMap::Render()` before rendering the 2D UI. Skipping the map call
would therefore require a new composition path and would not preserve the
animated map. A narrower fallback is the [version-pinned paused-idle pacer](../benchmark/eu4_idle_pacer.py):
it leaves the display at 120 Hz and rendering quality intact, but limits
animation to 60 swaps/s after three seconds without input while the game
itself reports paused. OFF is a direct pass-through. This can still affect
perceived idle animation smoothness. More importantly, the diagnostic's light
counter already measured about 60 swaps/s in this scene, so the 60/s cap has
little or no headroom here. The pacer controller stops after ten seconds of
OFF measurement if its baseline is below 70/s, preventing a full manual ABAB
run with little prospect of useful savings. It remains an optional fallback
for a higher-throughput paused scene, not the next experiment here.

The subsequent [state-cache validation](state-cache-validation.md) supersedes
the prospective decision above. Texture and vertex suppression engaged but
showed no convincing CPU benefit; the uniform stage was invalidated by a shim
bug that has been fixed and checked offline.
