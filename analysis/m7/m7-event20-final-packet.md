# M7 final readiness packet — PR #280

Date: 21 November 2026. Review baseline: `35446d89`; this packet covers the
candidate-execution remediation that follows it.

## 1. Final PR head and review decision

- Final PR head: `4b34bbd5f1b4a58e41d666a932b7482677cfc545`
- Candidate execution code head: `6619f13a753bb2183b14655392dacc4436a34435`
- Canonical campaign pin: `09e03ec7` (configuration-only commit after the code
  head; the sidecar/source workspace point at `6619f13a`).
- Independent review of this exact head: **not yet obtained**.
- Merge recommendation: **do not merge**. Request review of the candidate
  execution trust boundary, reference isolation, accounting, alpha/format scope
  and the adversarial regressions.

## 2. Trusted candidate execution design

The independent checker (`automation/gfx_prefix_check.py`) now:

1. Loads the canonical sidecar and verifies the candidate worktree, HEAD and
   allowed/forbidden path policy (`verify_candidate`).
2. Copies the candidate replay-host dependency closure into an isolated
   snapshot (`replay_host_dependencies.snapshot_sources`).
3. Builds the replay host from that snapshot with the trusted toolchain and
   documented flags (`build_snapshot_host`).
4. Binds the candidate revision, changed paths, source digest, per-file source
   hashes, compiler identity, flags, build fingerprint and host SHA-256.
5. Executes the candidate host against the verified immutable copied reference
   snapshot and a request derived by trusted infrastructure.
6. Rehashes the host, candidate source closure, replay result, receipt and
   every GPU output after execution; any replacement is rejected.
7. Recomputes all comparisons from those outputs.

A/B proof (no EU4 launch):

| Candidate | Source digest | Host SHA-256 | Independent result |
| --- | --- | --- | --- |
| Baseline | `255a5b7e…` | `190fde92…` | first divergence event 1; 23 px / max 1 |
| Controlled defect (red clear +0.01) | `492e6654…` | `533f79c6…` | first divergence event 0; 1,930,176 px / max 3 |
| Defect reverted | `255a5b7e…` | `190fde92…` | first divergence event 1; 23 px / max 1 |
| Same candidate on immutable copy | `255a5b7e…` | `190fde92…` | first divergence event 1; 23 px / max 1 |

The unchanged candidate reproduces its baseline; a candidate-only source change
changes the independently executed GPU result. Candidate-authored screenshots,
`replay.json`, receipts or executable bytes are never authority.

## 3. Native DSH multi-round integration

**Not demonstrated in this round.** The canonical campaign and a fresh isolated
worktree are prepared and anchored, but a genuine multi-round goal with a
controlled correction and checker acceptance was not executed. The previous
controlled two-round evidence predates the corrected candidate-execution path.
This is the blocking reason `M7_AUTONOMOUS_READY` is not claimed.

## 4. Focused and full tests

- Required focused command: **passed**.
- `test_profiles verify --profile offline`: **passed**.
- Final autonomy gate `20261121T172542Z-17dd1d11` at `4b34bbd5`:
  **4,445 ran / 4,409 executed / 36 skipped / 0 failures / 0 errors**,
  1,079.9 s. `test_profiles verify --profile offline` passed before the gate.

## 5. Budget-accounting verification

`campaign_guard.attempt("replays")` wraps the trusted translation/build/GPU
execution. Tests verify: one actual replay consumes one allowance; a failed
attempt remains charged; nested checker/replay calls join one reservation;
exhaustion is refused before build or GPU work; validation attempts and replays
remain separate counters.

## 6. Existing event-1 baseline/candidate comparison

Canonical `accept.py` on the final candidate `6619f13a` and the immutable copied
reference returns `fail`:

- Event 0: exact on applicable RGB channels.
- Event 1: 23 differing RGB pixels, maximum byte-channel difference 1.
- Events 10 and 12–17: exact in RGBA scope.
- Events 11: GL_R32F, unobserved in the old seal.
- Events 18–20: divergent (359/12, 1,186/12, 4,781/22).
- Verified prefix: `[0, 0]`.

## 7. New live pilot

**Not executed.** No EU4 launch was authorized in this round. A fresh capture is
prepared in `m7-event20-live-plan.md`; it remains blocked on native multi-round
integration and independent review. The old seal cannot establish event-20
equivalence because event 11 has no original R32F observation.

## 8. Verified prefix

`[0, 0]` for this seal. Event 1 is the first authoritative divergence.

## 9. Remaining differences and missing evidence

- Event 1: unresolved 1-LSB RGB difference on the DXT1_ALPHA offscreen target.
  Decoded-texture fallback is worse (17,446 px). No discriminating evidence yet
  for BC1 decode/filter rounding versus another state difference.
- Event 11: R32F support implemented; the old seal lacks the original R32F
  readback. A new capture is required.
- Events 18–20: later default-framebuffer differences, preserved but not
  diagnosed because the campaign stops at event 1.
- Native DSH multi-round integration of the corrected checker is unproven.

## 10. Decisions

- **M7_AUTONOMOUS_READY: NO** — candidate execution, isolation, accounting and
  tests pass; native multi-round integration is missing.
- **EVENT20_EXACT: NO** — event 1 diverges; event 11 is unobserved in this
  seal.
- These are separate gates. Infrastructure correctness does not imply pixel
  equivalence.
