# GOG EU IV root-cause diagnostic

Passive telemetry; no optimization was enabled.

In-session telemetry calibration: 60.0 → 48.0 swap calls/s (-20.0%). Short windows and scene drift limit this overhead estimate.

## Calibration Passthrough

Swap calls/s (mean of complete one-second snapshots): 59.0
Estimated CPU / GPU / combined power: 8.672 / 2.175 / 10.874 W
EU IV short timer wakeups: 1568.14/s; idle wakeups: 48.515/s
Reported GPU frequency: 338.0 MHz; CPU cluster frequencies: cpu_P0-Cluster_mhz=2136.9 MHz, cpu_P1-Cluster_mhz=2590.21 MHz, cpu_S-Cluster_mhz=4406.96 MHz
Draw calls: 0; structurally repeated-pass draws: 0 / 0
Repeated uniform updates: 0 / 0

| Function | Calls/s | Repeated state | Estimated inclusive ms/s |
|---|---:|---:|---:|
| CGLFlushDrawable | 47.2 | 0 | — |

Top threads by intercepted calls:

- Thread 9961049: 472 calls

Top sampled callers (image-relative offsets):


Telemetry table overflows: callers=0, shadow_state=0, unknown_lookups=0


## Calibration Full

Swap calls/s (mean of complete one-second snapshots): 46.4
Estimated CPU / GPU / combined power: 7.658 / 1.773 / 9.556 W
EU IV short timer wakeups: 1568.765/s; idle wakeups: 22.324/s
Reported GPU frequency: 338.0 MHz; CPU cluster frequencies: cpu_P0-Cluster_mhz=2185.825 MHz, cpu_P1-Cluster_mhz=2547.57 MHz, cpu_S-Cluster_mhz=4373.695 MHz
Draw calls: 2,227,709; structurally repeated-pass draws: 0 / 2,227,709
Repeated uniform updates: 2,060,083 / 10,448,892

| Function | Calls/s | Repeated state | Estimated inclusive ms/s |
|---|---:|---:|---:|
| glUniform1i | 739173.2 | 1,972,990 | 29.77 (229452 samples) |
| glDisableVertexAttribArrayARB | 726490.4 | 2,345,328 | 17.21 (228040 samples) |
| glVertexAttribPointerARB | 492081.2 | 702,449 | 14.31 (150619 samples) |
| glEnableVertexAttribArrayARB | 492081.2 | 0 | 11.51 (157310 samples) |
| glUniform4fvARB | 305165.4 | 87,093 | 42.35 (96020 samples) |
| glBindTexture | 294782.9 | 476,735 | 10.19 (93135 samples) |
| glActiveTextureARB | 294671.6 | 130,963 | 6.59 (92204 samples) |
| glTexEnvf | 294634.5 | 0 | 6.36 (91948 samples) |
| glBindBufferARB | 238568.9 | 334,271 | 8.62 (73465 samples) |
| glTexParameteri | 178523.5 | 0 | 4.25 (56044 samples) |
| glDrawElements | 107815.4 | 0 | 234.70 (36673 samples) |
| glDrawElementsBaseVertex | 98708.2 | 0 | 60.70 (26500 samples) |
| glUseProgramObjectARB | 43554.9 | 0 | 5.02 (15522 samples) |
| glTexParameterf | 26409.2 | 0 | 0.60 (8100 samples) |
| glDrawArrays | 16130.0 | 0 | 33.89 (5002 samples) |
| glBufferDataARB | 9344.3 | 0 | 4.92 (2940 samples) |
| glEnable | 1446.1 | 371 | 0.23 (301 samples) |
| glDisable | 1409.1 | 0 | 0.13 (228 samples) |
| glColorMask | 889.9 | 5,936 | 0.05 (329 samples) |
| glDepthMask | 741.6 | 0 | 0.03 (170 samples) |
| pthread_cond_timedwait | 635.4 | 0 | 797.45 (6357 samples) |
| glViewport | 482.0 | 2,597 | 0.05 (169 samples) |
| glBindFramebufferEXT | 407.9 | 371 | 0.18 (56 samples) |
| glBlendFunc | 370.8 | 0 | 0.02 (74 samples) |
| glClear | 296.6 | 0 | 3.20 (143 samples) |
| glScissor | 296.6 | 1,484 | 0.04 (143 samples) |
| glClearColor | 296.6 | 0 | 0.03 (43 samples) |
| glFrontFace | 296.6 | 0 | 0.01 (153 samples) |
| glVertex2f | 296.6 | 0 | 0.10 (92 samples) |
| glLoadIdentity | 148.3 | 0 | 0.01 (61 samples) |

Top threads by intercepted calls:

- Thread 9961049: 43,683,350 calls
- Thread 9961072: 6,357 calls
- Thread 9961566: 674 calls
- Thread 9961452: 78 calls

Top sampled callers (image-relative offsets):

- Thread 9961049, glUniform1i: eu4+0x15ebc7a, 217505 sampled calls
- Thread 9961049, glEnableVertexAttribArrayARB: eu4+0x15eaa47, 157310 sampled calls
- Thread 9961049, glDisableVertexAttribArrayARB: eu4+0x15ebfa1, 155492 sampled calls
- Thread 9961049, glVertexAttribPointerARB: eu4+0x15eaa27, 150619 sampled calls
- Thread 9961049, glBindTexture: eu4+0x15ecd25, 93135 sampled calls
- Thread 9961049, glActiveTextureARB: eu4+0x15ecd08, 92204 sampled calls
- Thread 9961049, glTexEnvf: eu4+0x15ecd44, 91948 sampled calls
- Thread 9961049, glUniform4fvARB: eu4+0x15eaaf8, 88830 sampled calls
- Thread 9961049, glDisableVertexAttribArrayARB: eu4+0x15ebc06, 65419 sampled calls
- Thread 9961049, glBindBufferARB: eu4+0x15ea9ba, 41342 sampled calls
- Thread 9961049, glDrawElements: eu4+0x14c8409, 33914 sampled calls
- Thread 9961049, glBindBufferARB: eu4+0x15ec2b1, 29483 sampled calls

Observed wait duration buckets:

- nanosleep, actual <1 ms: 371 calls
- nanosleep, actual >=5 ms: 752 calls
- nanosleep, requested <1 ms: 371 calls
- nanosleep, requested >=5 ms: 752 calls
- pthread_cond_timedwait, actual 1–2 ms: 6,356 calls

Observed framebuffer passes (draw calls are submission counts):

- FBO 0: 742 passes, 2,225,854 draws, 0 in structurally repeated passes
- FBO 1: 371 passes, 371 draws, 0 in structurally repeated passes
- FBO 2: 371 passes, 371 draws, 0 in structurally repeated passes
- FBO 3: 371 passes, 371 draws, 0 in structurally repeated passes
- FBO 7: 371 passes, 371 draws, 0 in structurally repeated passes
- FBO 8: 371 passes, 371 draws, 0 in structurally repeated passes

Draw size (submitted vertices or indices per call):

- 4–31: 215,922 draws
- 32–255: 1,430,205 draws
- 256–4095: 557,467 draws
- 4096+: 24,115 draws
- Submitted vertices/indices across draws: 781,756,344
- Primitive-mode counts: GL enum 4=2,104,166, GL enum 5=123,172, GL enum 7=371

Telemetry table overflows: callers=0, shadow_state=4823, unknown_lookups=0


## Paused Idle

Swap calls/s (mean of complete one-second snapshots): 48.0
Estimated CPU / GPU / combined power: 7.611 / 1.719 / 9.323 W
EU IV short timer wakeups: 1568.55/s; idle wakeups: 20.378/s
Reported GPU frequency: 338.0 MHz; CPU cluster frequencies: cpu_P0-Cluster_mhz=2223.6 MHz, cpu_P1-Cluster_mhz=2566.39 MHz, cpu_S-Cluster_mhz=4380.63 MHz
Draw calls: 16,441,281; structurally repeated-pass draws: 0 / 16,441,281
Repeated uniform updates: 15,190,512 / 77,106,152

| Function | Calls/s | Repeated state | Estimated inclusive ms/s |
|---|---:|---:|---:|
| glUniform1i | 909113.9 | 14,552,932 | — |
| glDisableVertexAttribArrayARB | 893530.1 | 17,303,832 | — |
| glVertexAttribPointerARB | 605248.1 | 5,182,931 | — |
| glEnableVertexAttribArrayARB | 605248.1 | 0 | — |
| glUniform4fvARB | 375421.9 | 637,580 | — |
| glBindTexture | 362620.6 | 3,518,107 | — |
| glActiveTextureARB | 362483.8 | 966,446 | — |
| glTexEnvf | 362438.2 | 0 | — |
| glBindBufferARB | 293440.0 | 2,466,904 | — |
| glTexParameteri | 219673.9 | 0 | — |
| glDrawElements | 132636.4 | 0 | — |
| glDrawElementsBaseVertex | 121422.4 | 0 | — |
| glUseProgramObjectARB | 53568.5 | 0 | — |
| glTexParameterf | 32508.9 | 0 | — |
| glDrawArrays | 19841.7 | 0 | — |
| glBufferDataARB | 11494.5 | 0 | — |
| glEnable | 1778.9 | 2,596 | — |
| glDisable | 1733.3 | 0 | — |
| glColorMask | 1094.7 | 43,699 | — |
| glDepthMask | 912.3 | 0 | — |
| pthread_cond_timedwait | 752.0 | 0 | — |
| glViewport | 593.0 | 19,155 | — |
| glBindFramebufferEXT | 501.7 | 2,629 | — |
| glBlendFunc | 456.1 | 0 | — |
| glClear | 364.9 | 0 | — |
| glScissor | 364.9 | 10,843 | — |
| glClearColor | 364.9 | 0 | — |
| glFrontFace | 364.9 | 0 | — |
| glVertex2f | 364.9 | 0 | — |
| glLoadIdentity | 182.5 | 0 | — |

Top threads by intercepted calls:

- Thread 9961049: 322,366,632 calls
- Thread 9961072: 45,138 calls
- Thread 9961566: 4,780 calls
- Thread 9961452: 555 calls

Top sampled callers (image-relative offsets):


Observed framebuffer passes (draw calls are submission counts):

- FBO 0: 5,510 passes, 16,427,625 draws, 0 in structurally repeated passes
- FBO 2: 2,738 passes, 2,738 draws, 0 in structurally repeated passes
- FBO 3: 2,738 passes, 2,738 draws, 0 in structurally repeated passes
- FBO 7: 2,738 passes, 2,738 draws, 0 in structurally repeated passes
- FBO 8: 2,738 passes, 2,738 draws, 0 in structurally repeated passes
- FBO 1: 2,704 passes, 2,704 draws, 0 in structurally repeated passes

Draw size (submitted vertices or indices per call):

- 4–31: 1,593,516 draws
- 32–255: 10,554,990 draws
- 256–4095: 4,114,805 draws
- 4096+: 177,970 draws
- Submitted vertices/indices across draws: 5,771,867,589
- Primitive-mode counts: GL enum 4=15,529,527, GL enum 5=909,016, GL enum 7=2,738

Telemetry table overflows: callers=0, shadow_state=81120, unknown_lookups=0


## Candidate ranking

- **GL state suppression** (coverage limited): 29,495,358 of 191,033,008 observed state calls repeated tracked values; sampled call time was disabled for this phase.
- **uniform update suppression** (low): 15,190,512 of 77,106,152 observed uniform updates repeated a tracked value (arrays larger than 256 bytes are excluded).
- **timer/wait source** (investigate): 886/s observed wait calls versus 1568.55 EU IV short timer wakeups/s; inspect caller and duration histograms before any change.
- **render-pass caching** (low): 0 of 16,441,281 draw calls occurred in structurally repeated passes; pixel identity unproven.
- **draw batching** (research only): 4,235,689 of 16,441,281 adjacent draws shared the tracked state signature; ordering constraints untested.

## Screen-image check

Capture status: captured.
- whole screen changed fraction: 60.64%
- map center changed fraction: 62.57%
- upper left ui changed fraction: 56.56%
- lower right ui changed fraction: 31.98%

## Interpretation limits

- Swap calls are not independently measured displayed frames.
- Call durations are sampled inclusive elapsed time, not GPU time.
- Redundancy is inferred from per-thread shadow state and can be uncertain if a GL context moves between threads or an unwrapped call changes state.
- Draw-state matches are batching upper bounds; pass hashes do not prove equal pixels.
- Power values are estimated system-wide SoC power, not EU IV-only watts.
