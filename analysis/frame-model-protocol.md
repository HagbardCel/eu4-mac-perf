# Frame profiler protocol and accounting v3

The v3 command is little-endian `<6I4Q>`: version, publication sequence, mode,
phase, flags, detail frame budget, update period, render period, command
generation, measurement epoch. Controller publication uses an odd sequence while
writing and an even sequence when complete. The probe snapshots at update
boundaries; calls outside an owned update refresh their thread's snapshot when
the publication sequence changes. A detail command preserves the epoch. Disabling measurement sets
command epoch zero; the next enable assigns a new nonzero epoch.

Probe-owned fields follow the command: acknowledged generation (offset 56),
hook failures (64), dropped records (72), and acknowledgement timestamp (80),
all uint64. Commands never overwrite them. Acknowledgement time is written
before publishing its generation, after completion of the update using that
snapshot. The controller waits for enable acknowledgement before `phase_start`,
and for disable acknowledgement before the next transition. Enable generation
and epoch are recorded separately in the phase manifest. A post-start command
with the same epoch establishes `window_generation`, even without detailed
sampling, so unowned producers can distinguish enable acknowledgement from the
timed window.

Telemetry begins `H,3`. F retains all v1 columns, followed by epoch, update
start/end, render start/end, and last present timestamp (v2). V3 appends generation, sample-window command
identity, render deadline, lateness, missed deadlines, render overruns, and
raster-state challenges. Q columns are:

```text
Q,update,phase,parent_node,scope,calls,in_wall,in_cpu,ex_wall,ex_cpu,epoch,node,flags,thread,generation,sample_window
```

Node numbers are local to an update. Parent -1 identifies the root. Identity is
the full scope path; recursion therefore creates distinct nodes. Repeated
siblings on the same path accumulate. Storage bounds are 64 stack entries and
128 nodes. Flags 512/1024 identify overflow/invalid scope reconciliation; such
frames cannot provide coverage. The native harness uses the same stack and Q
serializer as the profiler. Frame flag 4096 marks a changed measurement/mode
boundary. Existing flag 2048 retains its GL state-seeding meaning.

Detail records have 12 payload columns, followed by originating epoch, update,
phase, thread, generation, sample-window identity, and collecting context pointer.
Detail comparisons require consecutive render IDs in the same epoch/thread/window;
epoch/update/thread/generation/window fields are excluded from command payload
equality; the collecting context remains part of the ordered command identity.
V/W are derived uniform
comparison metadata and never enter command-stream equality. E payload begins `epoch,update,phase,event_kind,timestamp,render,present`;
kinds 1/2/3/4 mean update start, render attempt, render execution, and drawable
flush attempt. E records count individual events, including multiple presents
and skipped render attempts; F cost statistics require complete eligible frames.

O payload begins `epoch,thread,phase,timestamp,context` and marks a measured call
outside an owned update. Each producer emits one marker per command generation;
no frame is guessed. Markers use the same mapped half-open event window. Any
in-window marker fails the separate origin-integrity gate for causal acceptance;
residual discovery may retain this partial evidence.

G payload begins:

```text
G,phase,render,gpu_start,gpu_end,context_lifetime,pass,segment_sequence,epoch,update,missing_reason,0,0
```

GPU origin comes from that payload, not the trailing metadata of the update
which happens to collect the delayed result. Pass 0 is outside map rendering;
pass 1 is graphical-map rendering. A context switch closes the active segment
before calling CGL; opening uses the actual current context, including after a
failed switch. Context lifetimes never repeat within the process. A shared bounded registry
retains pending queries when a context migrates between threads; each render
thread has its own cursor. Registry operations are serialized. GPU trailing
origins also retain the original thread/generation/window, even when collected
elsewhere. Unknown origins are not assigned to a guessed frame. Results
retain their originating epoch/update/render. Query slots are free, active, or
pending. Only available pending results are read in their owning current context.
No collection switches contexts. Destruction invalidates remaining segments;
measurement stop collects available results and marks unresolved queries missing.
Null/unsupported contexts, context/query capacity, unexpected ownership,
destruction, unresolved completion, and backwards timestamps have distinct
missing reason codes. Reported intervals are GPU timeline elapsed time. They
are never summed across contexts or interpreted as whole-render busy time.

All C wall timestamps use `CLOCK_UPTIME_RAW`; CPU uses
`CLOCK_THREAD_CPUTIME_ID`. Python event/window timestamps use `monotonic_ns`.
Each enable command brackets the C acknowledgement with controller command-send
and acknowledgement-receive timestamps. The interval `[send-C_ack, receive-C_ack]`
bounds the mapping offset. The phase records its midpoint offset and maximum
half-width as alignment uncertainty; it never assumes two clock APIs are equal.
This is conservative and includes update completion, polling, and scheduling
latency. The mapping is recalibrated per phase. Clock drift during a short phase
is not independently measured and remains a limitation.

A cost frame must have matching epoch/phase, valid accounting, mapped start at
or after window start plus uncertainty, and mapped end at or before window end
minus uncertainty. Scope, temporal, GPU, and live coverage checks use that same
population. Individual rate events use their mapped timestamps in the half-open
controller window. Power analysis uses those same start/end boundaries and the
recorded realtime/monotonic anchor. Boundary diagnostics remain in raw telemetry.
Legacy F/Q/G rows remain viewable, but legacy Q cannot pass v2 coverage.

Tree reconciliation verifies inclusive = exclusive + direct children for CPU
and wall independently. Coverage uses verified semantic exclusive intervals only.
The five broad envelopes remain residual; pacing is separate. UpdateOneFrame and
executed Render each have independent aggregate CPU and wall ≥95% gates with
per-frame ratio distributions. A gate uses totals, never the median of ratios.
Localized non-CPU elapsed time is exclusive wall minus exclusive thread CPU;
it may include waiting and descheduling. A balanced tree alone does not explain
those residuals. GPU completeness is independent of CPU coverage.


The reference tier (mode 7) retains update/render/present timing and timestamped
rate events while bypassing detailed GL counters and GPU queries. Continuous
counters accumulate locally and flush at frame boundaries. Each producer owns
its bounded SPSC queues (128 frames, 32,768 detail records, at most 16 producers);
writer publication uses release/acquire ordering. Capacity failures increment
the retained dropped-record counter and fail integrity. Scope snapshots exclude
the live stack. Shutdown observes the stop flag before taking a fresh queue
snapshot and drains all completed publications. GPU/payload/GL timing runs only
in sampled windows.

D preserves forced rasterizer discard despite engine enable/disable attempts,
counts challenges, and restores each context's last requested state. Context
destruction, missing state, capacity and restoration failures invalidate D.
Clears, copies and readbacks remain outside D's interpretation.

Legacy v1/v2 frames and scope records remain readable. They cannot pass the v3
release evidence gate. A balanced scope tree alone cannot enable architectural
recommendations. Reports show passed/failed/unavailable gate reasons separately.
The reviewed helper permits 1,200 samples; the controller deadline is 1,140
seconds, with pre-launch budget rejection and journaled restoration.
