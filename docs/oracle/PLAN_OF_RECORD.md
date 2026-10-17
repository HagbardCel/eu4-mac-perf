# EU IV Oracle v2 — plan of record

**Status:** authorized execution sequence. This records decisions only; detailed design
lives in the documents named under *Ownership* in [README.md](README.md). Do not spawn
competing architecture documents.

## Mission

Primary next-phase capability: **establish correct ground truth for automated,
agent-driven C++ reconstruction of large parts of the graphics stack.** The Oracle
(capture + validation infrastructure) and RE0–RE3 reconstruction are primary; the
power/heat objective is the downstream consumer.

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

Levels: **acquisition** (evidence container valid) → **fixture** (complete + replayable)
→ **observable domain** (adequate for the specific claim) → **candidate** (passes
qualified tests for that domain). Two axes: fixture adequacy × candidate result. No
corpus-wide boolean.

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
| **P5** | One co-designed authorized acquisition; first real function (`_GfxSetConstantBuffer`) | Pre-registered frame target + closure predicate met (see *Frame replayability and resource closure*): ≥1 qualified replayable function fixture **and** ≥1 complete independently replayable frame. Seal partial as partial; document gaps; **go/no-go** before reconstruction claims |
| **Subsequent** | RE0–RE2 reconstruction loops | RE3 binary integration optional, separately gated |

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

> **P4 status (2026-10-18):** **in progress.** **P4r-A** (P4.1 record schema,
> canonicalization and immutable evidence store; P4.2 portable C11 synthetic target
> with a deterministic golden transcript) and **P4r-B** (P4.3 native capture
> accounting; P4.4 declared capture policy; P4.5 offline finalization - resource
> identity, aliasing and semantic dedup; P4.7 concurrency and bounded capture) are
> implemented and merged under `oracle/recorder/`, with the recorder transcript
> deterministic across compilers and clean under ThreadSanitizer, ASan and UBSan.
> **P4r-C** (P4.6 resource registry + closure; P4.8 R0-M protocol compatibility)
> and **P4b** follow. See [P4_RECORDER.md](P4_RECORDER.md).

## Hard rule

No further EU IV launch until **P3** and **P4/P4b** pass acceptance.
