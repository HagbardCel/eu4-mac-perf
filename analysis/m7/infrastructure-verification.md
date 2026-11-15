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

**Result: 227 tests, OK** (6.1 s). Node plugin suite:
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
  `runtime.mismatch=true`, with both paths reported.

The recorded Experiment A case was also replayed through the bridge directly:

```json
{"ok":true,"status":"ok","outcome":{"execution":"ok",
 "diagnostic":"complete","diagnostic_complete":true,
 "verdict":"different","evidence":"valid","qualified":false},
 "replay":"fail","runtime":"/opt/homebrew/.../python3.14","numpy":true}
```

The canary could not do this (`incomplete_evidence: numpy is required`).

## Full suite and base comparison

One full suite was completed after a first run was intentionally stopped
mid-flight:

- `3040` tests, `26` skipped, `14` errors, `15` failures, 473 s.
- The failing/erroring modules were `test_dsh_bridge`, `test_dsh_review`,
  `test_eu4_re2_accept`, `test_reconstruction_accept`,
  `test_automation_handoff`, `test_gfx_session`,
  `test_index_buffer_campaign`, `test_oracle_shadow_acceptance` and
  `test_trace_tr3`.

Those modules were extracted from base `5a0ac03` (`git archive`) and run in
`.local/base-check`; the candidate was run with the same focused selection.
`test_runner compare` reports:

```
verdict     no_new_failures
base        32 failing ids
candidate   28 failing ids
new         0
preexisting 28
fixed       4 (test_eu4_re2_accept environment-only)
```

`test_gfx_session` failed once inside the full suite but passes standalone
(14 tests, OK); it is an isolation/flakiness issue, not a regression. The
sandbox-induced `git worktree add` errors (2 in `test_dsh_bridge`, 10 in
`test_dsh_review`) are identical at base.

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

- The base comparison extracts the base commit with `git archive` because this
  session cannot write the repository's `.git` directory; a sandbox with Git
  write access can use a real worktree instead.
- The full suite's `test_dsh_review`/`test_dsh_bridge` worktree tests cannot run
  under the current file sandbox. They are excluded from the "new failure"
  claim only by proving the identical failure set at base.
- The recorded evidence under `.local/` is not committed; the portable tests use
  synthetic frames and the sanitized DSH fixture.
