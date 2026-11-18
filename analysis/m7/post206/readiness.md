# M7 readiness assessment (post-PR-#206)

Baseline: `f13602e4` (PR #206 merged). Preparation stack: #207 Phase C,
#208 Phase D, #209 evidence/pilot. This assessment is not a merge
authorization.

## Two readiness levels

**Offline diagnostic readiness: READY WITH LIMITATIONS.** The harness can
capture one process, seal and replay three consecutive authentic frames, detect
gap-free engine identities, run a same-backend GL/GL transfer control, localize
the first authoritative divergence, produce a compact agent package, and prove
a controlled later-frame fault. These operations are bounded and reproducible.

**Fully unattended migration readiness: NOT READY.** The remaining blockers
are concrete:

- no full-frame GL/GL replay (the control is transfer-scoped);
- `gl_dither` and `multisample` remain unsupported, so renderer equivalence is
  not claimed;
- the causal origin before event 284 is unresolved; the same-resource lower bound is unbounded;
- a model-driven mounted DSH session could not be established in this
  environment: the headless profile reaches `MISSING_CREDENTIAL` before any
  tool call, and the current Web session does not expose the `eu4-tools`
  plugin. The plugin bridge itself passes a read-only `gl-control` and Ghidra
  query smoke test (see `dsh-native-smoke.json`).

Do not describe the preparation as ready for unattended production migration.
It is ready for supervised, bounded diagnostic work and for migration tasks
whose acceptance does not claim full renderer equivalence.

## Required-evidence table

| Area | Required evidence | Result |
| --- | --- | --- |
| Capture | One-process, correctly ordered frame stream | **Pass.** One process (pid 84477) captured 3 sealed frames, 325 operations each; engine present indices 615/616/617 are consecutive; validator and case verifier report `gap_free`. |
| Reproduction | Deterministic GL/GL identity | **Partial.** The versioned transfer/readback control matches frame 1 exactly (0/1,930,176); frames 2/3 are `unavailable` because the historical capture lacks copy-only lifecycle coverage. A full-frame GL command replay is not implemented. |
| Differential validation | Genuine GL/Metal results with truthful classification | **Pass for diagnostics, not equivalence.** Three frames replay 325/325 operations with 0 failures, provenance verified, history `ok`; all are `difference_with_partial_state_coverage`; `gl_dither`/`multisample` unsupported. |
| Cross-frame behavior | Verified identities, resource continuity, later-frame fault detection | **Pass.** One persistent session; history `ok` at sequence 3; controlled frame-2 event-1 fault is detected at frame 2 event 1 and the campaign remains unqualified. |
| Diagnostics | Actionable first-divergence localization | **Pass.** Event 284 / default framebuffer / copy source; producer draw 283 / program 346; unbounded–284 (0–284 for bisection); normalized recommendation event 142; compact package ~6.3 KB. |
| Agent integration | Working DSH → tools → Ghidra → validation | **Partial.** Plugin definitions/bridge pass; read-only `gl-control` and Ghidra `_GfxResolve` queries pass. A mounted model session is blocked by `MISSING_CREDENTIAL`; readiness is downgraded accordingly. |
| Efficiency | Measured launches, test costs, tool calls, detours | **Pass with measurement.** 3 capture command invocations, 2 actual EU4 process starts (one failed `draw_cap`, one successful capture), 0 pilot launches; focused suites 419/328/492 tests; no duplicate focused suite runs; efficiency audit separates native tool failures from shell failures. |
| Reliability | Fail-closed behavior, reproducible artifacts, bounded execution | **Pass.** Fault experiments, gap/partial identities, missing copy provenance, superseded destination versions and bad digests fail closed. The 1.348 GB case uses an explicit 2 GiB budget; default 512 MiB yields an explicit incomplete summary. |
| CI | Focused regression coverage without heavyweight tests | **Pass for focused/CI.** All three PRs pass the portable workflow. The authentic case stays outside CI. Interactive/live benchmark modules are excluded from the routine offline gate after the full run reached a live prompt. |

## Evidence

- [Phase C qualification](phase-c-qualification.md)
- [Phase D diagnostics](phase-d-diagnostics.md)
- [Phase E0 pilot](phase-e0-pilot.md)
- [Machine-readable evidence](evidence.json)
- [Native DSH smoke result](dsh-native-smoke.json)
- [Reproduction commands](reproduce.md)

## Remaining limitations

- **No full-frame GL/GL replay.** The controlled path covers the captured
  copy/readback transfer only.
- **No renderer equivalence.** `gl_dither` and `multisample` are unsupported.
- **Causal interval unresolved.** The copy source differs; the origin is
  upstream. The next checkpoint is event 142 and needs a capture-side feature.
- **One authentic capture.** Three frames from one process are qualified; a
  longer gameplay sequence is not.
- **Mounted-session credential blocker.** The plugin mounts, but the available
  headless profile cannot authenticate. Exporting a provider key or using a
  Web session with the plugin mounted is required for unattended operation.
- **Full-suite claim.** The wider full run reached an interactive live
  benchmark prompt and was stopped; no full-suite pass is claimed. The focused
  suites and CI are the evidence used here.

## Proposed next task (not started)

**M7-P0 — Capture a selected default-framebuffer checkpoint near event 142.**

This is a separate PR after preparation, using reusable capture
instrumentation, not arbitrary checkpoints in #208.

1. Add one bounded capture option for a default-framebuffer checkpoint at a
   selected event (event 142 for the frozen 615–617 frame).
2. Re-capture frame 1 only; preserve the existing copy-source checkpoint and
   all identities/digests.
3. Replay and compare the GL/Metal checkpoint to bisect the unbounded–284 interval (midpoint 0–284).
4. Choose the next interval from the observed evidence; do not assume
   dithering or shader precision is the cause.
5. Report the new first divergent checkpoint and the next recommendation.

The opt-in `EU4_FRAME_REPLAY_PRECISE_MATH` switch remains diagnostic only; its
default is unchanged and it is not a correction.

## Review status

The three stacked PRs are open, CI-passing and awaiting fresh independent
review at their latest head SHAs. No PR is merged, no auto-merge is enabled,
and no PR is self-certified. Approval of one PR does not authorize the others.
