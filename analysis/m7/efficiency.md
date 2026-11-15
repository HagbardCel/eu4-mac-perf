# M7 diagnostic infrastructure — efficiency report

**Scope.** Compare the PR #195 M7.0 starter-canary workflow with the offline
workflow this PR enables. The canary numbers are measured from its actual DSH
session export; the improved numbers were measured in this checkout on the same
machine. No EU4 launch was made for either measurement.

## Method

The canary session is `session-ed49c2cd-0f72-41ff-9a5e-29edc4e9bda9`
(`~/.local/share/eu4-dsh/sessions/...`), turn 1 only: the bounded investigation
(`M7.0 — Autonomous gameplay diagnostic canary`). Turn 2/3 were the later
"complete PR #195" work and are excluded.

```sh
python3 -m automation.dsh_efficiency analyze \
  ~/.local/share/eu4-dsh/sessions/--Users-fabian-Projects-MiniProjects-eu4-mac-perf-eu4-mac-perf--/session-ed49c2cd-0f72-41ff-9a5e-29edc4e9bda9/session.v4.jsonl.zstd \
  --turn 1 --out .local/canary-efficiency.json
```

The improved measurements use the same recorded Experiment A session
(`.local/infra/live-a6/session-20261114T181230Z-d28f6fee`) and the same
checkout. Sizes are the JSON envelope written by `--out`; timings are wall clock
on this machine. These are single-run measurements, not a benchmark.

## Canary baseline (turn 1, measured)

| Metric | Value |
|---|---|
| Elapsed | 2,751.7 s (45.9 min) |
| Tool calls | 173 (106 `bash`) |
| Tool failures | 1 (`edit`) — 0.58 % |
| Tool wait | 1,238.9 s (45.0 % of elapsed) |
| — of which `job_output` (waiting on background jobs) | 1,041.2 s |
| — of which `bash` | 195.5 s |
| Test invocations | 15 across 7 distinct signatures |
| Full-suite invocations | 4 (three long checkout runs: 544.7 s, 536.3 s, and a killed 514.1 s run; one more in a base worktree) |
| Duplicate test signatures | full suite ×4; a 5-module base set ×3; three more sets ×2 |
| Long jobs (>20 s) | 4 (544.7 s, 536.3 s, 514.1 s, 66.3 s) |
| Tool-result characters | 441,933 (max single result 49,930; 4 oversized, 2 spilled/truncated) |
| Model tokens | 439,719 input + 21,676,708 cache read + 96,078 output = 22,212,505 |
| Cache-read share | 97.59 % |
| EU4 launches | 0 |
| Human interventions | 1 user message in the turn; policy change before turn 1 |
| Goal | `create` → `complete`, outcome `completed` |

The canary's own report counted 178 tool calls / 111 shell calls for the
investigation; the analyzer's turn-window boundary (`turn/start` to `turn/end`)
gives 173 / 106. The difference is five calls around the boundary and does not
change any conclusion.

## Improved offline workflow (measured)

| Step | Command | Result |
|---|---|---|
| Session preflight | `eu4_session list` | 1,069 B; 0 active, explicit `closed=147, stale=0, total=147` |
| Historical listing | `eu4_session list --include-history --limit 5` | 3,836 B; bounded page + `next_offset` |
| Evidence discovery | `eu4_session discover --limit 5` | 8,801 B; 53 campaigns indexed in 0.15 s, no manual worktree search |
| Exact frames | `eu4_capture frames --run <a6> --frame 74` | 3,502 B / 2 compact records (was 177 records, ~90 KB) |
| Session inspection | `eu4_diff_inspect --run <a6>` | 9,862 B; recorded `rendering_difference` re-derived to `incomplete`, health + provenance, staticity, missing evidence, category-specific next step |
| Temporal diagnostics | `eu4_diff_inspect --run <a6> --temporal --reference-frame 74 --candidate-frame 74` | 25,240 B / 0.4 s; 4 pair comparisons + mask overlap, no custom script |
| Native replay | `eu4_replay case` through the DSH bridge | completed with the selected interpreter environment and imported NumPy (canary: `incomplete_evidence: numpy is required`) |
| Focused tests | `test_runner run --module tests.test_agent_tools` | ≈0.7 s for 40 tests, compact summary, complete log on disk |
| Focused suite | 11 modules (227 tests) | 6.1 s |
| Duplicate suite | second identical invocation | returned the running record immediately; no second process |
| Efficiency analysis | `dsh_efficiency analyze ... --turn 1` | 0.09 s |

Temporal output on the recorded session reproduces the canary's decisive
measurements: reference 74→75 3.77 %, reference 75→76 79.81 %, candidate
74→75 6.43 %, cross-process 1.35 %, and 92.0 % of cross-process differing
pixels also change in reference 74→75.

## What the changes remove

| Canary inefficiency | This PR |
|---|---|
| Native replay could not find NumPy | one absolute interpreter, resolved by launcher and preflight, checked for NumPy, reported in every envelope; end-to-end Node→Python bridge test |
| `ok=true` on incomplete operations | `eu4-agent-tool/v2` separates execution, diagnostic completion, verdict, evidence validity and qualification; missing NumPy is `unavailable`, not success |
| 100+ stale session records (~25 KB) | active-only compact listing by default with explicit counts; history opt-in and bounded |
| Inspecting a campaign root raised `FileNotFoundError` | campaign roots are accepted and listed with per-session identities/outcomes; never auto-selected |
| `eu4_capture frames` ignored the frame filter and capped silently | exact frame/role filtering, bounded neighbours, explicit pagination, compact records, `--full` escape hatch |
| `eu4_capture health` said unknown while inspection said healthy | one reconciled health view with source paths, agreement and “unknown is not healthy” |
| No temporal analysis; private NumPy script | reusable `gfx_frame_compare.compare_temporal` + `eu4_diff_inspect --temporal`, bounded, artifacts opt-in |
| Full suite launched repeatedly | focused `test_runner` with pending-duplicate detection, comparable-only base comparison and logs |
| Evidence only visible from one worktree | read-only `EU4_EVIDENCE_ROOT` discovery with original paths/hashes |
| No way to quantify the wasted work | `dsh_efficiency` one-shot analysis (this report) |

## Limitations

- The canary baseline is one session on one machine; it is a case study, not a
  general benchmark.
- Tool wait is an attribution of call→result gaps; parallel calls can overlap, so
  it is not a serial wall-clock decomposition.
- The improved workflow still spends most model time reading and reasoning; the
  reduction is in redundant tool calls, output size and repeated suite runs, not
  in model latency.
- The full-suite run still takes ~8–9 minutes and has pre-existing failures in
  modules unrelated to this PR. The focused workflow narrows the loop; it does
  not make the suite fast.
- The DSH export is local and not committed. The committed fixture is synthetic
  and sanitized; the analyzer reproduces the canary numbers only when pointed at
  the local export.
- No EU4 launch was made, so no claim is made about live diagnostic latency.

## Reproduction

```sh
python3 -m automation.dsh_efficiency analyze <session.jsonl.zstd> --turn 1
python3 -m automation.test_runner run --module tests.test_dsh_efficiency
python3 -m automation.agent_tools eu4_session discover --limit 5
python3 -m automation.agent_tools eu4_diff_inspect \
  --run .local/infra/live-a6/session-20261114T181230Z-d28f6fee \
  --temporal --reference-frame 74 --candidate-frame 74
```

See [the canary report](starter-canary.md) for the original investigation,
[the verification record](infrastructure-verification.md) for the full-suite and
bridge evidence, and [the M7 follow-up plan](m7-instrumentation-followup.md) for
the remaining native instrumentation work.
