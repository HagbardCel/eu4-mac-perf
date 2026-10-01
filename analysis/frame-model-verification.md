# Profiler verification — 2026-10-01

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
intervals are in [the offline evidence](frame-model-offline-evidence.json).
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
