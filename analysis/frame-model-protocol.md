# Frame profiler protocol and accounting v2

The v2 command is little-endian `<6I4Q>`: version, publication sequence, mode,
phase, flags, detail frame budget, update period, render period, command
generation, measurement epoch. Controller publication uses an odd sequence while
writing and an even sequence when complete. The probe snapshots at update
boundaries. A detail command preserves the epoch. Disabling measurement sets
command epoch zero; the next enable assigns a new nonzero epoch.

Probe-owned fields follow the command: acknowledged generation (offset 56),
hook failures (64), dropped records (72), and acknowledgement timestamp (80),
all uint64. Commands never overwrite them. Acknowledgement time is written
before publishing its generation, after completion of the update using that
snapshot. The controller waits for enable acknowledgement before `phase_start`,
and for disable acknowledgement before the next transition. Enable generation
and epoch are recorded separately in the phase manifest.

Telemetry begins `H,2`. F retains all v1 columns, followed by epoch, update
start/end, render start/end, and last present timestamp. Q columns are:

```text
Q,update,phase,parent_node,scope,calls,in_wall,in_cpu,ex_wall,ex_cpu,epoch,node,flags
```

Node numbers are local to an update. Parent -1 identifies the root. Identity is
the full scope path; recursion therefore creates distinct nodes. Repeated
siblings on the same path accumulate. Storage bounds are 64 stack entries and
128 nodes. Flags 512/1024 identify overflow/invalid scope reconciliation; such
frames cannot provide coverage. The native harness uses the same stack and Q
serializer as the profiler. Frame flag 4096 marks a changed measurement/mode
boundary. Existing flag 2048 retains its GL state-seeding meaning.

Detail records have 12 payload columns, followed by originating epoch, update,
and phase. E payload begins `epoch,update,phase,event_kind,timestamp,render,present`;
kinds 1/2/3/4 mean update start, render attempt, render execution, and drawable
flush attempt. E records count individual events, including multiple presents
and skipped render attempts; F cost statistics require complete eligible frames.

G payload begins:

```text
G,phase,render,gpu_start,gpu_end,context_lifetime,pass,segment_sequence,epoch,update,missing_reason,0,0
```

GPU origin comes from that payload, not the trailing metadata of the update
which happens to collect the delayed result. Pass 0 is outside map rendering;
pass 1 is graphical-map rendering. A context switch closes the active segment
before calling CGL; opening uses the actual current context, including after a
failed switch. Context lifetimes never repeat within a producer thread. Results
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
