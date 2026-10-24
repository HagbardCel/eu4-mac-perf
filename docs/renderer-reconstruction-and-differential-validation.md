# Renderer reconstruction and differential validation — RE0–RE3

- **Status:** proposed architecture-track workstream (not yet implemented or empirically qualified).
- **Scope:** pinned GOG EU IV 1.37.5 x86-64, Apple Silicon/Rosetta, initially paused Venice.
- **Canonical strategy:** [EU IV rendering roadmap](eu4_recommended_strategy.md).
- **Evidence:** [state of knowledge](eu4_macos_rendering_findings.md), the [cost-based alternatives ranking](eu4_rendering_alternatives_ranked.md), and the closed-hypothesis records ([border multi-draw](gfx-border-multidraw-ababa.md), [sub-record](gfx-subrecord-ababa.md)). That corpus is the standing constraint on this workstream: reconstruction must not reopen a closed hypothesis without genuinely new evidence, and must not disturb the frozen Phase-C diagnostic. This document proposes work; it does not change the status of existing evidence.

## Decision

Reconstruct a **small, high-value vertical slice of the Clausewitz Gfx/OpenGL path as faithful, testable C++ source**, validate it against original binary behavior, then use that reference to explore GL, command-architecture and eventually Metal changes. Work **bottom-up but demand-driven**: begin at a tractable function boundary, advance toward state/resource/draw submission and callers only where cost evidence and semantic dependencies justify expansion.

Do **not** require complete-engine decompilation or perfect understanding of every path before beginning bounded experiments. Reconstruction is a correctness and maintainability investment, **not an optimization by itself**. The goal of roughly 80–90% less static render work remains conditional on actual frame-evolution evidence; active-scene improvement comes from measured work elimination, backend cost reduction and caller/command redesign, not from source-language choice.

RE0–RE2 are **offline and function-scoped**: they require no frame replay, no graphics-API equivalence and no in-process replacement. The **first target** is `_GfxSetConstantBuffer` (entry RVA `0x15ec36a` in the pinned image: 21 instructions, 75 bytes, 0 selected direct callees), chosen for tractability and observability rather than performance payoff. The matching live acquisition is **P5a**; frame-level work (**P5b**) is gated separately and does not block RE0–RE2 ([Oracle plan of record](oracle/PLAN_OF_RECORD.md)).

## How this fits the existing work

| Existing workstream | Relationship |
|---|---|
| Phase C / R-mode | Preserve sealed evidence and R-mode baseline; no new invasive profiler-qualification loop |
| R0-M (PR #43 merged; residual evidence PR) | Continues independently: full-resolution frame-evolution census, per-thread CPU, sample-based attribution. A future live session may be **co-designed** for reference capture, but do not silently mix diagnostics with the measurement protocol |
| R0-A minimum cut, shader and pass graph | Supplies function boundaries, call hierarchy, state dependencies and a domain candidate |
| M0a standalone GL vs Metal single-draw probe | Proceeds independently; consult RE results when available but **do not block M0a** on reconstructed source or game integration |
| M0b ordered command-stream census | Provides meaningful, bounded end-to-end fixtures and state-entropy barriers for prioritizing RE beyond the pilot |
| M0c Metal-native redesign | Consumes validated semantic contracts to distinguish essential behavior from legacy GL bookkeeping |
| R2 / M1 interop | Remains a separate target/pass/synchronization feasibility gate |
| R4 / M2 first domain | Any EU4 in-process replacement required for the chosen domain must pass RE3-like binary integration and correctness gates; unrelated functions need not be reconstructed |

## Tool and language decisions

- **Analysis:** Ghidra disassembly and P-code, symbol/call-graph inspection, object-layout recovery, cross-references, CFG and dataflow checks. Decompiled C-like pseudocode is a hypothesis, never authoritative evidence by itself.
- **Reconstruction:** C++20 via Clang, with conservative C-compatible or verified C++ ABI interfaces at the binary boundary. Prefer explicit, small structs for recovered layouts; preserve calling convention, field offsets, object lifetime, return/exception semantics, side effects and relevant thread-local/context state. Compile an **x86-64** replacement for in-process use; never assume a native arm64 image can load into translated EU4.
- **Metal:** Objective-C++ as a C++/Metal boundary when needed; Metal Shading Language for shaders. Do not rewrite the faithful reconstruction in MSL or start with a one-for-one GL→Metal shim.
- **Tests and models:** C++ unit and property tests, mock/recording GL dispatch, bounded fixtures, optional SMT checks for small pure functions, standalone GPU/image tests. Prefer a **targeted** GL-state model over building a general OpenGL emulator.

## RE0 — Select and characterize one function (offline first)

1. Pin binary hash/build/architecture and address ranges; inventory function entry/exit, cross-references, direct/virtual callers, callees, thread/context assumptions, original GL calls and bypass paths.
2. Recover the minimum required object layout: field offsets, pointer ownership, buffers, texture/sampler IDs, context state, global and TLS accesses, alias relationships, exceptional paths.
3. Document a function **contract** with preconditions, postconditions, modified memory, external calls, ordering constraints and unobserved/uncertain cases. Record address and disassembly/P-code provenance separately from hypothesized source semantics.
4. The pilot is **selected**: `_GfxSetConstantBuffer` (entry RVA `0x15ec36a`; 21 instructions,
   0 selected direct callees), chosen for **tractability and observability**, not alleged
   performance payoff. RE0 characterizes *that* function. Reselect only if the RE0 → RE1 gate
   below fails, and then only among candidates ranked by the same test
   (`InternalSetTextureAndSamplerState`, `GfxDrawIndexed`, or a nearby Gfx setter).
5. **Offline execution pilot** (done for the pilot function, 2026-10-18): the reviewed
   instructions were executed in Ghidra's P-code emulator under an explicitly constructed
   state, on two frozen paths, with every expected memory operation asserted as an exact
   sequence and two runs byte-identical. Receipt and limits:
   `analysis/reconstruction/_GfxSetConstantBuffer/pilot/`. This establishes the behaviour of
   those **decoded instructions inside that emulator** — not hardware-level execution of x86
   machine code, and not runtime behaviour in the game — and is RE0 evidence. It does not
   substitute for the RE1 reconstruction or the RE2 differential.

**Gate RE0 → RE1:** identified callable boundary; finite, recoverable input/side-effect surface; credible strategy for calling or observing the original; no unresolved ownership/context requirements likely to make a faithful replacement unsafe. Otherwise pick a different function.

**Artifacts:** `analysis/reconstruction/<function>/contract.md`, source-address inventory, object-layout table, dependency map, and (for the pilot function) the offline execution pilot's frozen scenarios, receipts and runner under `analysis/reconstruction/<function>/pilot/`. Prefer adding these after the pilot target is selected.

### Function package (G1 deliverable boundary)

A **function package** is the documented hand-off from RE0/RE2 to the agent and
replay adapters. Its boundary is named by G1; it is not a generic schema for
unknown future functions. A package records:

- the pinned function identity and binary/build pins;
- the RE0 contract, object layout and dependency map;
- the fixture plan and the declared evidence tier;
- the harness and per-function replay-adapter plan;
- the package's explicit limits and unobserved cases.

G1 freezes this boundary; G2 fills the reconstruction and replay adapter, and G3b
consumes it. A second function may add its own small closure branch and replay
adapter rather than generalizing A2 or PR C. `oracle.function.replay` is
single-function (`_GfxSetConstantBuffer`) and returns `refused: unsupported-function`
for any other target until that adapter exists.

**The RE0→RE1 gate applies to every function, including the second.** A function
that cannot establish a callable boundary, a finite recoverable input/side-effect
surface, a credible independent-reference strategy, or unresolved
ownership/context safety does not proceed to RE1.

## RE1 — Faithful, unoptimized source reconstruction

- Write readable C++ with explicit low-level adapter boundaries and isolated GL calls.
- Match observed behavior before correcting redundancies or batching anything. Keep the original implementation as the reference.
- Use synthetic state fixtures to exercise branches and edge cases, with sanitizers where host and ABI constraints permit.
- Keep the source and binary integration separate; no modification to a user save or in-game behavior is required at this phase.

**Gate RE1 → RE2:** clean x86-64 build, deterministic offline tests, documented ABI/object-layout checks and explicit list of remaining unknowns.

**Artifacts:** `analysis/reconstruction/<function>/reconstruction/` — the handwritten
reimplementation, its ABI adapter and probe, the harness and a README recording what is proved and
what is not. Only narrow, handwritten functional reimplementations of documented behaviour are
tracked there (see [conventions](conventions.md)); decompiled source, original binaries and bulk
reconstructed source stay local.

**Status (2026-10-18):** the first reconstruction, for `_GfxSetConstantBuffer`, is tracked at
`analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/`. Its semantics are checked against
the RE0 instruction model and the frozen pilot scenarios; agreement with the original's *executed*
instructions is still RE2's job, not this one's.

## RE2 — Differential oracle and semantic comparison

Build a replayable harness that records and compares **observable behavior**, not just decompiler text.

**Reference acquisition, in descending order of strength:**
1. **Direct original-function invocation** in a controlled, equivalent context, compared with our reconstruction for the same initialized state, when safe and feasible.
2. **In-process original-path traces** with complete relevant inputs, state, GL side effects and postconditions, then replay with normalized IDs/pointers.
3. **Recorded result/command fixtures** when a callable oracle is infeasible. These only prove equivalence on captured cases and must not be represented as a general proof.

**Versioned fixture:** executable hash, function address, build, caller/context/thread identity, relevant entry object graph, arguments, external dependencies, original return and mutated state, ordered GL trace, errors/observables at suitable boundaries, and capture provenance. Serialize resource identity by stable logical handles; never naively replay process pointers or assume GLuint values identify equivalent resources across contexts.

**Three explicit comparison modes:**

| Mode | Correctness criterion | Intended use |
|---|---|---|
| **Faithful reconstruction** | Exact return/memory effects and identical normalized GL call sequence **where deterministic and completely observable** | RE1/RE2 promotion |
| **GL optimization** | Equivalent GL state and draw effects at defined observable boundaries; removed redundant calls are allowed | State/cache/command changes after RE2 |
| **GPU/backend redesign** | Equivalent scene/pass results under defined numerical/pixel tolerances, resource lifecycle and synchronization invariants; no stale content or invalid GL/Metal mixing | M0/R4 compatibility and command paths |

For identical deterministic GL renders, compare exact full-resolution hashes where justified. For different GPU backends, allow explicitly bounded per-pixel deviations and inspect representative difficult pixels/effects; **do not claim exact hash parity is universally attainable across GL and Metal**. Validate dynamic transitions, multiple contexts/threads, resource destruction, state invalidation and unexpected inputs; comparisons must not mask wrong ordering, undefined behavior or persistent-state divergence.

**Gate RE2 → RE3:** faithful path passes **all qualified reference fixtures** with no unexplained deviations; independent tests cover the identified control-flow and state classes; limitations and untested states are documented. Do not equate 100% fixture pass with mathematical proof of global equivalence.

**Artifacts:** `analysis/reconstruction/<function>/differential/` — the frozen input corpus, the
reference record of the original over them, and a README recording what is established and what is
not. The record carries its statement and limits verbatim, so the evidence cannot be read as more
than it is.

**Status (2026-10-19):** the differential for `_GfxSetConstantBuffer` is tracked at
`analysis/reconstruction/_GfxSetConstantBuffer/differential/`. It is acquisition option 3 above, not
a callable oracle: 414 frozen inputs — the cross product of the six declared slots with both paths'
sign and wrap boundaries, containing every RE0 pilot scenario at the same constructed state — were
executed through RE0's P-code emulator, and the reference record is what it observed. The RE1
reconstruction, compiled fresh, is reconciled with it byte-for-byte on the region both sides
describe, both sides starting from the same declared pre-state, with the stored buffer pointer
compared as a role and each side validated against what that role means. The reference is qualified
structurally before it is used as evidence, and the comparison refuses to run at all against one that
fails: it is bound to the corpus, to the pinned binary and to the executable Ghidra loaded — RE2 has one
frozen corpus, so a corpus supplied to the validator has to be that corpus, not a dictionary the
record's file digest does not describe; only an omitted argument means the committed artifact, so an
empty one is refused rather than replaced by the default; every
row's initial registers and constructed pre-state (canaries included) are the corpus's, its executed
path, decoded instructions, lengths, loads and ordered stores match the frozen path structure, and
its store values agree with its own post-state — while the word count itself comes from the
execution, never from a second arithmetic model. The instruction bytes every row records are held to
the pinned code window in `reverse_engineering/target.json`, derived from the pinned binary by the
repository's own Mach-O mapping, and the emulator script refuses to step an instruction unless the
bytes it is about to execute hash to the same pin. Both RE1 arithmetic
alternatives (a signed division on the buffer path, the missing `+3` wrap on the selector path) are
compiled as deliberately wrong reconstructions and rejected, each on its own path; a record from
another corpus, binary or executable, a perturbed, reordered or unqualified row, an altered
instruction path, an extra store and a withheld required input are all rejected too. It proves
equivalence **on those inputs and no further** — the README states the limits, including that P-code
emulation is not hardware execution and that the input sets are the paths' boundaries rather than an
exhaustive enumeration.

**Two-tier validation (2026-10-23).** The same method now has two tiers, both consuming one fixture
projection rather than separate formats. **Tier 1 (offline):** qualified fixtures → isolated replay →
exact differential comparison; the fixture is a digest manifest over the existing observation record
plus a per-function observable-domain descriptor, and its qualification is decided by
descriptor-driven checks (required regions, pointer aliases, ordered effects), never by the
candidate's output. **Tier 2 (live shadow):** real invocations are captured into owned snapshots and
replayed asynchronously in an isolated worker while the original remains authoritative; the worker
never holds live game pointers. Tier 2 is enabled only after Tier 1 succeeds. Neither tier authorizes
replacement.

## RE3 — Narrow in-process integration (deferred)

**Status: deferred (2026-10-23).** RE3 is not the next step. It is authorized only after Tier 1 and
Tier 2 both succeed *and* replacement has its own reversible integration tests; the hook that comes
next is observation-only. The requirements below remain the bar for whenever RE3 is resumed.

- Compile an x86-64 replacement; implement minimal, reversible redirection with a **verified ABI**, original trampoline/fallback and a hard runtime switch. Address ASLR, Mach-O relocation, instruction boundaries, thread safety, executable protection/signing and failure cleanup.
- Start in **observation/original mode**. Run static/offline preflight and integration harnesses first.
- Only then run one controlled, disposable paused-Venice fixture session, preferably **one launch with original → faithful replacement → original** stages and bundled correctness checks. Do not mix timing diagnostics into this correctness admission.
- Check relevant memory effects, GL state and image results, hover/map-mode/zoom where the chosen function is exercised, context transitions if applicable, faults, leaks and logs. Preserve save hashes and standard recovery procedures.
- On mismatch or missing precondition, fail closed to original code; abort on corruption or unsafe state. Treat Ironman games and untested builds as out of scope.

**Gate RE3 → domain expansion:** replacement is stable under the bounded integration tests; no unexplained semantic divergence; enabled/disabled toggle demonstrably reverts to the original; no regressions in save safety or process stability. Only **afterward** benchmark an actual optimization profiler-off, using existing ABABA and per-thread/wall/energy metrics. RE3 itself carries **no performance-saving claim**.

If RE3 is prohibitively difficult due to object ownership, exception/ABI coupling or unsafe detours, keep the reconstructed implementation as an **offline reference** and use narrower existing hooks or move to a different function. This is a valid stop decision.

## After the pilot: vertical-slice expansion, not horizontal porting

1. Expand only from the validated function toward a **complete** semantic slice: relevant Gfx setters → draw helper → shader/resource lifetime → selected renderer caller, driven by the R0-A minimum cut and M0b/M0c cost/entropy evidence.
2. Use faithful reconstructed code as a fallback/reference. Separate **compatibility-preserving** command/state optimization (C0a) from **representation-changing** work (C0b).
3. Support B0 and C0 for the **same** eventual Metal domain, with explicit render-pass/target boundaries; do not mix GL and Metal within a depth-sharing pass.
4. Re-evaluate ROI after every promoted slice. A function may be well understood and replaceable yet not economically worth optimizing.
5. Keep M0a single-draw and M0b/M0c feasibility studies moving; do not make them wait for complete RE3 or for a global port.

## Validation and efficiency rules

- **One game launch is costly:** maximize offline disassembly, ABI tests, fixture generation, harness tests and review before requesting a new live capture. Bundle reference inputs in the next permitted bounded live session only if this does not invalidate the R0-M experiment or compromise its intrusion rules.
- **No blanket claims of perfect understanding:** maintain an explicit confidence ledger (proven from code, observed at runtime, inferred, unknown), plus counterexamples and unresolved states.
- **No conflation of correctness and performance:** capture instrumentation is permitted for structural evidence and semantic comparison; primary savings claims still require low-overhead/profiler-off A/B validation.
- **No false proof by replay:** recorded snapshots do not exercise unseen callbacks, aliasing, asynchronous GL effects or edge-case state. Add targeted generative tests or new qualified oracles for each identified hole.
- **No resurrection of closed tactics as the main strategy:** reproducing a texture setter does not reopen generic GL setter caches as the priority. The primary architectural ROI remains command organization, resource representation, pass structure and elimination of unchanged work.
- **No premature Metal rewrite:** first identify semantic contracts and a tractable vertical domain; then translate shader/resource behavior into B0 and optimize caller representation via C0.

## Initial pull-request sequence

| PR | Content | Live EU4 launch? |
|---|---|---|
| **RE-PR1** | RE0 inventory, candidate decision, function contract and frozen reference schema | No |
| **RE-PR1b** | RE0 offline execution pilot: frozen paths/scenarios, P-code emulator script, runner, receipt and fail-closed checks — original instructions executed under constructed state. RE0 evidence, not RE1/RE2 | No |
| **RE-PR2** | RE1 faithful C++20 reconstruction written from the documented contract: six-position ABI adapter and probe, object-layout assertions, exact modular arithmetic with bounded verification, harness and tests. No GL mock at this stage (nothing in this function touches GL) and no claim of assembly-level reproduction | No |
| **RE-PR3** | RE2 differential fixture capture/replay, semantic comparator, oracle-strength labeling | **Only if** a qualified bounded reference capture is necessary; batch with an already-approved session when possible |
| **RE-PR4** | RE3 reversible binary hook and game integration with fallback; correctness evidence | One controlled launch **only after offline gates pass** |

Do not start RE-PR4 on the strength of pseudocode alone; do not prolong planning once RE0 establishes a viable candidate. Each PR should state what it proves, what it **does not** prove, and exactly which evidence authorizes the next stage.

## Decisions to make from results, not assumptions

1. Which **pilot** has the cleanest ownership/context boundary and observable reference?
2. Is direct oracle invocation safe, or are bounded capture/replay fixtures the strongest feasible evidence?
3. At which Gfx/caller boundary does expanding the reconstructed slice first unlock a credible, measured optimization opportunity?
4. Is x86-64 in-process replacement practical for that boundary, or should we rely on offline models plus a smaller detour?
5. Which invariants survive the move from GL to Metal, and which need a backend-specific tolerance or validation method?
