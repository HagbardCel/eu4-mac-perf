# M7 authentic exact-prefix campaign

Objective: establish exact original-EU4-OpenGL versus Metal framebuffer equality
for every event from event 0 through the campaign's configured target of one
authenticated, sealed captured frame.

The configured target comes from the campaign's acceptance command
(`--target-end N`); this objective is shared by every successive prefix
milestone.  The independent checker owns the verdict.

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
validated renderer materially changed.  Do not rerun the independent checker
because a diagnosis report, progress note or unrelated file changed; run it
after a substantive renderer correction and before claiming an exact-prefix
milestone.  A direct diagnostic acceptance call must use the campaign's
configured `--target-end`.  A previous validation never establishes equivalence
for new renderer code or altered reference inputs.

After an experiment, classify the conclusion as **supported** (evidence favors
the explanation), **falsified** (the tested intervention or prediction was
contradicted) or **unresolved** (the experiment did not discriminate); these are
reasoning conventions, not machine-readable fields.
An unsuccessful texture-decoding substitution does not disprove every
texture-precision mechanism, and an LOD clamp that leaves one output unchanged
is not proof that every sample used LOD zero.  Numerical magnitudes and
correlation results are not measured causal errors unless supporting evidence
establishes causation.  A repeated failed hypothesis causes reassessment, not
automatic termination.

The operator deadline is authoritative only through
`python3 -m automation.campaign_guard status --state $EU4_CAMPAIGN_STATE`.
Never report a deadline stop unless that status shows no remaining time or a
campaign action is refused with `kind=deadline`; an unsupported correction path
or a missing observation is reported as its actual cause.

Keep a compact running record in `progress.md` or another allowed `analysis/m7/`
location containing: the divergent operation and affected output, relevant
original-engine/captured evidence, relevant Metal implementation evidence, the
leading unresolved explanation, the expected observation that would distinguish
it, and the outcome of the chosen experiment. Do not run a new GPU experiment
when the same question can be resolved by inspecting already available shader,
state or source evidence, and do not repeat a rejected experiment unless new
evidence materially changes the hypothesis. Preserve the distinction between
falsifying an intervention and eliminating an entire causal mechanism. Do not
rewrite a lengthy report after every unsuccessful experiment; produce a
detailed report when a meaningful causal finding, a milestone or a genuine
blocker is established.

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

1. Inspect the existing sealed evidence and prior experiments relevant to the
   current first divergence. Record identities, the first confirmed divergence
   and which earlier results remain valid for the unchanged inputs. Before a
   significant new GPU experiment, establish the evidence base:
   - identify the first divergent operation and its original caller, program,
     target resource and producer relationships from the authenticated event
     stream;
   - consult relevant original-engine knowledge with the native research tools
     (`eu4_re_query` for fast indexed lookups, `eu4_research` for sealed
     instructions and detailed decompilation) where the question is within the
     corpus;
   - inspect the actual captured GLSL, resource bindings, sampler/raster state,
     geometry and available historical experimental evidence;
   - compare those original inputs and semantics against the Metal replay;
   - identify the smallest unresolved semantic or numerical difference that a
     targeted experiment could discriminate.

   Label each relevant statement **Known original behavior** (runtime capture,
   executable instructions or existing qualified evidence), **Known Metal
   behavior** (implementation inspection or executed Metal evidence) or
   **Unresolved difference** (not established by either source), and prefer
   candidate-side experiments that separate unresolved differences. This is an
   evidence-first workflow, not a mandatory large reverse-engineering exercise:
   do not force Ghidra analysis when the disputed mechanism is texture-filtering
   precision, floating-point implementation detail or another behavior the
   original executable cannot resolve; do not query hundreds of unrelated
   functions; do not launch Ghidra or EU4. If the corpus is unavailable, report
   that limitation and proceed on other valid evidence when it is sufficient.
   The original authenticated OpenGL pixels remain the sole authoritative
   reference.
2. Run the synthetic drawable-format observer and focused offline tests only
   when the current task and existing verification evidence require them. Do not
   launch EU4 if a required observer is incomplete or a relevant prefix test
   fails; a full preliminary battery is not required at every goal round.
   Candidate-safe focused tests run entirely inside this worktree. Tests that
   need additional Git worktrees, shared Git metadata writes or other
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
   capture only when the required observation is genuinely absent from the
   existing evidence and the proposed capture can actually produce it. Do not
   attempt a capture from this objective and never construct a custom launcher
   or bypass the one-use launch guard.
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
8. Before declaring a candidate-scope limitation, give a concrete causal
   attribution to an excluded component, or state precisely why the next
   necessary discriminating experiment cannot be performed with available
   evidence and authorized tools. Declaring a component out of scope requires
   specific evidence linking the discrepancy to that component, not a plausible
   but unsupported attribution. If the defect belongs to a component outside
   the candidate's authorized scope (for example trusted shader translation or
   capture instrumentation), report the genuine candidate-scope limitation and
   the exact component. Do not compensate by changing unrelated renderer
   behavior; a correction belongs in the component that owns the defect.

## Stop conditions

Stop on any of: verified success for the configured target scope; a genuine
evidence gap that cannot currently be observed; an unavailable or unsafe
environment; an explicit operator cancellation; or the operator's execution
deadline as reported by `campaign_guard`. A failed exact-prefix comparison
requires investigation, not campaign termination, and a first divergence does
not end the campaign after a correction exposes the next one; a repeated failed
hypothesis is a reason to reassess rather than a quota stop. Do not merge, push
to `main`, modify historical seals, or relax exactness.
