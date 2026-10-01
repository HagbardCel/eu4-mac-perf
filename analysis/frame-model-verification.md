# Corrective profiler verification — 2026-10-01

The corrective implementation includes the shared production scope stack and
serializer, path identities and validity checks, semantic coverage and envelope
residuals, v2 commands/epochs/telemetry, acknowledged windows with clock uncertainty,
matched control cadence, context/pass GPU query state management, the discovery
schedule, and the feasibility-seed label. No semantic hooks were added speculatively.

Verification completed:

- `python3 -m unittest discover -s tests`: 72 tests passed, including native
  producer compilation, injected-clock accounting, busy/sleep smoke, controller
  acknowledgement and detail-epoch tests, aggregate coverage, timestamped event
  rates, boundary filtering, legacy decoding, and discovery report generation.
- `python3 benchmark/eu4_frame_model.py preflight --static-only`: x86-64 build
  passed; detour, render-gate, exact ARB-handle forwarding, production scope,
  serializer/analyzer integration, and GPU state-machine harnesses ran under
  Rosetta and passed. Static preflight is not an overhead acceptance.
- `python3 benchmark/eu4_engine_inventory.py verify`: seven pinned engine
  entry points and generated backend inventory verified.
- `git diff --check`: passed.

The shared GPU harness exercises A→B→A in one render, failed switches, pass
splits, null and unsupported contexts, active versus pending queries, delayed
availability, wrong-owner polling, unexpected ownership, destruction/reuse,
query exhaustion, context capacity, and uncollected results. Mock callbacks
assert every stamp/result operation uses the owning context and every result
read was preceded by availability. This is state-machine evidence, not proof
of a driver’s timer-query support in EU IV.

The sandbox cannot expose an accelerated CGL pixel format. An authorized
unsandboxed OpenGL preflight reached the real GL workload. It initially found
recursive CGL forwarding through `RTLD_NEXT`; the macOS crash report showed
repeated `tracked_set_context` frames. Forwarding now uses direct framework
imports from the interposer image. Repeating the unsandboxed preflight completed
the bare and wrapped GL runs without that recursion crash.

**The counters-only overhead gate failed:** wall overhead **+632.1%**, thread CPU
**+626.1%** on the current synthetic GL workload, versus the retained 3% limit.
These are observed synthetic perturbations, not estimates of EU IV overhead.
No live counters or sampled-tracing perturbation gate has passed. Do not adjust
the thresholds or infer that real-game overhead is acceptable from static tests.
The instrumentation/workload must be investigated before another live attempt.

`run --residual-discovery` was attempted. Sandboxed preflight could not verify
the display. Authorized unsandboxed preflight verified the display but rejected
the installed powermetrics helper: it still has the old 420-sample limit, while
the exact reviewed source uses 720 samples. Installed SHA-256:
`8baf0d0a6b7ebc6bd2d835cef04c0d5512391b2b6f126f5623431e3e6e4dcc01`;
reviewed source SHA-256:
`e93423069f7c79debb81d858b4709c0d889401a5c073ce4cf72a8611af258733`.
No privileged helper or sudoers files were changed. Exact helper checks remain.

**Pilot status: blocked before game launch.** There is no captured ANATIVE/A0
residual population, power result, or evidence-driven hook expansion to report.
No EU IV launch, fixture mutation, or power-mode transition occurred in the
failed pilot attempt. Metal go/no-go remains undetermined. After overhead is
resolved and the reviewed helper is refreshed, repeat the discovery pilot;
then add 3–8 verified semantic hooks per iteration based on its residual table,
with separate commits and repeated ABI/overhead checks. The long causal run
additionally requires the independent UpdateOneFrame and executed Render CPU
and wall coverage gates.
