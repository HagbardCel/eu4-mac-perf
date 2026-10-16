# EU IV Oracle v2 — plan of record

**Status:** authorized execution sequence. This records decisions only; detailed design
lives in the documents named under *Ownership* in [README.md](README.md). Do not spawn
competing architecture documents.

## Mission

Primary next-phase capability: **establish correct ground truth for automated,
agent-driven C++ reconstruction of large parts of the graphics stack.** The Oracle
(capture + validation infrastructure) and RE0–RE3 reconstruction are primary; the
power/heat objective is the downstream consumer.

## Accounting invariant (P4)

Per successfully qualified hook, per epoch:

    N_intercepted = N_unique + N_verified_repeat + N_incomplete + N_counted_only

- Event loss and instrumentation coverage are tracked **separately**.
- Counters are exact only for hooked sites; they do not imply whole-program coverage.
- Cheap per-hook counters stay exact even when deep capture is disabled or a bounded
  buffer overflows; overflow/loss moves invocations into `N_incomplete`/loss only.
- Only fully-captured, canonically-comparable observations may become `verified_repeat`.
- Already reflected in the prototype `ObservationLedger.summary`.

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
target before launch; seal partial as partial.

## Phases

| Phase | Deliverable | Exit gate |
| --- | --- | --- |
| **P0** | Baseline record | Frozen revision; honest report incl. pre-existing failures; sealed capsule re-verified |
| **P1** | `docs/repo-map.md`, `REUSE_MAP.md`; #44 ownership reconciled | Complete reuse map; **no code moved** |
| **P2** | Bundle preserved (sha256+manifest+provenance, scanned) → seed migration + CI wiring; examples inactive | Original preserved (commit+tag or digest manifest); integrated tests + CI green; no evidence invalidated |
| **P3** | Synthetic OpenCode slice, isolated worktree | Real OpenCode repairs a **supplied defective** candidate via oracle diagnostics + held-out verdict; controller abort/budget/receipt + negative-path tests pass |
| **P4** | Function-agnostic, policy-pluggable recorder + synthetic target | Two functions sharing mutable state; resource create/update/destroy + handle reuse; aliasing across serialize/replay; equal-args-different-pre-state not deduped; missing resource → `INCOMPLETE_ORACLE`; counters exact under disabled capture/overflow; reentrancy/concurrency safe; loss reported. R0-M **protocol** compatibility validated offline |
| **P4b** | Synthetic graphics **replay** harness | Reconstruct a frame from recorded synthetic resources incl. resource closure + handle reuse |
| **P5** | One co-designed authorized acquisition; first real function (`_GfxSetConstantBuffer`) | Pre-registered frame target + closure predicate met: ≥1 qualified replayable function fixture **and** ≥1 complete independently replayable frame. Seal partial as partial; document gaps; **go/no-go** before reconstruction claims |
| **Subsequent** | RE0–RE2 reconstruction loops | RE3 binary integration optional, separately gated |

## Hard rule

No further EU IV launch until **P3** and **P4/P4b** pass acceptance.
