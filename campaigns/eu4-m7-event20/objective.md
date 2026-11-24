# M7 authentic exact-prefix campaign

Objective: establish exact original-EU4-OpenGL versus Metal framebuffer equality
for every event from event 0 through the campaign's configured target of one
authenticated, sealed captured frame.

The configured target comes from the campaign's acceptance command
(`--target-end N`); this objective is shared by every successive prefix
milestone.  The independent checker owns the verdict.

## Starting evidence for the event-3 milestone

Review `progress.md` and `analysis/m7/reports/event3-prefix-diagnosis.md` in the
retained candidate first, especially the precise-math and depth experiments
and the superseded interpretations in §25. Check renderer-source identity
before reusing a receipt or deciding another validation is justified. The
trusted candidate comparison first diverged at event 1. The separate merged
PR #293 smoke replay in the infrastructure checkout
(`.local/m7/pr293-metal-ch0ohkqj/metal/`) is standalone diagnostic evidence
for the merged-main renderer. Keep its measurements tied to that renderer.

Use `eu4_case_inspect` on the `tex:433` transition from the event-0 clear to
the event-1 draw. Compare original observations and Metal evidence for the
attachment format, state, channel precision, resource lineage and readback
semantics. Use original-binary research when it can test a specific prediction.
Treat dither, multisampling, UV interpolation and channel-local differences as
hypotheses or observations until evidence discriminates their roles. State a
falsifiable prediction before new GPU work and preserve negative results.
After a supported correction and focused tests, use the trusted checker and
continue through events 2 and 3 as the earlier prefix verifies. When an
essential original-side observation is missing, identify it precisely for the
operator and specify what a separately authorized capture would discriminate.

## Governance

There is no replay, correction, validation or goal-round quota. Live EU4
launches are controlled separately: this campaign is `autonomous_reconstruction`
by default, and a capture needs a `controlled_capture` campaign plus an
operator-issued one-use launch authorization. An operator-configured execution
deadline bounds unattended cost; it is not a per-experiment budget.

Before a meaningful GPU experiment, state the competing causal explanations, the
specific change or observation that distinguishes them, the predicted outcome
under each, and the cheapest available way to obtain that observation.  Prefer a
narrow experiment that separates one mechanism over one that changes several
renderer behaviors at once.  Reuse a completed deterministic experiment when its
relevant inputs and execution conditions are unchanged, and use existing
renderer-source/build identities and execution receipts to decide whether the
validated renderer materially changed.  The candidate digest covers every
modified permitted file, including progress notes and analysis reports, so a
different candidate digest does not by itself mean that renderer behavior
changed.  Do not invoke `eu4_validate_candidate` merely to refresh a receipt at
the end of a goal round, after documentation changes, or after a byte-identical
rollback whose effective shader and build inputs are unchanged; reuse the
previous diagnostic result in those cases.  A retained renderer correction or
an exact-prefix success claim still requires the appropriate trusted
verification, and a direct diagnostic acceptance call must use the campaign's
configured `--target-end`.  A previous validation never establishes equivalence
for new renderer code or altered reference inputs.

After an experiment, classify the conclusion as **supported** (evidence favors
the explanation), **falsified** (the tested intervention or prediction was
contradicted) or **unresolved** (the experiment did not discriminate). A
negative perturbation excludes the tested intervention or predicted response.
Fewer differing pixels show sensitivity to the change. Estimating the fraction
causally explained requires attribution of the original discrepancy. Gradient
correlation alone cannot identify hardware interpolation as the cause.
Statistical significance requires a justified null model that accounts for
spatial dependence and nonuniform errors. In A/B comparisons, inspect the
differing coordinates, channels, signed errors and overlap of both outcomes.

The operator deadline is authoritative only through
`python3 -m automation.campaign_guard status --state $EU4_CAMPAIGN_STATE`.
Never report a deadline stop unless that status shows no remaining time or a
campaign action is refused with `kind=deadline`; an unsupported correction path
or a missing observation is reported as its actual cause.

Maintain one compact current investigation record in `progress.md` or an
allowed `analysis/m7/` location. Record the first divergent event and target,
relevant evidence and provenance, tested hypothesis and prediction, observed
result, conclusion (supported, falsified or unresolved), and the next
discriminating observation or genuine blocker. Preserve negative results and
link supporting artifacts. Revisit a rejected experiment only when new evidence
materially changes the hypothesis. Never invent work to fill a round counter.

## Evidence convention

The **authoritative reference** is the framebuffer observations sealed from
original EU4 running its original OpenGL renderer; it is the only oracle.  The
**candidate** is the independently executed Metal renderer under correction.  A
reconstructed OpenGL implementation is an **optional diagnostic control**: it
may help explain a difference but neither establishes nor replaces the
reference.

Use the trusted reference root and sealed frame recorded by the selected
campaign sidecar.  The checker verifies the seal, the event stream, the
observation file, the shader receipt binding, the published checkpoint request
and every applicable colour channel of every observation.  The channel scope
comes from the sealed attachment identity; a candidate cannot narrow it.  Do not
copy, relabel or combine observations from another seal.  Do not edit the
checker, `automation/gfx_prefix_check.py`,
`automation/gfx_independent_replay.py`, `automation/gfx_observation.py` or
`automation/m6_frame.py`.

At campaign start, inspect previous evidence relevant to the current first
divergence.  Use a reconstructed OpenGL control only when it can discriminate a
specific hypothesis.  An unavailable or unsupported reconstructed OpenGL
control is not, by itself, a reason to declare `requires_additional_evidence`
when relevant authentic observations and candidate-side tests remain available;
record it and continue with the available authentic evidence.

## Sequence

1. Review the prior evidence and follow the first divergence with the cheapest
   supported operations.
   - For an established divergence and a supported frame or case, call
     `eu4_case_inspect` with the event before writing a parser. Read its
     original caller, program, target, producer relationships and Metal evidence.
   - Look up relevant original functions with `eu4_re_query`, then use
     `eu4_research` when instructions or decompilation can distinguish the
     hypotheses. Skip corpus calls for questions the executable cannot answer.
   - Read the exact captured GLSL, state or source that can resolve the current
     question. Label claims **Known original behavior**, **Known Metal
     behavior** or **Unresolved difference** and cite the evidence for each.
   - Use native `eu4_replay diagnose` when Metal artifacts already exist or
     `independent` when a justified GPU experiment is needed. Check the outcome
     fields before using a comparison as evidence.
   - Use shell or a custom script for an unsupported operation or a native
     failure. Record the exact failure reason and avoid repeated retries.
     In a fallback, check the exit status and parse the JSON envelope before
     interpreting any comparison. Pixel files alone cannot validate evidence.

   Choose calls that answer the question. `eu4_diff_run`, `eu4_capture` and
   Ghidra are unnecessary for a supported offline comparison. This campaign
   never launches EU4 or Ghidra.
2. Run the synthetic drawable-format observer and focused offline tests only
   when the current task and existing verification evidence require them. Do not
   launch EU4 if a required observer is incomplete or a relevant prefix test
   fails; a full preliminary battery is not required at every goal round.
   During this autonomous reconstruction campaign, the campaign-specific
   verification policy takes precedence over generic full-suite instructions
   inherited from the pinned candidate worktree. Temporary experiments, exact
   rollbacks and individual goal rounds do not trigger the full offline gate.
   Retained corrections require focused verification and the trusted independent
   checker. Candidate-safe focused tests run entirely inside this worktree.
   Tests that need additional Git worktrees, shared Git metadata writes or other
   unavailable operations belong in operator-side verification: report the
   inability to execute them in-session as an environment limitation, not a
   renderer failure, and do not rerun a known-failing test merely because it
   cannot create `.git/worktrees` entries.
3. Live capture is a separate, operator-controlled preparation stage: an
   `autonomous_reconstruction` campaign must not launch EU4, and a
   `controlled_capture` campaign may launch only with an operator-issued
   one-use authorization. If the existing evidence is insufficient, stop with
   `requires_additional_evidence` and name the exact capture request (caller
   set, prefix scope, required observations) for the operator; request a new
   capture only when it can provide a necessary observation. In a gap report,
   distinguish observations already in the authenticated frame, available
   operator-controlled observations still to be made, and observations current
   tooling cannot obtain. The [UV2 probe](../../docs/harness.md#event-1-uv2-diagnostic-probe)
   already targets event 1, program 310, framebuffer 6 and texture 433.
   Its [comparison](../../automation/gfx_uv2_probe.py) is conditional and
   `fragment_attribution_verified=false` leaves the original fragment-level
   cause unresolved. Only the operator may authorize and run that capture.
4. Run the independent checker after a substantive renderer correction and
   before claiming the requested exact-prefix milestone; reuse the current
   checker cache and execution facilities. Record the earliest verified
   divergence and preserve the sealed frame, Metal outputs, request, receipt,
   logs and the portable replay case before diagnosing.
5. Diagnose with the cheapest discriminating control that can distinguish the
   remaining mechanisms. State the question, the relevant evidence, the gap and
   the expected information before a GPU replay. Change only the smallest
   renderer component supported by that evidence, run focused tests, replay the
   same seal, and submit the proposed correction to `correction_accept`.
6. Repair the earliest independently verified divergence and continue to the
   next one while meaningful, justified investigation remains possible. Do not
   stop merely because an earlier correction exposed another divergence.
7. If the evidence cannot support a causal correction, stop with
   `requires_additional_evidence` and name the exact missing observation. Do not
   demand exhaustive investigation of every conceivable hypothesis; the test is
   whether the next necessary discriminating experiment is available.
8. Before declaring a candidate-scope limitation, either cite causal evidence
   linking the discrepancy to the excluded component or identify the next
   discriminating experiment and why it is unavailable. Correct a defect only
   in the component that owns it.

## Stop conditions

Stop on any of: verified success for the configured target scope; a genuine
evidence gap that cannot currently be observed; an unavailable or unsafe
environment; an explicit operator cancellation; or the operator's execution
deadline as reported by `campaign_guard`. A failed exact-prefix comparison
requires investigation, not campaign termination, and a first divergence does
not end the campaign after a correction exposes the next one; a repeated failed
hypothesis is a reason to reassess rather than a quota stop. Do not merge, push
to `main`, modify historical seals, or relax exactness.
