# M7 readiness assessment (post-PR-#206)

Baseline: `f13602e4` (PR #206 merged). Preparation branch: `m7/prep-post206`.
This is a readiness assessment, not an authorization to merge.

| Area | Required evidence | Result |
| --- | --- | --- |
| Capture | One-process, correctly ordered frame stream | **Pass.** One process (pid 84477) captured 3 sealed frames, 325 operations each; engine present indices 615/616/617 are consecutive; the sequence validator and case verifier both report `gap_free`. |
| Reproduction | Deterministic GL/GL identity | **Partial.** A GL/GL control of the captured copy/readback path gives 0/1,930,176 differing pixels on all three frames under the declared BGRA→RGBA normalization. A full-frame GL command replay is not implemented. |
| Differential validation | Genuine GL/Metal results with truthful classification | **Pass for diagnostics, not equivalence.** Three frames replay 325/325 operations with 0 failures, verified provenance and history ok. All three are `difference_with_partial_state_coverage`; `gl_dither` and `multisample` are unsupported, so renderer equivalence is not claimed. |
| Cross-frame behavior | Verified identities, resource continuity, later-frame fault detection | **Pass.** One persistent Metal session, history `ok` at sequence 3, zero missing/stale resources. A controlled fault at frame 2 event 1 is reported at frame 2 event 1; frame 1 remains unchanged. |
| Diagnostics | Actionable first-divergence localization | **Pass.** First authoritative divergence: event 284, default framebuffer, copy source; producer draw 283 / program 346; unresolved interval 13–284; next checkpoint event 148; pixel location and bounds; compact 6 KB agent package. |
| Agent integration | Working DSH → tools → Ghidra → validation workflow | **Pass with a session limitation.** Python tools, `eu4_re_query`, the DSH plugin (9/9 tests) and the native bridge test work. This session used the CLI through the DSH bash surface; the native tool list was not mounted in this session. |
| Efficiency | Measured launches, test costs, tool calls, avoidable detours | **Pass.** 2 EU4 launches (one failed `draw_cap`, one successful capture), 0 pilot launches, 7 focused test invocations, no duplicate suites at snapshot, 329 tool calls / 3,597 s elapsed, 1,030.6 s tool wait. One avoidable capture retry and one overdrawn fault-event probe are recorded. |
| Reliability | Fail-closed behavior, reproducible artifacts, bounded execution | **Pass.** Non-sealed, gap, partial-identity, missing-copy, incomplete-checkpoint and one-pixel GL-control failures all fail closed. The 1.348 GB case is explicit 2 GiB configuration; default 512 MiB yields an explicit incomplete summary. |
| CI | Focused regression coverage without heavyweight tests | **Pass for focused coverage.** 366 focused tests pass (plus the 9 plugin tests); the authentic case is outside CI. A broader full run reached an interactive live-EU4 benchmark prompt and was deliberately stopped; it is not claimed as a full-suite pass. |

## Conclusion

**READY WITH LIMITATIONS.**

M7 autonomous migration can begin within the following bounded scope:

1. Diagnostic work on frozen portable evidence, including first-divergence
   localization, checkpoint bisection and encoding/lifecycle defects.
2. Migration tasks whose acceptance does not claim full renderer equivalence,
   provided they keep `state_coverage=partial` truthful while `gl_dither`,
   `multisample` or a full-frame GL/GL control remain unresolved.
3. Any production renderer change must still pass the frozen 615–617 case and
   must not weaken pixel identity or convert an unsupported comparison into a
   match.

The following are **not** permitted by this readiness:

- declaring renderer equivalence or a zero-pixel GL/Metal result on the
  authentic frame;
- treating the transfer-only GL/GL control as a full-frame GL replay;
- claiming the event-286/284 residual is caused by `gl_dither`, `multisample`
  or fast math without the discriminating experiment;
- merging any PR without a fresh independent review and explicit authorization.

## Specific limitations

- **No full-frame GL/GL replay.** The control covers the captured copy and
  readback path, not all 325 operations.
- **No renderer equivalence.** `gl_dither` and `multisample` remain declared
  unsupported; the authentic frame is `difference_with_partial_state_coverage`.
- **Causal interval 13–284 unresolved.** The copy source diverges; the origin is
  upstream. The next checkpoint is event 148 and requires a capture-side
  feature.
- **One authentic capture.** Three frames from one process are qualified; a
  longer gameplay sequence is not.
- **Full-suite and native-tool session gate.** The full suite was attempted but
  stopped at a live benchmark prompt; a native DSH tool invocation was not
  mounted in this session. Both remain final-gate checks, not phase evidence.

## Proposed first narrowly scoped M7 production migration task

**M7-P1: implement `gl_dither` parity in the Metal replay backend for 8-bit
color writes, with the frozen 615–617 case as the qualification input.**

Scope:

- reproduce the OpenGL fixed-function dither behavior for the captured
  draw state (`dither=true`) on color attachments, using the same viewport and
  target dimensions;
- add a focused synthetic dither fixture that must be pixel-exact;
- replay the frozen authentic case and record whether the residual changes;
- stop and return to the event-148 bisection if the residual does not
  materially change; do not proceed to `multisample` in the same task.

Acceptance: focused tests pass, the frozen case still reports the truthful
classification, no threshold is weakened, and the authentic pixel statistics
are reported before/after. This task is proposed only; it is not started.

## Review status

Three stacked PRs are open and CI-passing; none is merged:

- [#207 Phase C](https://github.com/HagbardCel/eu4-mac-perf/pull/207)
- [#208 Phase D](https://github.com/HagbardCel/eu4-mac-perf/pull/208)
- [#209 evidence, pilot and readiness](https://github.com/HagbardCel/eu4-mac-perf/pull/209)

No automatic merge is enabled and no PR is self-certified. A fresh independent
review and explicit authorization are required before any merge.
