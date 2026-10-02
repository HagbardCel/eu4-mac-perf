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

Control flags use bit 1 for intervention mode, bit 2 for measurement, and bit 4
for a forensic phase. E forensic timing records are limited to armed sample
renders. Other phases use the F record's update/render/present timestamps for
rate calculations; an incomplete E stream never overrides those frame counts.

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

## Deterministic sampled calibration and supplementary coverage

The immediate post-start provenance command always carries `detail_frames=0`
and preserves the enable epoch. N requested arm times are `start+i*duration/(N+1)`
for i=1…N. Forensic `TAIL` capture requires six four-render groups, after causal
and power-mode phases. The controller incrementally reads complete telemetry lines,
uses the same eligibility filter, waits for the preceding plain renders, then
waits for the complete sampled group and its trailing plain renders before the
next arm. Requested times, command generations, observed preceding identities,
actual sampled identities and both neighbor groups are retained in phase evidence.
Duration and unattended deadlines are unchanged. A missing group, overwritten or
late command, incomplete neighbor group, mixed phase/epoch/thread population,
nonconsecutive render IDs or multiple executed renders per participating frame
makes the forensic capture unavailable. The capture has role `forensic`; its
frames and enclosing scopes are excluded from causal phase summaries. The live
capture-intrusion result is recorded separately and does not qualify the
counters-only reference calibration. Stage 2 splits offline admission:

- **`offline_causal_admission`** — bare/reference/counters seven-pair gates on training recipes plus hash-frozen **held-out** recipe; Tier-1 policy `tier1_causal_rel3pct_abs50us_v1` (relative 3% + absolute µs/frame cap). **Blocks** calibration and live causal work when failed.
- **`offline_forensic_suitability`** — sampled/ablation/A–F evidence; reported independently and **does not** block `calibration-only` when causal admission passes.

Legacy combined `representative_workloads.status` remains in archives for replay only.

GL shadow preparation runs with measurement disabled on the owning current
context. Bounded thread-owned caches hold 16 contexts and 64 VAOs; a serialized
64-entry lifetime/ownership registry publishes atomic invalidation stamps.
Prepared A→B→A switches restore their cached bindings. Shared object deletion,
context destruction/reuse, migration and unsupported mutations invalidate
identities. A measured cache miss never triggers reconstruction queries. Flag
2048 and `L,update,render,phase,epoch,thread,generation,reason` identify incomplete
structural evidence: reasons 1 missing preparation, 2 capacity, 3 invalidation,
4 unsupported mutation/forwarded submission. Detailed records from such frames
are excluded from structural equality claims; valid CPU scope accounting remains
available. Context/query capability checks and query pool allocation also run
unmeasured. GPU missing reason 9 means the measured context was not prepared.
Core contexts enumerate extensions with `glGetStringi`; timestamp support is not
inferred from the duration-only `EXT_timer_query` extension.

F/Q wire layouts remain unchanged. Supplementary records use the existing
12-payload-column detail layout and its epoch/update/phase/thread/generation/
sample-window/context tail:

- `K`: payload begins `1,0,0,0`; revision 1 declares per-frame draw API observers.
- `A`: payload begins `canonical_api_id,observed,suppressed,forwarded`; one record
  per nonzero API count in an owned frame, including unsampled frames.
- `Y`: payload begins `canonical_api_id,1`; a supported dlsym submission pointer
  was replaced with its observer/suppression wrapper.
- `R`: payload begins `name_hash,reason`; 1 unsupported resolved submission path,
  2 unresolved submission pointer. These records persist even during settling.

The canonical [draw manifest](frame-model-draw-api.json) combines pinned executable
imports, full symbol/string references, supported SDK export stubs/declarations,
aliases and historical observations. Historical six-API traces alone never
establish completeness. Candidate range, multi-draw, indirect, legacy immediate,
display-list, evaluator, bitmap and pixel paths have forwarding observers where
an exported ABI is known. Unexported/static candidates and exports without a
public ABI remain explicit coverage gaps, including unsupported resolver paths.
Forwarded candidates lack complete detailed D identities and invalidate structural
claims. The six existing draw wrappers remain the suppression set.

`draw_api_coverage` is checked before C and recomputed from manifest plus runtime
evidence in reports. Complete C requires every candidate observer path, no resolver
gaps, K evidence for every eligible participating frame, reconciled A/F counts,
no baseline/phase submissions outside the suppression set, positive suppressed
C counts and zero forwarded covered C draws. Missing legacy/new evidence cannot
pass. A failed gate labels C a partial draw-suppression intervention and blocks
strong A−C recommendations.

Owned GL count-only totals are aggregated from frame counters at publication.
Raw sampled draw CPU/wall sums remain distinct from the ×256 frame estimates;
semantic scope timing remains unchanged. Unowned-origin detection is independent.
Global C totals are **partial supporting telemetry**: producer flush completeness
is not assumed and the consumer never reads another producer's mutable counters.

The writer uses a bounded 128 KiB buffer, flushes on capacity or a 10 ms deadline,
and drains/flushes at shutdown. Internal publication sequences merge each
producer's frame and detail queues in publication order; F and its associated
Q/K/A/L records remain a publication bundle. Producers remain independent.
EINTR and short writes are retried, permanent failures are visible through shared
hook-failure counters, and parsing retains incomplete lines until serialization
finishes. Absolute CPU/wall overhead and confidence intervals in microseconds per
frame are diagnostics and cannot override a failed percentage gate. Harness-only
ablations restore repeated accounting, unbatched writing or measured preparation;
they are absent from the production library and excluded from acceptance.
