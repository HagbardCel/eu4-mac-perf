# Profiler verification — 2026-10-02

**Offline acceptance still fails; residual discovery remains blocked before launch.**
The scheduling, shadow preparation, accounting, writer and draw-coverage changes
are implemented. The 3% reference/counters and 5% sampled gates are unchanged.
No engine hooks were added. Metal remains a feasibility seed with go/no-go
undetermined. No live calibration, ANATIVE/A0 pilot or long causal result is claimed.

## Implemented and verified

- `python3 -m unittest discover -s tests`: **92 tests passed**, including an
  integrated controller/streamed-producer test with injected clocks, delayed
  acknowledgement/command pickup, delayed serialization, six complete four-render
  windows, boundary exclusion, full preceding/trailing neighbors, missing samples,
  late commands, unchanged epochs and unsupported populations.
- Portable native shadow tests cover unmeasured preparation, measured query
  prohibition, A→B→A selection, invalidation, migration/reuse and bounded capacity.
  GPU tests verify explicit unprepared measured segments without initialization.
- Portable writer tests cover the 128 KiB boundary, 10 ms flushing, EINTR, short
  writes, permanent failures and immediate shutdown. macOS/x86-64/Rosetta tests
  additionally exercise the production serializer with four concurrent producers,
  128 frames, independent unknown-origin markers, merged frame/detail publication
  order, draw resolver aliases and complete shutdown draining.
- `python3 benchmark/eu4_frame_model.py preflight --static-only`: production/test
  libraries and detour, forwarding, scope, GPU, queue, shadow, writer and worker
  control harnesses **passed**. The display-dependent run also passed native checks.
- `python3 benchmark/eu4_engine_inventory.py verify`: **passed**, including
  deterministic draw observer/manifest generation and pinned engine prologues.
- The display-dependent harness verifies GL resources/output, original recipe draw
  counts, frame/scope serialization, semantic timings, count aggregation and raw
  sampled timing totals. Normal test runs assert zero measured state reconstruction
  and GPU capability/pool initialization. Only the test library permits the explicit
  measured-preparation ablation.
- `git diff --check`: **passed**. Portable CI is run on the publication branch;
  its final result is reported separately from local and display-dependent evidence.

The sandbox cannot create an accelerated CGL pixel format. The authorized
unsandboxed GL run completed on the final source and retained its source/library/
header/harness hashes in [the offline evidence](frame-model-offline-evidence.json).
The production C source SHA-256 is `1ae5be10c48afcd2d6cb4dd7a19154fb04c1d32d6c446d15373216795330780a`.
Unavailable live checks are recorded as blocked, not successful or unmeasured
passes. The root helper was not refreshed because offline acceptance failed.

## Final structural measurements

Each unchanged structural recipe has seven paired trials with alternating stage
order. Draw counts and payload recipes remain pinned; no sleeps or busywork were
added. CPU and wall are separate. Each cell shows median percentage perturbation
and absolute diagnostic microseconds per frame against reference. Acceptance also
requires the paired bootstrap 95% interval to fit the unchanged limits.

| Recipe | Draws/frame | Counters wall | Counters CPU | Sampled wall | Sampled CPU |
|---|---:|---:|---:|---:|---:|
| mesh | 2774 | +8.1% / +285.25 µs | +7.9% / +6.64 µs | +466.6% / +15118.81 µs | +39.4% / +30.95 µs |
| borders | 2285 | +8.4% / +78.81 µs | +8.2% / +1.85 µs | +360.6% / +3481.01 µs | +91.7% / +20.69 µs |
| text_ui | 632 | +10.6% / +42.39 µs | +10.6% / +1.02 µs | +226.0% / +921.35 µs | +177.4% / +18.28 µs |

**Every recipe fails acceptance.** Reference-versus-bare gates also fail, including
borders where near-zero medians conceal intervals outside ±3%. Absolute values
cannot override failed gates. These are generated-resource structural surrogates,
not estimates of installed-game overhead. The prior failed source/measurements are
preserved in [the pre-reduction evidence](frame-model-offline-evidence-before-overhead-reduction.json)
and the historical verification below.

Harness-only ablations restore per-call accounting, unbatched writes, or measured
state/query preparation. Raw results and CPU/wall intervals are retained alongside
preparation timing. Their noisy/occasionally negative paired deltas do not establish
a dominant component or prove that state reconstruction explains the remaining
sampled overhead. Thread CPU measures the workload thread; background writer cost
can affect wall time through contention and is not included in that CPU axis.
Ablations are diagnostic and excluded from release acceptance.

## Coverage and next live action

The [canonical draw manifest](frame-model-draw-api.json) includes executable imports,
full symbol/string references, supported SDK exports/declarations, aliases and
historical observations. It currently contains 101 candidate entrypoints and
76 supported forwarding/suppression observers. Range, multi-draw, indirect,
immediate-mode, display-list, evaluator, rectangle, bitmap and pixel paths are
represented. Several static/unexported vendor/extension candidates and exports
without a public ABI remain explicit gaps. The game imports legacy `glBegin`,
`glEnd` and `glVertex2f`; historical six-API observations cannot rule out their use.
No complete C coverage is claimed.

The new `draw_api_coverage` gate is checked before C and recomputed for reports
from the compiled manifest and per-frame K/A evidence. Missing observers/resolver
paths, submissions outside the suppression set, absent markers, inconsistent
counts, missing positive suppression or forwarded covered C draws block complete
acceptance and strong A−C recommendations. Partial C reports are labelled explicitly.
F/Q layouts remain compatible; older records remain readable but cannot pass the
new gate. Global hook totals are labelled partial supporting telemetry.

Only after offline acceptance may the exact reviewed root helper be refreshed
through its installer and noninteractive authorization checks, then
`run --residual-discovery` launched. Mandatory live reference/counters/reference
and six-window sampled calibration remain prerequisites. A successful pilot would
capture unrestricted ANATIVE and paced A0, rank residuals, and support a separately
reviewed addition of 3–8 semantic hooks. The long causal experiment remains behind
all release gates. Threshold revisions or a forensic/causal split require a
separate plan.

---

# Historical profiler verification — 2026-10-01

The release-gate follow-up builds on local corrective commit `96a9d2d`.
The corrective code fixes scope parent production, semantic coverage, acknowledged
epochs/windows, matched cadence, and context/pass GPU segmentation. The follow-up
adds v3 origins, per-thread bounded queues/local counters, a shared GPU lifetime
registry, D state enforcement/restoration, render deadline metrics and rate
acceptance, reference calibration, fail-closed evidence, partial reports, bounded
power collection, and portable CI. Static backend evidence remains a feasibility
seed. Seven engine hooks are installed; additional semantic hooks remain a
separately reviewed residual-driven step.

## Local verification

- `python3 -m unittest discover -s tests`: **79 tests passed**. Native shared
  scope/GPU/queue/render-gate harnesses compile and run, with Python serializer
  analysis, four concurrent producers, capacity/reuse, migration/stale cursors,
  acknowledged windows, epoch preservation, V/W exclusion, sample-window pairing,
  local-neighbor perturbation, cadence, missing gates, budget rejection and
  legacy decoding and unassociated event-window qualification.
- `python3 benchmark/eu4_frame_model.py preflight --static-only`: x86-64 build and
  Rosetta detour, partial-install rollback, render gate, ARB forwarding, shared
  producer and serializer/analyzer harnesses **passed**. The production test
  library also verifies worker control refresh across profile/draw-suppression/OFF
  transitions, explicit unknown origins, and final queued records at shutdown.
- `python3 benchmark/eu4_engine_inventory.py verify`: **passed**, including pinned
  prologue lengths and deterministic generated inventory. Bounded recursive
  static traversal and local shader hashes/includes/features are recorded in
  [the static evidence](frame-model-static.json). Enabled Proper 2K UI has no
  `gfx/FX` shader override directory. Indirect dataflow and runtime frequencies
  remain unresolved.
- `git diff --check`: **passed**.

## Display-dependent overhead

The sandbox cannot create an accelerated CGL pixel format. Authorized
unsandboxed preflight ran valid core-profile GL workloads. The test-only library
exercises the seven actual production wrappers with pointer, integer, bool and
float sentinels, plus scope production, queues, serialization and the writer.
The GL harness checks linked shaders, VBO/IBO bounds, FBO completeness, error-free
draws and nonzero output. Recipes retain observed counts, topology/API categories
and adjacent tracked state-change frequencies from one complete passive frame.
They use generated payloads/shaders; they do not reproduce the original assets,
all GL calls between draws, or engine computation. No sleeps or extra busywork
inflate the baseline. Thread CPU and wall timing remain distinct.

Each recipe has seven paired trials with alternating stage order. Raw timings,
recipe/source/library hashes, median fractions and paired bootstrap 95%
intervals are in [the offline evidence](frame-model-offline-evidence-before-overhead-reduction.json).
Acceptance requires the median and interval to remain inside the unchanged
3% reference/counters and 5% sampled limits for every recipe and axis. The
old tiny-call workload remains a diagnostic, with its raw trials retained.

**The overhead gates failed.** Median perturbations on the final source:

| Recipe | Draws/frame | Counters wall | Counters CPU | Sampled wall | Sampled CPU |
|---|---:|---:|---:|---:|---:|
| mesh | 2774 | +15.4% | +15.4% | +513.8% | +45.9% |
| borders | 2285 | +13.8% | +13.8% | +378.2% | +94.8% |
| text_ui | 632 | +19.5% | +19.5% | +204.7% | +189.7% |

Reference overhead also fails the aggregate 3% acceptance gate. These are structural-surrogate
perturbations, not estimates of installed-game overhead. Native correctness and
successful valid GL rendering do not override the measurement acceptance gates.
No live calibration, ANATIVE/A0 residual population, intervention, power result,
or Metal go/no-go is claimed.

## Live status and publication

**Residual-discovery pilot: blocked before launch.** The retained overhead gates
reject this source. The installed powermetrics helper also differs from the
reviewed 1,200-sample source; it still has the older 420-sample limit. No privileged
helper/sudoers changes were made. Source refresh remains an exact checked installer
step after overhead acceptance. No EU IV launch, fixture mutation or power-mode
transition occurred in this follow-up. The controller does not rely on `sudo -v`.

The portable CI workflow runs Python 3.10/3.14 and native producer tests on
Ubuntu. It does not claim Rosetta, driver overhead, or installed-game validation.
Remote CI is checked after publication and reported separately from these local
results. Its current status is available on the [portable workflow page](https://github.com/HagbardCel/eu4-mac-perf/actions/workflows/profiler.yml);
the evidence above is local and source-hashed. Final code commit `373f3ff` passed
both Python 3.10 and 3.14 jobs in
[CI run 36923891574](https://github.com/HagbardCel/eu4-mac-perf/actions/runs/36923891574),
including the worker-control and shutdown follow-up. Commit `22539e3` had also
passed the preceding portable run. Display-dependent GL overhead remains local
evidence and failed acceptance independently of CI.

The next live action remains the short `run --residual-discovery` pilot after
offline overhead and exact helper checks pass. Its two A0 windows contain two
consecutive renders each, following unrestricted ANATIVE. Calibration separately
requires six windows of four consecutive renders and local unsampled neighbors.
Rank residual CPU/wall envelopes, then add 3–8 ABI-verified semantic hooks per
iteration. The long experiment additionally requires every baseline's independent
≥95% Update/Render CPU and wall coverage, integrity, cadence, intervention and
control-drift evidence. Missing evidence blocks architectural recommendations.
