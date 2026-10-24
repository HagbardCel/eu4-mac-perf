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
| 3 | **PR D** P5a first live acquisition (~50 qualified fixtures) — implemented, live acceptance pending authorization | one bounded session |
| 4 | **PR E** Tier 1 offline certification | no |
| 5 | **PR F** Tier 2 shadow validation (≥1,000 sampled comparisons) | controlled session |
| 6 | **PR G** second-function scaling and agent pilot | conditional |

A1 and A2 must pass before the first instrumented run; Tier 2 is enabled only after
Tier 1. **RE3** (automatic in-game replacement) is deferred until both tiers succeed
*and* replacement has its own reversible integration tests; the hook introduced by
PR B is observation-only. **P4.11/P5b do not block P5a.**

Every added abstraction must map to a demonstrated false-positive risk or an
essential scaling requirement. Deliberately deferred: automatic memory-graph
discovery, general dynamic access tracing, frame-level replay/P4.11, live shadow
against real game objects, automatic in-game replacement/RE3, multi-function
campaigns, and new agent frameworks.

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
against the local pinned GOG build. **No live EU IV session has been run.** The single
bounded acceptance session is pending separate operator authorization; no CI job
launches the game.

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
fallback and runtime-safety requirements.
