# GOG EU IV state-cache validation, 27 September 2026

The second controlled paused run used the pinned GOG v1.37.5 executable and
completed all six 20-second phases. Its [raw report](../results/20260927T141040Z-state-cache/validation.md)
and [machine-readable values](../results/20260927T141040Z-state-cache/validation.json)
show that the texture and vertex caches engaged. No visible rendering defect
was reported. The save was unchanged. The earlier
[capture](../results/20260927T135005Z-state-cache/validation.md) did not engage
the cache and supplies no effect estimate.

| Phase | Cache | Skipped GL calls in 17 measured seconds | Swaps/s | EU IV CPU ms/s | CPU + GPU W |
|---|---|---:|---:|---:|---:|
| A1 | Off | 0 | 51.96 | 1202.47 | 10.55 |
| B | Texture | 8.61 million texture | 51.42 | 1102.90 | 9.43 |
| A2 | Off | 0 | 51.69 | 1117.52 | 9.72 |
| C | Texture + vertex | 8.28 million texture, 18.98 million vertex | 49.60 | 1108.60 | 9.38 |
| A3 | Off | 0 | 51.56 | 1114.15 | 9.52 |
| D | Texture + vertex + uniform | 4.16 million texture, 9.55 million vertex, **0 uniform** | 24.87 | 1078.58 | 9.54 |

The pass-through baseline fell by 7.1% in EU IV CPU time and 7.8% in combined
power from A1 to A2, while swaps remained near 52/s. Consequently, B's
comparison with the average of A1 and A2 overstates a possible texture-cache
benefit. Against the settled A2 phase alone, B is 1.3% lower in EU IV CPU
time, 3.0% lower in combined power, and 0.5% lower in swaps. Those differences
are too small to establish a useful causal effect in this run.

C compared with its adjacent settled controls A2 and A3 is 0.7% lower in EU IV
CPU time and 2.5% lower in combined power, but 3.9% lower in swaps. The vertex
cache eliminated about 1.1 million calls/s during C without a meaningful CPU
gain. This does not justify a version-pinned engine patch for texture or vertex
state on the present evidence. System-wide power estimates and one-second
samples limit the precision of these comparisons.

D is **invalid as a uniform-cache experiment**. It forwarded about 2.77 million
tracked uniform calls, suppressed none, and cut swaps by 52%. The cause was in
the shim: each other `glUniform*` setter scanned the whole 4,096-slot shadow
table and invalidated all cached uniforms, including unrelated locations. The
cost became dominant and prevented duplicates from being recognized. This was
an instrumentation defect, not evidence that uniform dedup hurts EU IV.

The shim now invalidates only the written uniform locations, using the existing
hash lookup, and resets conservatively for unusually large arrays. The offline
OpenGL harness checks both that unrelated writes preserve cached values and
that an overwrite of the same location is forwarded. The full offline preflight
and Python tests pass. This corrected code was **not** used in the game run, so
there is no measured EU IV benefit for uniform dedup. Further manual launches
should wait for offline evidence that uniform caching has enough headroom to
justify another controlled session.
