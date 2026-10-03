# Profiler feedback review and next implementation plan

Date: 2026-10-02. Reviewed implementation: `profiler-overhead-calibration` at
`f2cf2fbed975b1333157bdac8f49b355752985d9`.

This is the implementation plan and current status record. Changes in the shared
worktree implement portions of work packages 1, 2, 3, 4, 5 and 6 below. Those
changes have not been built or exercised after editing. No overhead results,
successful gate or live experiment is claimed. The offline gate and live 3%
reference/counters gate remain in force; residual discovery and causal work remain
blocked until their applicable gates pass.

## Verified starting point

Local `main` and `origin/main` are at `a36f942`; the reviewed branch is one commit
ahead. [Portable CI run 36973634407](https://github.com/HagbardCel/eu4-mac-perf/actions/runs/36973634407)
completed successfully for Python 3.10 and 3.14 on the reviewed commit. Portable
CI does not establish macOS driver performance or installed-game validity.
The C source hash matches the retained offline evidence:
`1ae5be10c48afcd2d6cb4dd7a19154fb04c1d32d6c446d15373216795330780a`.

The [verification record](frame-model-verification.md) and
[raw offline evidence](frame-model-offline-evidence.json) establish substantial
correctness improvements, but every structural recipe still fails acceptance:

| Recipe | Counters wall vs reference | Sampled wall vs reference | Sampled thread CPU increase |
|---|---:|---:|---:|
| mesh | +8.1% / +285.25 µs per frame | +466.6% / +15,118.81 µs per frame | +30.95 µs per frame |
| borders | +8.4% / +78.81 µs per frame | +360.6% / +3,481.01 µs per frame | +20.69 µs per frame |
| text/UI | +10.6% / +42.39 µs per frame | +226.0% / +921.35 µs per frame | +18.28 µs per frame |

These measurements are generated-resource microbenchmarks, not installed-game
overhead estimates. Wall-minus-thread-CPU is evidence of additional waiting or
descheduling; it does not identify driver serialization by itself. Writer work,
contention, scheduling, query operations and changes in command submission remain
possible contributors. The harness uses an accelerated OpenGL 3.2 core context;
its results do not establish identical behavior in the game's context/profile.

## Assessment of the feedback

| Feedback point | Decision and qualification |
|---|---|
| 1. Sample scheduling | Agree; closed for the supported single-phase/epoch/thread, one-render-per-frame population. Preserve the 4 plain / 4 sampled / 4 plain checks and delayed-stream tests. |
| 2. GL shadow and query preparation | Agree; retain the prohibition on measured reconstruction/allocation and explicit incomplete-state evidence. |
| 3. Accounting and writer improvements | Agree; supported by tests and measurements. The current ablations do not establish the dominant remaining cost. |
| 4. Sampled slowdown and new ablations | Agree; highest-value performance work. Driver serialization and metadata cost are hypotheses to test. |
| 5. Offline 3% hierarchy | Agree that reconsideration is warranted. Do not substitute a convenient absolute threshold or change launch eligibility before defining and validating a separate calibration admission policy. |
| 6. Causal versus forensic frames | Agree with the architectural direction. Excluding capture frames alone is insufficient if GPU queues, writer work or power windows remain contaminated. |
| 7. Export presence as reachability | Agree; SDK/export evidence belongs in the candidate universe, not the required reachable-path set. Unknown executable paths must still block completeness. |
| 8. GLEW/function-pointer coverage | Agree; P1 before C. Import/dlsym interposition does not prove a stored function pointer is observed. |
| 9. Alias canonicalization | Agree; replace suffix stripping with explicit verified aliases and preserve exact forwarding targets. |
| 10. Reference fast path | Agree to investigate after the ablations. A TLS flag alone cannot replace coherent refresh for calls outside owned updates. Separate probe binaries are an evidence-dependent option. |
| 11. Residual report coverage | Agree; report pre-C diagnostics and C as not applicable when no C was scheduled. Do not weaken full-experiment recomputation. |
| 12. Correction-plan status | Agree; refresh the current status now, retaining historical evidence and separating planned policy from the active contract. |

Do not add semantic engine hooks, extend Metal translation analysis, or add many
obscure draw wrappers before resolving this work. The branch can be reviewed as
an incremental correctness improvement; it must not be represented as a finished
or accepted profiler. This plan neither merges the branch nor launches the game.

## Worktree implementation status

The existing seven-pair acceptance suite still runs first with its original stage
order. A separate seven-pair A–F matrix is wired after it. Test-build counters
record GPU stamps, availability polls, result reads, detail records and queue
drops; disabled query/record features are rejected if operations appear. No matrix
or overhead result has been collected from this implementation.

The generated draw manifest is now schema 2 with evidence provenance, separate
reachability and explicit aliases. It contains 101 candidate APIs and 43 unresolved
symbol/pointer paths; those paths still block C. Export-only and string-only
candidates no longer block the gate by themselves. No GLEW pointer interception
is claimed. The regenerated observer and ID headers have not been compiled.

Normal causal phases now request no forensic frames or GPU timestamps. P0/P1/P2
calibration is capture-free. The `TAIL` phase runs after the causal and power-mode
comparisons; analysis omits it from causal phase summaries, while retaining its
forensic records and separate intrusion result. `--calibration-only` stops after
calibration and the forensic tail. It retains the existing offline preflight and
cannot proceed when that gate fails. Producer thread IDs and context identities
are cached for production detail records; delayed GPU records carry their source
context.

The residual-discovery report now marks C unavailable when it was not scheduled,
and a causal report with no C evidence cannot claim a pre-C pass. The live sampled
result is informational for forensic suitability; the existing offline 5% gate
remains mandatory. No percentage or absolute admission threshold has changed.
No tests, native build, offline workload, live calibration or game experiment has
been run after these edits. Those checks remain before considering this work
complete.

## Work package 1 — identify sampled costs with independent controls

Priority: P0 diagnostic. Files: `benchmark/eu4_frame_model.c`,
`benchmark/eu4_frame_model.py`, `benchmark/frame_model_workload.py`,
`tests/frame_model_workload_harness.c`, and native/portable profiler tests.

Introduce test-only independent controls for GPU timestamps, detailed record
families, metadata acquisition and sampled per-draw clocks. Keep scopes, counter
accounting, preparation, command recipes, resources and writer policy fixed.
Record the effective feature mask in evidence; reject unknown controls. Diagnostic
controls must not silently become production acceptance modes.

Run the following paired conditions against a common counters baseline:

| Condition | GPU timestamps | Detailed GL records | Metadata |
|---|---|---|---|
| A | off | off | existing baseline |
| B | on | off | existing metadata |
| C | off | on | current per-record acquisition |
| D | off | on | cached thread/context identities |
| E | on | on | current full sampled path |
| F | on | on | cached thread/context identities |

A–E implement the proposed decomposition; F tests whether metadata interacts
with timestamp insertion. B may emit GPU results (`G`), while D/S/U and resource
capture are disabled. F/Q and essential integrity/coverage evidence remain
available in all relevant conditions. Disable query insertion, measured polling
and result collection together in GPU-off conditions; retain identical unmeasured
preparation for the first comparison. Count timestamp insertions, availability
polls, result reads, detail records and queue drops to prove each condition's
effective behavior. Keep per-draw CPU/wall clock sampling constant across the
first matrix, then ablate it separately; otherwise C versus A confounds record
capture with timing-call overhead.

Cache thread identity once per producer lifetime. Cache context at render entry
and update it on successful intercepted context switches, destruction/reuse and
migration. A render can contain multiple contexts: one immutable context value
per render would be incorrect. Preserve invalidation/lifetime stamps and explicit
unknown contexts where an unobserved switching path remains possible. Delayed GPU
results must retain the originating context as well as epoch/thread/render/window;
the currently collecting context is not their provenance.

If detailed capture remains expensive, compare cumulative families: draw identity
only, then state, uniforms, and resources. Also retain the old accounting/writer/
preparation ablations as historical diagnostics. Measure all comparisons in wall
and workload-thread CPU; collect process/writer CPU as separate diagnostics to
avoid assuming background cost is absent. Do not insert synchronization into the
measured workload merely to simplify attribution.

Acceptance:

- Native tests prove zero timestamp/poll/result calls in GPU-off measured frames,
  no disabled detail families, exact draw forwarding and count reconciliation.
- Cached metadata tests cover A→B→A, failed switches, null contexts, thread
  migration, destruction/reuse, unowned calls and delayed results.
- Every condition renders valid output without measured state/query preparation,
  unsupported silent fallback, queue loss or serializer-order violations.
- Preserve seven pairs, four measured frames, alternating order and the three
  unchanged recipe hashes. Retain the original bare/reference/counters/sampled
  suite as a separate comparable result; give the expanded matrix its own order
  metadata. Report paired deltas and uncertainty, including GPU/detail interaction,
  without declaring a cause from a noisy median.

## Work package 2 — qualify executable draw paths, including pointers

Priority: P1 correctness; required before C, independent of package 1.
Files: `benchmark/frame_model_draw_api.py`, `benchmark/frame_model_static.py`,
`benchmark/eu4_engine_inventory.py`, `benchmark/eu4_frame_model.c`, generated
`eu4_draw_observers.h` / `eu4_draw_api_ids.h`,
`analysis/frame-model-draw-api.json`, and `tests/test_frame_model_draw_api.py`.

Replace `static_reference: true` and the single observer label with a versioned
manifest that separates candidate availability, evidence provenance, executable
reachability and coverage of each path. Preserve API IDs where meanings remain
unchanged; version supplementary evidence when its interpretation changes. Old
schema-1 manifests remain readable as historical data but cannot prove the new
complete-path gate.

| Evidence | Reachability treatment | Required interception proof |
|---|---|---|
| Mach-O imported GL entrypoint | reachable | actual interposed imported call |
| Verified executable call or pointer path | reachable | mechanism covering that specific call/pointer path |
| GLEW/function-pointer symbol/reference | unresolved until dataflow is checked; blocks completeness if potentially executable | verified initialization, pointer target and observed invocation |
| Actual dynamic resolution | reachable | recognized resolver returns the correct wrapper; preserve resolution evidence |
| Runtime invocation | reachable | observed forwarded/suppressed call with origin and accounting |
| SDK declaration or library export only | candidate | no required observer solely for availability |
| Embedded string only | candidate; promote when a resolver/dataflow path is verified | no completeness claim from the string alone |
| Unclassified executable indirect/resolver path | unresolved | blocks C until demonstrated covered or proven irrelevant |

Retain symbol kind, relocation/address, caller/site, resolver and pointer-slot
evidence rather than matching `_gl` and `glew` names in concatenated text/JSON.
Inventory resolvers in the executable and relevant loaded dependencies; a
matching string or absent runtime observation cannot prove an indirect path is
unreachable. State the pinned executable/dependency and observation boundary for
every completeness claim.

For each GLEW path, trace initialization and target assignment. Demonstrate that
the resolver returns the wrapper and that stored-pointer calls invoke it, or
implement bounded, ABI-verified pointer-slot interception with restoration and
lifecycle tests. Prefer the existing resolver mechanism when it actually covers
the path. Leave unsupported or unverifiable paths explicitly unresolved. Resolver
coverage must include initialization before measurement and later reinitialization;
an absence of `dlsym` during measured frames is not sufficient.

Replace generic suffix removal with an explicit alias map containing signature
and semantic justification. Preserve API spelling/alias evidence, ABI and exact
forwarding target even where equivalent calls share a canonical identity. Unknown
extensions keep separate identities. An alias must not inherit suppression merely
because its name loses a suffix.

Acceptance:

- Export-only or string-only candidates without observers do not independently
  fail C; reachable or unresolved executable paths without proof still fail.
- Synthetic tests demonstrate imported calls, stored dlsym pointers, GLEW-style
  slots, alternate resolvers, late resolution and unsupported paths. An import
  test cannot substitute for a function-pointer test.
- Known aliases pass signature/forwarding tests; unknown suffixes, mismatched ABIs
  and vendor-only names remain distinct. Generation and verification are deterministic.
- Runtime K/A evidence remains mandatory for every participating frame; unknown
  APIs, count mismatch, unsupported reachable submissions, forwarded C submissions,
  missing positive suppression or stale executable/header hashes block C.
- Do not remove legacy `glBegin`/`glEnd`/`glVertex2f` from the required set: the
  executable imports them. Model immediate-mode/display-list semantics explicitly
  before claiming full suppression; observing entrypoints alone is insufficient.

## Work package 3 — separate causal measurement and forensic capture

Priority: architectural decision, informed by package 1. Files: C probe, Python
controller/analyzer, `benchmark/frame_model_gates.py`, protocol and release-gate tests.

Implement two explicit measurement roles, initially sharing the safe producer,
control, queue and serializer implementation:

```text
causal windows:   scopes + counts + cadence + aligned process/system power
forensic windows: ordered GL identity/state/payloads + optional GPU markers
recovery:         measurement excluded until capture effects have settled
```

Causal mode must not insert GPU timestamps or capture detailed GL payloads.
Forensic mode describes frame structure and observed GPU timeline segments; its
intrusive CPU/wall observations are diagnostic. Structural equality in captured
frames does not establish unchanged behavior in uncaptured frames.

Introduce explicit role/window provenance and eligibility shared by every
consumer: phase summaries, scope coverage/residuals, contrasts, cadence/drift,
recommendations and power alignment. Exclude entire owned updates containing a
forensic render, including nested scopes, from causal timing. Preserve records
and the existing epoch/generation/delayed-serialization rules for diagnostics.
Use a protocol revision or explicit manifest schema/capability version; ambiguous
old logs cannot be retroactively classified as clean causal measurements.

Move the reference → counters → reference calibration into entirely capture-free
phases. The current P1 contains six sampled windows, and process CPU/power and
cadence summaries span the whole phase: the label "counters-only" is not exact.
Run forensic qualification in separate windows/phases. Keep complete local
neighbors and the six four-render sample tests as intrusion diagnostics, or as
the existing ≤5% acceptance gate if sampled frames are still used causally.

Frame exclusion alone does not remove spillover. Define and validate a bounded
recovery policy for pending GPU work/results, writer queues and cadence/CPU
stability before causal measurement resumes. Collection/draining happens outside
causal windows. Power samples overlapping capture or recovery must be excluded;
if collector granularity cannot isolate them, use separate forensic phases/runs.
Missing recovery evidence invalidates adjacent causal windows. Do not introduce
`glFinish` into accepted causal frames to force apparent recovery.

Start with runtime feature separation. If bare/reference or causal overhead still
fails because of inactive interposers, evaluate a minimal causal build and a full
forensic build from shared components. Hash and independently calibrate each;
do not assume cross-build timing and forensic observations describe identical
execution. Such a split is optional, not a prerequisite for the initial diagnosis.

Acceptance: mixed-role synthetic logs prove no forensic frame, enclosing update,
scope or overlapping power interval enters causal metrics. Delayed GPU/writer
records retain original identity; recovery failures block resumption. Separate
live counters calibration still has the hard 3% CPU/cadence gate. No new forensic
acceptance threshold is inferred from the current failed results.

## Work package 4 — define calibration admission without weakening release gates

Priority: policy decision after diagnostic evidence. This package must precede
any change allowing live calibration under failed legacy offline percentages.
Files: controller/preflight, `frame_model_gates.py`, release-gate tests, README,
protocol, correction plan and verification record.

Adopt the proposed tier structure in a versioned policy:

| Tier | Purpose | Proposed condition |
|---|---|---|
| 0 | native/static safety | pinned ABI, forwarding, ownership, queues, state/output/restoration and integrity checks pass |
| 1 | admission to bounded live calibration | offline causal-path sanity, explicit absolute CPU/wall budgets, no corruption/loss or unexplained catastrophic behavior |
| 2 | actual workload causal overhead | capture-free reference/counters/reference passes hard 3% process/frame CPU and cadence checks with adequate controls |
| 3 | capture suitability | forensic intrusion, completeness and recovery assessed separately; causal sampled use retains the 5% requirement |
| 4 | intervention/recommendation eligibility | all relevant causal integrity, semantic coverage, cadence, drift, draw coverage and intervention gates pass |

The feedback does not specify Tier 1 absolute limits or forensic recovery limits.
This review does not invent them. Use package 1 evidence to propose concrete
predeclared limits, measurement duration/uncertainty rules, stop conditions and
maximum run budget in a policy decision record before implementing launch access.
Retain and display the legacy 3%/5% synthetic results as failed historical and
comparative evidence. Changing applicability must never relabel them as passed.
Do not inflate synthetic denominators with sleeps or unrelated engine-like work.

Add a bounded `calibration-only` controller path. If admitted under the new policy,
it must exit after calibration/restoration and cannot reach ANATIVE/A0, C, other
interventions or power-mode experiments. Distinguish calibration admission,
residual-discovery eligibility and full causal acceptance in gate sets and reports.
Keep pinned fixture, exact reviewed power-helper checks, deadline, scene guard
and restoration requirements. Counter-only discovery becomes eligible only after
applicable calibration gates pass; forensic capture requires its own suitability
and recovery evidence.

Acceptance: tests prove each tier cannot authorize a later tier, static-only or
missing evidence never authorizes calibration, failed live 3% gates stop before
discovery, and a diagnostic capture cannot authorize causal recommendations.
Default execution remains blocked until the new policy and admission evidence
are complete. Separating eligibility is a methodological change, not a blanket
relaxation of all overhead limits.

## Work package 5 — improve reference throughput without losing refresh safety

Priority: after packages 1 and 3 identify the remaining causal cost. Files: probe
wrapper/control helpers and `tests/frame_model_control_harness.c`.

Measure reference versus bare independently of counters versus reference; an
interposed reference cannot quantify the whole added overhead by itself. Use
captured frame/producer state for the owned-update pass-through check and avoid
repeated control-refresh work there. For unowned calls, retain the atomic command
sequence check and coherent snapshot so worker threads observe enable, disable
and suppression transitions promptly. Preserve context/resource lifecycle
tracking needed for forensic preparation and D restoration. Resolve targets
outside measured hot paths and retain exact extension ABIs/forwarding.

Acceptance: reference/OFF forwards each call exactly once, worker transition and
shutdown tests still pass, no stale cached enable state leaks measurements or
suppression, and paired bare/reference/counters measurements demonstrate whether
the optimization helps. Do not broaden the interposed API set just to satisfy an
export-only catalog.

## Work package 6 — reports and current documentation

Priority: small independent correctness change. Files: `analyze()` and report
rendering in `benchmark/eu4_frame_model.py`, `frame_model_gates.py`, report tests,
README, protocol and the two current status documents.

For `residual_discovery`, recompute reachable-path/observer diagnostics using the
phases actually executed and `require_c=False`. Report `C_intervention` as
`not applicable — C not scheduled`, retaining any pre-C gaps as future experiment
blockers. Gate applicability must be explicit; do not manufacture a passed C
gate or require absent A1–A5/C populations for discovery. A causal report missing
C remains incomplete. A run that attempted C and failed must not become exempt
by relabelling its report kind; cross-check scheduled/attempted phases.

Keep partial attribution and suppress decisive recommendations for discovery.
For full causal reports, continue recomputing draw coverage from pinned build
and runtime evidence rather than trusting the manifest's claimed pass.

The correction-plan status table is refreshed alongside this plan. It remains
the current implementation-status index; the verification record owns measured
results and history, and this document owns proposed work. Update all policy
references together when packages 3–4 actually change the acceptance contract.

## Delivery order and validation

1. Land documentation review/status refresh; no runtime policy change.
2. Add package 1 controls and tests; retain unchanged production measurements and
   run the expanded decomposition before choosing performance fixes.
3. Implement package 2 reachability/provenance, aliases and pointer-path proofs;
   keep unresolved paths blocking C. Package 6 report applicability can land
   independently with regression tests.
4. Implement the supported metadata/query/record optimizations and package 3
   causal/forensic separation. Implement package 5 if reference overhead warrants it.
5. Record and implement package 4's concrete tier policy only with defined limits
   and recovery criteria; a plan alone cannot unlock calibration.
6. Rerun the original seven-pair offline suite and the diagnostic matrix on the
   final source/builds. Preserve prior JSON evidence under immutable dated/hash
   names before regenerating the current record. Retain recipes, raw trials,
   source/library/header hashes, environment/context details and uncertainty.
7. When admission allows it, run bounded live calibration, then eligible residual
   discovery. Full causal interventions require their own later acceptance.

Run `python3 -m unittest discover -s tests`,
`python3 benchmark/eu4_engine_inventory.py verify`,
`python3 benchmark/eu4_frame_model.py preflight --static-only`, and
`git diff --check` for implementation changes. Run display-dependent preflight
and the expanded workload diagnostics on macOS with an accelerated context;
portable CI does not substitute for them. Repeat tests when affected changes or
failures justify it. Preserve failures as failures in the verification record.

Completion of this iteration means the intrusive mechanisms are measured,
draw completeness is justified by executable paths, causal data cannot contain
forensic contamination, and admission/report gates have explicit applicability.
It does not require semantic hook expansion, a Metal go/no-go decision, or a
successful long experiment before those downstream tasks become justified.
