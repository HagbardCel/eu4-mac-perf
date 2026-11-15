# M7 infrastructure verification evidence

All checks are offline. No EU4 launch was made.

## Focused and dependent suites

```sh
python3 -m unittest \
  tests.test_python_runtime tests.test_agent_tools \
  tests.test_gfx_frame_compare tests.test_gfx_dual_run \
  tests.test_gfx_case tests.test_offline_campaign \
  tests.test_evidence_index tests.test_test_runner \
  tests.test_dsh_efficiency tests.test_dsh_profile \
  tests.test_dsh_native_bridge
```

**Result: 264 tests, OK** (8.5 s). The required six-module focused selection
(`test_python_runtime`, `test_agent_tools`, `test_gfx_frame_compare`,
`test_test_runner`, `test_dsh_native_bridge`, `test_evidence_index`) is
**107 tests, OK** via `automation.test_runner`. Node plugin suite:
`npm test --prefix dsh/eu4-tools-plugin` → **9/9 pass**.

Broader dependent modules (`test_gfx_metrics`, `test_gfx_replay_case`,
`test_gfx_diagnostics`, `test_gfx_session`, `test_gfx_frame_graph`,
`test_gfx_capture_native`, `test_gfx_frame_observe`,
`test_gfx_live_capture_report`, the DSH suites) were run separately; the only
errors were the pre-existing `git worktree add` failures caused by this
session's file sandbox, not by the change (they fail identically at base).

## Native DSH bridge

`tests/test_dsh_native_bridge.py` spawns the real `bridge.js`, which spawns the
real Python core:

- `eu4_replay case` on a packaged case: `ok=true`,
  `outcome={execution: ok, diagnostic: complete, verdict: different}`,
  `runtime.executable` is the absolute configured interpreter and NumPy is
  available.
- an incomplete case: `ok=false`, `status=incomplete`,
  `diagnostic=incomplete`, exit 2 — incomplete evidence is not a success.
- a configured interpreter that differs from the executing one:
  `runtime.mismatch=true`, with both paths reported;
- a virtualenv-selected interpreter keeps its entry-point path; discovery
  honors `EU4_EVIDENCE_ROOT`, explicit `evidence_root` wins, and the native and
  CLI surfaces return the same sessions.

The recorded Experiment A case was also replayed through the bridge directly:

```json
{"ok":true,"status":"ok","outcome":{"execution":"ok",
 "diagnostic":"complete","diagnostic_complete":true,
 "verdict":"different","evidence":"valid","qualified":false},
 "replay":"fail","runtime":"/opt/homebrew/.../python3.14","numpy":true}
```

The canary could not do this (`incomplete_evidence: numpy is required`).

## Full suite and base comparison

Candidate full suite (`python3.14 -m automation.test_runner run --label full`):

- **3093 tests, 11 failures, 0 errors, 26 skipped** (531 s, exit 1).
- All 11 failures are `test_trace_tr3` (QBDI release not installed).

Base `cfa62c6` (updated `main` after PR #195) was extracted with `git archive`
to `.local/base-cfa` and run through the same runner with the same full-suite
selection (`--cwd .local/base-cfa`):

- **3001 tests, 22 failures, 10 errors, 30 skipped** (479 s, exit 1).

`test_runner compare` (comparable full selection):

```
verdict     no_new_failures
comparable  true
base        32 failing ids
candidate   11 failing ids
new         0
preexisting 11
fixed       21 (base-only environment differences; see below)
```

The 11 common failures are `test_trace_tr3` and are identical on both sides.
The 21 base-only ids are environment-only: the clean base archive lacks the
gitignored `benchmark/.build` and recorded `.local` evidence used by
`test_border_loop_*`, `test_oracle_bundle`, `test_repo_inventory`,
`test_reconstruction_accept` and `test_eu4_re2_accept`; `test_gfx_session` is a
known timing-flaky process-identity assertion. They are not candidate
regressions. The comparison is therefore a genuine matching-selection result,
not a comparison of unrelated runs.

## Acceptance checks against recorded evidence

Session `20261114T181230Z-d28f6fee` (Experiment A, `live-a6`):

| Check | Result |
|---|---|
| `eu4_session list` | 1,069 B; `active=0, closed=147, stale=0, total=147`; explicit reason |
| `eu4_session discover --limit 5` | 53 campaigns indexed in 0.15 s; no manual worktree scan |
| `eu4_capture frames --frame 74` | 2 compact records (reference + candidate), 3,502 B |
| `eu4_diff_inspect` | recorded `rendering_difference` re-derived to `incomplete`; health `healthy` with `persisted_only` provenance; staticity unverified; next step is temporal, not per-draw |
| `eu4_diff_inspect --temporal` | 4 comparisons + overlap in 0.4 s: 3.77 %, 79.81 %, 6.43 %, 1.35 %, 92.0 % overlap |
| `eu4_capture health` | `healthy` with `agreement=persisted_only` — no longer contradictory with inspection |
| `eu4_replay case` (native, non-mutating) | `verdict=different`, NumPy true, no write into the case |

`live-a10` reclassifies to `incomplete`; `live-a21` stays `alignment_failure`,
with its next step pointing at caller signatures/render stage rather than a
per-draw capture.

## Reproducing

```sh
python3 -m automation.test_runner run \
  --module tests.test_python_runtime --module tests.test_agent_tools \
  --module tests.test_gfx_frame_compare --module tests.test_dsh_native_bridge
python3 -m automation.agent_tools eu4_diff_inspect \
  --run .local/infra/live-a6/session-20261114T181230Z-d28f6fee --temporal
python3 -m automation.agent_tools eu4_session discover --limit 5
```

## Limitations

- The base comparison uses a `git archive` extraction because the authoring
  sandbox cannot write `.git`; a normal sandbox can use a worktree.
- The clean base archive lacks gitignored build/evidence directories, which
  accounts for the 21 base-only environment failures. The 11 `test_trace_tr3`
  failures are identical and pre-existing on both sides (QBDI release absent).
- The recorded evidence under `.local/` is not committed; portable tests use
  synthetic frames and the sanitized DSH fixture.
