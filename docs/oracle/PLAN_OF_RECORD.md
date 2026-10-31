# EU IV Oracle v2 — plan of record

**Status:** authorized execution sequence. This records decisions only; detailed design
lives in the documents named under *Ownership* in [README.md](README.md). Do not spawn
competing architecture documents.

## Mission

Primary next-phase capability: **establish correct ground truth for automated,
agent-driven C++ reconstruction of large parts of the graphics stack.** The Oracle
(capture + validation infrastructure) and RE0–RE3 reconstruction are primary; the
power/heat objective is the downstream consumer.

The reconstruction workstream is **function-first**: the first real target is a single
bounded Gfx function (`_GfxSetConstantBuffer`), reconstructed and validated offline before
any further Oracle infrastructure is built. Acquisition splits into **P5a** (function-level)
and **P5b** (frame-level), each separately gated (see *Pre-P5 offline integration gate*).

## Amended implementation plan (2026-10-23): capture once, replay repeatedly

The reconstruction workflow is **two-tier**. **Tier 1 (offline):** qualified live
fixtures → isolated replay → exact differential comparison. **Tier 2 (live shadow):**
actual invocations → asynchronous replay in an isolated worker, with the original
always authoritative. Both tiers consume the same fixture projection. Neither
authorizes replacing game code.

| Stage | Deliverable | Live EU IV? |
| --- | --- | --- |
| 0 | Scope contract (this section and the acceptance in [P4_INTEGRATION.md](P4_INTEGRATION.md)) | no |
| 1 | **PR A1** resource materialization; **PR A2** function-fixture contract | no |
| 2 | **PR B** passive x86-64 entry/exit hook; **PR C** generic offline replay and comparator | no |
| 3 | **PR D** P5a first live acquisition — **accepted 2026-10-25** (`gfx-live-20261025T155406Z`, 64 qualified, manifest `e8914fb1…ea437`; implementation merged at `09223f8`) | one bounded session |
| 4 | **PR E** Tier 1 live-grounded certification (E1 certifier, E2 independent audit) | no |
| 5 | **PR F** Tier 2 shadow validation (≥1,000 sampled comparisons; F1 offline worker now, F2 gated) | controlled sessions |
| 6 | **PR G** second-function scaling and agent pilot (G1 now; G2–G4 gated) | conditional |
| 7 | **PR H** repeatable multi-function campaigns — proposed only, not authorized | proposed |

A1 and A2 must pass before the first instrumented run; Tier 2 is enabled only after
Tier 1. **RE3** (automatic in-game replacement) is deferred until both tiers succeed
*and* replacement has its own reversible integration tests; the hook introduced by
PR B is observation-only. **P4.11/P5b do not block P5a.**

Every added abstraction must map to a demonstrated false-positive risk or an
essential scaling requirement. Deliberately deferred: frame-level replay/P4.11,
live shadow against real game objects, automatic in-game replacement/RE3,
multi-function campaigns before the G pilot (**PR H is proposed only**), and
new agent frameworks. Automatic memory-graph discovery stays deferred.
General dynamic access tracing of one preselected function is the next trace
milestone in
[Function-agnostic trace stage (GEN0, 2026-10-30)](#function-agnostic-trace-stage-gen0-2026-10-30).
The bounded synthetic stage remains
[Trace and fixture stage (2026-10-26)](#trace-and-fixture-stage-2026-10-26).

## Parallel development (2026-10-24)

PR D's implementation is complete and merged at `09223f8`. **D-live evidence (2026-10-25):**
one operator-authorized bounded session (`gfx-live-20261025T155406Z`) with reporter
`accepted` and 64 A2-qualified fixtures; transcripts are operator-local. **This section
authorizes no further EU IV launch and no additional live transcript without separate
operator authorization.**

Implementation readiness and certification readiness are separate. Offline
components may be implemented and merged in parallel; a result earns a gate only
when its named evidence exists. Passing a package's own tests does not certify
another package's evidence, and a synthetic or mocked downstream input never earns
the downstream gate.

### Initial parallel set

Start **E1**, **F1**, **G1** and **G3a** from the amendment merge commit. **E2** may
prepare adversarial fixtures once the E result and admission contracts are frozen;
its final audit waits for E1. Do not start F2, G2, G3b, G4 or H1 until the gates
below are met. The five initial work orders are under
[`work-orders/`](work-orders/).

| Package | Deliverable | Implementation may start | Result counts when |
| --- | --- | --- | --- |
| **D-live** | One authorized live session, ≥50 A2-qualified fixtures | Operator authorization | Accepted, sealed live evidence |
| **E1** | Portable sealed-session admission and Tier 1 certifier | Now | Real session admission, qualified replay and pre-registered coverage policy |
| **E2** | Independent adversarial certification audit | E result and admission contracts frozen | Audit of E1's implementation and results |
| **F1** | Offline shadow worker | Now | Owned snapshot contract, bounded execution and correct accounting |
| **F2** | Live shadow integration and ≥1,000 sampled comparisons | Design constraints now; live code after E and F1 | Approved sampling plan and separate live authorization |
| **G1** | Second-function RE0 and package boundary | Now | RE0 characterization and RE0→RE1 gate |
| **G2** | RE1/RE2, per-function replay adapter | After G1's RE0→RE1 gate | Faithful reconstruction, harness and independently qualified reference |
| **G3a** | Agent adapter seam on `synth:apply` | Now | Adapter reuses P3 primitives without modifying certified P3 |
| **G3b** | Real-function agent pilot | Interface after E schema + G1 boundary | Offline pilot after G2 and qualified reference evidence |
| **G4** | Second-function live qualification | After the per-function offline half-gate | New function-specific authorized live acquisition |
| **H1** | Repeatable campaign orchestration | After the G pilot | Proposed only; demonstrated orchestration need |

**G3b is an offline reconstruction pilot, not an E1 certificate.** G3b uses G2's
independently qualified original-code oracle and per-function RE2 comparator. Its
result carries a distinct evidence tier — Ghidra P-code execution, native
original-code execution and live game observations are not interchangeable.
G3b may exercise E's result contract with mocked or synthetic responses only when
the test is labelled as such. Genuine E certification of a second function waits
for evidence at E's supported tier, normally G4.

**E1 remains specific to the sealed PR D live session.** It must not become a
multi-tier certification framework now. A future separately qualified
emulator-evidence tier requires its own explicit admission policy.

F2's ≥1,000 comparisons are an experimental requirement, not only an accounting
target. Multiple 64-ticket sessions are the conservative baseline; the aggregate
campaign is decided after F1 is working and the operational cost of one session is
measured. Do not silently raise PR D's quota. Replaying files after their sessions
have ended is offline validation, not live shadow validation.

The 64-ticket baseline in that paragraph is the historical PR-D acquisition
approach. It does not constrain `exact-dedup/v1`.

### Historical: ACQ diversity-aware P5a capture (superseded 2026-10-26)

The paragraphs below are a superseded implementation plan. ACQ-1, ACQ-2, and the
E1-v2 adapter are not authorization to start work.

**ACQ-0** freezes [ACQ_DIVERSITY_V1.md](ACQ_DIVERSITY_V1.md): diversity-aware admission
for `_GfxSetConstantBuffer` under `EU4-GFX-LIVE-SUMMARY v2` (`acq-diversity/v1`).
Screening is not equivalence; the 64-record quota is a feasibility invariant; anchors
and coverage cells commit only after durable write and native closure predicate pass.

**ACQ-1** and **ACQ-2** may start in parallel from the ACQ-0 merge commit
([work-orders/ACQ-1.md](work-orders/ACQ-1.md),
[work-orders/ACQ-2.md](work-orders/ACQ-2.md)). Neither authorizes a EU IV launch.
The **E1-v2 adapter** (ACQ-2) must pass its tests before the next operator-authorized
live session. Historical PR D sessions, v1 summaries, and E1 v1 admission stay
immutable.

PR D’s **64 tickets reserved before the first expensive read** is **v1 behavior only**;
v2 retains up to 64 **diverse retained** `FUNCTION-CAPTURE v1` transcripts, not the
first 64 invocations. P4 invocation-ledger names are not reused for v2 counters.

Integrated offline gate after both merge: native capture vs ACQ-2’s Python model
(contention, failure injection, counter reconciliation, v1 regression).

### Active P5a recorder — `exact-dedup/v1`

The sole active P5a recorder specification is
[EXACT_DEDUP_V1.md](EXACT_DEDUP_V1.md) (`exact-dedup/v1`), merged in PR #94
(`7b17c83`): a reusable 64-slot pool, exact canonical-byte dedup, and a runtime
`max_unique` of 1..4096. Offline tests passed. No EU IV launch is authorized.
E1 integration is later, after the new summary is reportable on its own.
`ACQ_DIVERSITY_V1.md` is not edited. PRs #92 and #93 are superseded, will not be
merged, and are scheduled for administrative closure.

### Minimum frozen contracts

| Contract | Freeze now | Defer |
| --- | --- | --- |
| Capture evidence | Sealed session admission, hash integrity and immutability; v1 PR D sessions immutable; future sessions per EXACT_DEDUP_V1 | Further acquisition formats |
| Certification result | Identity, session/fixture admission, candidate evaluation, coverage and promotion | Function-specific extensions |
| Shadow invocation | Owned snapshot handoff, identity, result and failure vocabulary | Live throughput architecture |
| Function package | G1's documented deliverable boundary | Generic schema for future functions |
| Execution observation | [`execution-observation/v1`](TRACE_V1.md): one schema, two producers, one consumer | Branch-local event extensions; E1 admission; native replay of EU IV functions |

### Branch and merge discipline

- Branch from the amendment merge commit, not `09223f8`, so every agent sees the
  same frozen contracts and work order. That instruction records the base used for
  the E–G amendment. It is not the base for new work; new work branches from
  current `main`.
- A work order owns its paths. Frozen contracts and certified P3 components are
  read-only; a required change is a focused contract amendment, not a branch-local
  edit.
- A PR may merge with synthetic fixtures or a mocked downstream dependency only if
  its description labels that evidence and claims no downstream gate.
- Each work order carries three stopping rules: **contract conflict** (propose an
  amendment, do not silently change shared behaviour), **scope expansion** (obtain
  an architectural decision before adding a subsystem) and **evidence limitation**
  (finish the independently testable part and report the blocked acceptance
  honestly).

## Trace and fixture stage (2026-10-26)

This stage asks whether observed machine behavior can become a trustworthy
fixture. It does not ask whether that can be done inside EU IV. A passing
synthetic QBDI trace is not evidence that interception works in the translated
game process.

The milestone is one synthetic leaf and one synthetic caller–callee pair. It
does not reconstruct `_GfxForceWireframe`, `_GfxSetPrimitiveType`, or
`GetOpenGLPrimitiveType`. It does not add an agent, a function-packaging SDK,
or a second fixture format. The canonical schema is
[`execution-observation/v1`](TRACE_V1.md). Later branches do not extend it
locally. `reconstruction_trace/` stays outside `oracle/` and does not retrofit
`FUNCTION-CAPTURE v1`, replay, or E1.

Showing that QBDI can trace, and that a builder can transform handwritten
traces, is not enough. If TR0 supports QBDI, one actual QBDI trace must become
a qualified fixture and pass a fresh replay
(`tests/test_trace_qbdi_fixture.py`). If TR0 is unsupported, that integration
is blocked and this definition of done is amended in the same decision. A
Ghidra trace does not complete TR0.

### Authorized offline work

After this amendment merges, **TR0**, **TR1**, and **INV** may proceed in
separate worktrees. **TR2** composition may start from handwritten traces once
TR1's schema implementation is accepted. Its QBDI integration waits for a
supported TR0 producer, or for the explicit unsupported decision. Work orders:
[TR0](work-orders/TR0.md), [TR1](work-orders/TR1.md), [TR2](work-orders/TR2.md),
[INV](work-orders/INV.md).

This amendment does not start F2, G2, G3b, G4, or H1. G2 has not started. G2
retains its existing RE0 handoff and authorization gate.
`execution-observation/v1` may be used as upstream behavioral evidence or
fixture input. Any change to G2's existing capture-closure or replay-adapter
contract requires an explicit amendment when G2 starts. This PR does not edit
the frozen `_GfxForceWireframe` package. G3b stays gated. E1 does not admit
these traces. There is no G2a package.

### Frozen for this stage

Read-only: `oracle/function/**` (including `fixture.py`), `oracle/recorder/**`,
`oracle/certify/**`, `oracle/shadow/**`, `oracle/function_agent/**`,
`oracle/slice/**`, `FUNCTION-CAPTURE v1`, PR-D session
`gfx-live-20261025T155406Z`, E1 policies, P3,
`reverse_engineering/target.json`, and the existing RE0–RE2 artifacts under
`analysis/reconstruction/_GfxSetConstantBuffer/` and
`analysis/reconstruction/_GfxForceWireframe/`. No launch path is added to
`benchmark/eu4_gfx_live_capture.py`.

### TR3 gate — not authorized

TR3 has a preflight-only work order at
[work-orders/TR3.md](work-orders/TR3.md). The preflight passed on 2026-10-29
at `0a24c90`: `HEAD` matched `origin/main`, and both TR2 modules ran with
zero skips. The work order records prerequisites and a proposed later
experiment. That pass authorizes no implementation, EU4 launch, injection,
live trace, or replacement. The implementation scope is
[work-orders/TR3-IMPL.md](work-orders/TR3-IMPL.md). Writing its named paths
waits for that work order to merge. Running EU4 remains unauthorized.

The proposed experiment is exactly one preselected original
`_GfxSetConstantBuffer` invocation, with entry and exit state, memory events,
completeness, one native-derived fixture replayed against the existing
qualified reconstruction, and no dropped events. The TR0 receipt is the
measurement reference. This gate sets no slowdown threshold. This section
authorizes no EU IV launch, injection, live trace, or replacement.

### Definition of done

- An auditable QBDI/Rosetta receipt, or an explicit unsupported decision. A
  Ghidra substitution does not complete TR0.
- When QBDI is supported, one fixture derived from an actual QBDI trace, using
  the generic harness inventory and no function-specific dependency map. When
  QBDI is unsupported, this item is blocked and this list is revised in that
  decision.
- Fresh execution of the original synthetic function matches the independent
  expected observations, including a stored pointer relocated to a new base.
- A missing input and an incomplete trace are rejected.
  `expected_accesses_match` alone does not qualify a fixture.
- The original and reconstructed caller–callee pair agree, including one direct
  call and one indirect call. A defective callee produces a reproducible
  mismatch. The invalidated object is the validation result.
- A bounded readiness shortlist joined by pinned address. An unresolved or
  unvisited edge is not a ready leaf.
- The P-code importer checks the frozen receipt, labels `value_source`, and
  fabricates no load value. It does not execute the receipt.
- PR-D, E1, P3, and existing RE0–RE2 evidence are unchanged.

## Function-agnostic trace stage (GEN0, 2026-10-30)

The next trace milestone is one bounded execution of one preselected function,
given an executable identity and a function address, without function-specific
C. Descriptor fields, budgets, control flow, and the split between
execution-trace completeness and replay-state completeness are defined in
[`trace-target/v1`](GENERIC_TRACE_V1.md). This stage does not extend
[`execution-observation/v1`](TRACE_V1.md). The existing TR3 native JSON remains
the raw acquisition artifact. Historical function-first RE0–RE2 and P5a text
above stays in force. This section authorizes no EU IV launch, injection,
live trace, or code replacement.

### Recorded experiment

Committed receipts under `reconstruction_trace/tr3/` demonstrate controlled-host
one-shot QBDI interception under Rosetta. `startup_receipt.json` records
`live_gfx_set_constant_buffer` as `not authorized` and the EU4 claims as
`not demonstrated`. `receipt.json` is the controlled host `synth:tr3-oneshot`.

An operator report, not a sealed session, describes the first EU4 interception.
No session id and no log digest are recorded here. QBDI captured one dynamic
path of 15 instructions and eight memory operations, including `[RDI]`,
`[RSI]`, and the writes at context `+0xc8`, `+0xd0`, and `+0x128`, without
those objects in the entry inventory, and reported reaching the natural return
boundary. It did not establish transparent CPU state for an arbitrary function
and produced no replay-qualified fixture. It is not E1 evidence and authorizes
no further launch.

At GEN0, `insert_stub.S` restored `rbx`, `r12`–`r15`, `rbp`, `rsp`, `rax`,
`rdx`, and exit `rip` only. It did not restore `rcx`, `rsi`, `rdi`,
`r8`–`r11`, or `eflags`. GEN1 changed that stub on the controlled host, and
continuation for the supported register and flag state is GEN1 acceptance
condition 3. That GEN0 limitation is superseded there. This amendment does
not change the stub.

### Authorization

**GEN1** is the native tracer, from
[work-orders/GEN1.md](work-orders/GEN1.md). It does not authorize live use.
After this amendment merges, **GEN2** may start from
[work-orders/GEN2.md](work-orders/GEN2.md). GEN2 is offline projection of an
existing GEN1 raw record into `execution-observation/v1`. It authorizes no
EU4 launch, no native instrumentation change, no fixture-schema change, and
no new evidence tier. **GEN3-A** may be written from
[work-orders/GEN3.md](work-orders/GEN3.md). After that work order merges,
**GEN3-B** may repair the launcher. One live EU4 launch remains a separate
operator authorization after both merge and the GEN3 readiness review
passes. Deferred work and read-only paths are that work order together with
[`GENERIC_TRACE_V1.md`](GENERIC_TRACE_V1.md). No launch path is added to
`benchmark/eu4_gfx_live_capture.py`.

## Invocation accounting (P4)

Per successfully qualified hook, per epoch, the categories are **mutually exclusive**:

    N_intercepted = N_unique + N_verified_repeat + N_incomplete + N_counted_only

| Situation | Category |
| --- | --- |
| Complete observation, first occurrence | `unique` |
| Complete observation, verified equivalent to an already-seen observation | `verified_repeat` |
| Detailed observation started but required data is missing | `incomplete` |
| Invocation counted, but deep capture was disabled or never began | `counted_only` |
| Collector transport/storage event lost | **separate loss counter** + affected-evidence status |

- **Loss is not a member of the equation.** A lost event must never make an intercepted
  invocation vanish from the partition nor be double-counted; it is recorded separately and
  marks the affected evidence status.
- Counters are exact only for hooked sites; they do not imply whole-program coverage.
- Cheap per-hook counters stay exact even when deep capture is disabled or a bounded buffer
  overflows; overflow/loss moves invocations into `incomplete` or the loss counter only.
- Distinguish a **qualified** hook counter (valid despite trace loss) from an **unqualified**
  counter whose own updates may have been missed.
- Only fully-captured, canonically-comparable observations may become `verified_repeat`.
- Encoded in the prototype `ObservationLedger.summary`; becomes a deterministic P4 test contract.

## Certification (P5)

Levels: **acquisition** (evidence container valid) → **fixture** (complete + replayable
for the declared domain) → **observable domain** (adequate for the specific claim) →
**candidate** (passes qualified tests for that domain). Two axes: fixture adequacy ×
candidate result. No corpus-wide boolean.

Acquisition splits into two **separately gated** scopes: **P5a** (function-level - a fixture
complete for a declared function observable domain) and **P5b** (frame-level - a fixture
complete and independently replayable for a declared frame boundary). A P5a qualification
confers no P5b, graphics-API-equivalence or RE3 authorization.

## Epochs / shared launch

One process launch where protocol and operational compatibility permit, with **separate
measurement contracts**. Each epoch carries its own generation ID, instrumentation mode,
collector configuration and acceptance record. Co-design with R0-M is conditional on
**protocol** compatibility; runtime non-interference comes from epoch isolation +
fail-closed admission, not from pre-launch tests. Pre-register the minimum acceptance
target before launch; seal partial as partial. **Epoch isolation reduces measurement
contamination but does not by itself prove runtime non-interference.**

## Frame replayability and resource closure (P4/P5)

For a captured frame `F`, let `R(F)` be the transitive set of resources **and graphics
state** required to execute its command stream. `F` is independently replayable only if
every member of `R(F)` has a reproducible representation, including the relevant resource
versions and initialization dependencies.

`R(F)` includes more than buffer and texture contents. Depending on the captured commands it
may include shader/program linkage, uniforms, vertex layouts, framebuffer attachments, GL
context capabilities, implicit graphics state, and render-target contents produced by earlier
commands.

The acquisition must declare a **closure method** and demonstrate it before a frame is labelled
independently replayable:

- **Option A (preferred)** — a bounded resource registry live from initialization, observing
  all relevant creations and updates from a point early enough to be complete. Its
  instrumentation cost must be qualified against the R0-M performance epoch.
- **Option B** — a verified resource checkpoint reconstructing all required live resources at
  the capture boundary (contents, shader definitions, formats, bindings, generation IDs).
  Extracting these from a running GL context may itself be intrusive or incomplete, so Option B
  is not assumed to be always feasible; this is why Option A is retained.

The closure verifier establishes that there are **no unresolved resource-or-state dependencies
for the declared replay boundary** — not merely that every referenced handle has a registry
entry. Incomplete closure ⇒ `INCOMPLETE_ORACLE`; seal partial as partial.

## Phases

| Phase | Deliverable | Exit gate |
| --- | --- | --- |
| **P0** | Baseline record | Frozen revision; honest report incl. pre-existing failures; sealed capsule re-verified |
| **P1** | `docs/repo-map.md`, `docs/oracle/REUSE_MAP.md`, reproducible repo audit (`docs/repo-{inventory,edges,lifecycle}.csv`, `docs/repo-audit-context.json`); #44 ownership reconciled | Complete reuse map; **frozen inventory reproduces exactly**; every tracked path classified; no archival decision authorized by missing evidence; **no code moved** |
| **P2** | Bundle preserved (sha256+manifest+provenance, scanned) → seed migration + CI wiring; examples inactive | Original preserved (commit+tag or digest manifest); integrated tests + CI green; no evidence invalidated |
| **P3** | Synthetic OpenCode slice, isolated worktree | Real OpenCode repairs a **supplied defective** candidate via oracle diagnostics + held-out verdict; controller abort/budget/receipt + negative-path tests pass |
| **P4** | Function-agnostic, policy-pluggable recorder + synthetic target | Two functions sharing mutable state; resource create/update/destroy + handle reuse; aliasing across serialize/replay; equal-args-different-pre-state not deduped; missing resource → `INCOMPLETE_ORACLE`; counters exact under disabled capture/overflow; reentrancy/concurrency safe; loss reported. R0-M **protocol** compatibility validated offline |
| **P4b** | Synthetic graphics **replay** harness | Reconstruct a frame from recorded synthetic resources incl. resource closure + handle reuse, using the declared closure method |
| **P5a** | First authorized real-function acquisition | ≥1 live-grounded, **closed** function fixture with a declared observable domain and independently validated entry/exit evidence. Seal partial as partial; frame replayability is **not** required |
| **P5b** | Frame-level acquisition and qualification | ≥1 independently replayable frame with verified resource/state closure and a declared comparison boundary. Seal partial as partial; the **go/no-go** here governs **frame-level replayability claims only** |

**RE0–RE2 run in parallel, not after P5.** The function-first reconstruction workstream — RE0's
contract, the bounded offline original-code execution pilot, and offline RE1/RE2 reconstruction
with differential comparison — is **independent of P5a/P5b** and is **not** subsequent to frame
qualification; it precedes or parallels **P5a**. **RE3** (narrow in-process replacement) is
**deferred** by the two-tier amendment above: it is authorized only after both validation
tiers succeed *and* replacement has its own reversible integration tests, and it remains
separately gated by detour, ABI, fallback and runtime-safety requirements. The next hook
(PR B) is observation-only.

> **P3 status (2026-10-17):** **complete and released.** PR-A - the deterministic
> slice (synthetic target, harness, completeness comparator, five-tool MCP bridge,
> restart-safe budget ledger, controller with process-group abort and immutable
> candidate snapshots, sealed receipts) - is implemented under `oracle/slice/`.
> PR-B pins the OpenCode binary and performed the single declared acceptance run
> `p3b-acceptance-20261017`, which **certified** (visible 12/12, held-out 10/10);
> the sealed receipt is published and re-verified model-free in CI. Merged to
> `main` at `f751555` (PR #59) and released as the annotated tag
> `oracle-v2-p3b-20261017`, which points at that merge commit. See
> [P3_SLICE.md](P3_SLICE.md) and [P3B_ACCEPTANCE.md](P3B_ACCEPTANCE.md).

> **P4 status (2026-10-18):** **complete.** **P4r-A** (P4.1 record schema,
> canonicalization and immutable evidence store; P4.2 portable C11 synthetic target
> with a deterministic golden transcript), **P4r-B** (P4.3 native capture accounting;
> P4.4 declared capture policy; P4.5 offline finalization - resource identity,
> aliasing and semantic dedup; P4.7 concurrency and bounded capture) and **P4r-C**
> (P4.6 resource registry and closure verifier, so a missing resource yields
> `INCOMPLETE_ORACLE`; P4.8 R0-M epoch protocol compatibility validated offline) are
> implemented and merged under `oracle/recorder/`, with the recorder transcript
> deterministic across compilers and clean under ThreadSanitizer, ASan and UBSan.
> **P4b** is complete as well: `oracle/replay/` reconstructs a frame from a sealed
> capture of a synthetic graphics subject in a fresh process, resolving references by
> the closure rule P4.6 declares and failing closed on a missing dependency. Merged
> to `main` at `fdf0820` (PR #66) and released as the annotated tag
> `oracle-v2-p4b-20261018`, which points at that merge commit. See
> [P4_RECORDER.md](P4_RECORDER.md) and [P4B_REPLAY.md](P4B_REPLAY.md).

> **P5 status (2026-10-18):** the single **P5** phase is split into **P5a** (first
> real-function acquisition) and **P5b** (frame-level acquisition and qualification), with
> separate offline half-gates (see below). The reconstruction workstream is re-sequenced
> **function-first**: RE0 on `_GfxSetConstantBuffer` and an offline original-code execution
> pilot precede the function-oriented **P4.10**; frame-level **P4.11** gates **P5b** only and
> does not block **P5a**. This amendment authorizes no EU IV launch.

## Pre-P5 offline integration gate

P4b's synthetic replay is **not** this gate and does not satisfy it. P4b's subject
emits its own operation transcript through its own hook layer and supplies resource
contents itself, so a passing P4b shows that a complete transcript can be replayed —
not that the **generic P4 recorder** can produce an independently replayable frame.
The P4 recorder captures resource references and lifetimes, not resource contents.

**Progress (2026-10-23):** **P4.9** is implemented and merged. The **function** half of
**P4.10** is split into two reviewable PRs: **A1** — P4.9 export → sealed schema records
and immutable whole-version blobs, with native capture completeness kept distinct from
semantic qualification, collision-safe classification and an independently verified
capture binding; **A2** — the static `_GfxSetConstantBuffer` observable-domain descriptor,
descriptor-driven raw-memory closure, the versioned `FUNCTION-CAPTURE v1` transcript and
a synthetic paired-state producer. **P4.11** and the frame half remain.

**Progress (2026-10-24):** **PR D** is implemented as the operator-gated live path for
**P5a**, preserving PR B's offline TLS behavior and the A2 descriptor/schema and PR C
replay contract. It provides startup attestation (expected executable, x86-64 main
image, site within main text, patch bytes), a bounded live thread table with entry-side
registration inside the armed-epoch guard, 64 tickets reserved with a saturating atomic
CAS before the first expensive read, one `DISARMED -> ARMED -> CLOSING -> SEALED` epoch,
close waiting for active/reserved captures, one coordinator/drain writer, exact
intercepted/admitted/completed accounting, strict loss-free acceptance, and verified
session manifests.
The native harness and portable report-contract tests pass, and preflight succeeds
against the local pinned GOG build. **One bounded P5a acceptance session has been run**
(2026-10-25, `gfx-live-20261025T155406Z`). Further live sessions require separate
operator authorization; no CI job launches the game.

The gate splits by acquisition scope. Each half must pass **entirely offline** before the
matching acquisition is authorized:

* **Function gate (authorizes P5a):** **P4.9** + the **function-oriented** part of
  **P4.10** - materialization to schema-valid observations, immutable content blobs,
  record-to-blob closure relative to the declared function observable domain, collision-safe
  classification, and domain-specific **negative admission** - plus the static hook-site and
  ABI checks. It need not establish frame replayability.
* **Frame gate (authorizes P5b):** the frame-oriented part of **P4.10** (complete
  command-stream accounting) + **P4.11** (declared `gfx-v2` translation, state-dependent
  replay, resource/state closure and negative frame admission), or a qualified alternative
  such as a vetted Apitrace-based trace path.

A passing half-gate makes the matching acquisition *eligible for authorization*; it does not
authorize a launch. Neither half-gate establishes closure for the real game in advance - that
is exactly what P5a/P5b must attempt to capture. Each must establish that its slice of the
pipeline works end to end on a sufficiently representative synthetic workload.

| Requirement | Necessary demonstration | Slice | Gate |
| --- | --- | --- | --- |
| Resource-content capture | The native recorder records initial contents and relevant mutations, not just IDs | P4.9 | function + frame |
| Whole-version content | Exported content is a complete resource version, not a fragment, and is attributable to it | P4.9 | function + frame |
| Identity and versioning | Reused handles resolve to the correct generation and resource version | P4.9 / P4.10 | function + frame |
| Complete command stream | Native event accounting reconciles with the captured operations | P4.10 | function + frame |
| Materialization and blob closure | Observations materialize to schema-valid records and immutable blobs; every declared reference resolves over the whole store | P4.10 | function + frame |
| Collision-safe classification | Canonical equality is decided within hash buckets; collisions are counted and observable | P4.10 | function + frame |
| Function negative admission | Removing content, a mutation or a required observation prevents qualification of the declared function domain | P4.10 | function |
| Resource/state closure | No unresolved dependencies for the declared synthetic frame boundary | P4.11 | frame |
| Independent replay | A fresh process reconstructs the frame from the native recorder's sealed output | P4.11 | frame |
| Frame negative admission | Removing content, a mutation or a required event prevents frame qualification | P4.11 | frame |

The recorder-to-replay integration is not to be marked complete merely because the
standalone synthetic P4b replay passes.

The gate is sliced **P4.9** (bounded resource-content capture and the versioned
observation export), **P4.10** (offline materialization, content-blob closure and
collision-safe deduplication) and **P4.11** (declared `gfx-v2` translation, stateful
replay and negative admission), each separately reviewable and separately revertible.
Its interfaces and acceptance are in [P4_INTEGRATION.md](P4_INTEGRATION.md).

### Two gates, and why P5 is not circular

The hook-pack requirement splits in two, and conflating the halves would make the gate
circular:

* **Offline, before P5a/P5b:** the matching half-gate pipeline above, plus the
  *static* hook-pack checks - executable-byte identity against the pinned EU IV
  identity in `reverse_engineering/target.json`, and site validation. These read files
  and need no game process.
* **In P5a/P5b:** whether the instrumentation actually behaves safely inside EU IV, and
  whether the intended real evidence can be acquired. This is a bounded,
  operator-authorized runtime admission experiment with fail-closed abort conditions.
  It cannot be a precondition for the first authorized launch, because only that
  launch can answer it.

P5a and P5b therefore begin without assuming that the live hooks, the function fixture or
frame closure are already qualified.

## Engineering policy and progress measure

Each PR must name the real-function capability it advances; reuse existing evidence,
interfaces and tests before adding infrastructure; implement only what the declared
acceptance test requires; treat upstream invariants as documented rather than re-defended in
every downstream consumer; and stop when an experiment grows substantially beyond its initial
scope.

Progress is measured by **qualified real EU IV functions** - live-grounded closed fixtures;
original code executing offline under constructed state; reconstructions passing independent
differential tests - not by synthetic infrastructure volume. Test count and infrastructure size
are diagnostic metrics.

## Hard rule

No EU IV launch until **P3** and **P4/P4b** pass acceptance, and until the **applicable**
offline half-gate above passes: the **function gate** for any P5a function acquisition, the
**frame gate** for P5b. **P4b's synthetic replay alone does not satisfy either gate.** A
passing half-gate makes the matching acquisition *eligible for authorization*; it does not
authorize a launch. Live behavioural qualification is a P5a/P5b activity under fail-closed
abort, not a precondition for either. **P5a qualification is strictly limited to the declared
function-level observable domain** and confers no frame replayability, graphics-API-equivalence
or RE3 authorization. **RE3** remains separately gated by RE2 qualification plus detour, ABI,
fallback and runtime-safety requirements. No package in the parallel-development section
authorizes a launch.
