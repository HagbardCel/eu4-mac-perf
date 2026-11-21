# M7 autonomous final report — READY_FOR_OPERATOR_LAUNCH

Date: 21 November 2026. This report covers the preparatory work for the
operator-started native DSH `/goal` campaigns. No `/goal` was started, no
headless agent was substituted and no EU4 launch was made.

## 1. Final PR head and review status

- PR: https://github.com/HagbardCel/eu4-mac-perf/pull/280
- Last inspected head: `c181d0fb91aab365a86f975363d6b93aff510676`.
- Final head for operator handoff: `77eba58fe4ad651a8e586192d20b6d80894efd6b`.
- Code head: `3a2ed8f828005706a91f55df9a33a202583c1fd0`; the final commit adds
  the offline/live campaign configuration, runbook and this report.
- Independent review of the exact final head: **not obtained**.
- Merge: **NO** until independent review completes.

## 2. Correctness fixes applied

- **R32F acceptance:** the final per-event checker now accepts
  `reference_pixel_format="R32F"` only when the sealed attachment identity is
  `GL_R32F`, the comparison scope is `R32F`, alpha is not applicable, the
  candidate format agrees and no RGB/RGBA normalization is applied. R32F is
  compared bitwise; differences report `max_ulp_error`, not RGBA channel error.
- **End-to-end R32F fixture:** a complete event-0..20 fixture with an
  authenticated R32F event 11 qualifies exactly; a one-bit R32F change is
  rejected at event 11. No excluded event can produce `EVENT20_EXACT`.
- **Scoped exactness:** a complete exact prefix shorter than event 20 now
  reports `EXACT_PREFIX`, not `EVENT20_EXACT`.
- **Trusted candidate execution:** the checker verifies the candidate
  worktree/revision/policy, builds the actual candidate renderer from a source
  snapshot, executes it against an immutable copied reference and binds
  source/build/host/output digests. Candidate outputs are not authority.
- **Explicit reference selection:** the trusted root comes from campaign
  configuration; missing, ambiguous, nonexistent and candidate-internal roots
  are rejected.
- **Trusted evidence admission:** a frame is validated with `load_seal` and
  `load_trace(verify_blobs=True)` before it can be copied into the trusted root;
  `prepare`/`verify` CLI commands are available.

## 3. Tests and CI

- Focused pre-operator suite (`tests.test_m7_event20_prefix`,
  `test_gfx_frame_graph`, `test_gfx_independent_replay`, `test_dsh_bridge`,
  `test_dsh_campaign`, `test_campaign_guard`, `test_dsh_profile`): **passed**.
- Full offline gate `20261121T185012Z-7467133f` at code head `3a2ed8f8`:
  **4,448 ran / 4,412 executed / 36 skipped / 0 failures / 0 errors**,
  744.0 s. `test_profiles verify --profile offline` passed before the gate.
- Portable CI: **pass** at `c181d0fb`; the new final head must pass CI before
  merge.

## 4. DSH preflight

- Installed DSH `0.2.2-rc.5`, Node `v26.13.1`, pnpm `12.21.0`.
- `dsh/scripts/preflight.sh` **passes** with
  `EU4_DSH_PYTHON=/opt/homebrew/opt/python@3.14/bin/python3.14`.
- The profile mounts the goal service, goal-round driver, tool-goal and
  command-goal plugins; the local `eu4-goal` and `eu4-tools` plugins resolve
  from this checkout.
- Provider/model: `opencode-go` / `deepseek-v4.1-flash`; the Web profile reads
  the stored provider credential. The model path was not exercised.
- The canonical/live candidate worktree is registered through
  `dsh_bridge prepare --create-anchor`.

## 5. Offline autonomous qualification campaign

| Item | Value |
| --- | --- |
| Campaign id | `m7-autonomy-offline-20261121` |
| Sidecar | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/campaigns/m7-autonomy-offline.json` |
| Budget state / claim ledger | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/state-operator.json` |
| Candidate workspace | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/dsh/m7-autonomy-offline/workspace` |
| Seeded defect revision | `977330aed582d47d09df0777f2e00a1560ff1809` |
| Trusted reference | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/trusted/offline-b` |
| Trusted root SHA-256 | `f40a21e52522a11b9aa467109c15258ef0ae44ade1209a579f529558d4ffe73c` |
| Fixture | derived original events 10, 15, 17; exact scope 0..2 |
| Budgets | replays 4, corrections 1, wall 1800 s, no EU4 launch; `max_validations` 4 |
| Checker | `campaigns/eu4-m7-autonomy-offline/accept.py` |

Deterministic proof, without running the model:

| Candidate | Source digest | Host SHA-256 | Checker |
| --- | --- | --- | --- |
| Seeded defect | `a9385edb…` | `533f79c6…` | `fail`, first divergence event 0 |
| Correction applied | `255a5b7e…` | `190fde92…` | `pass`, `EXACT_PREFIX` 0..2 |
| Defect restored for the operator | `a9385edb…` | — | worktree clean at defect revision |

This proves the fixture is attainable and the independent checker responds to
actual candidate GPU output. The model-driven multi-round run remains the
operator's step.

## 6. Live M7 event-20 campaign

| Item | Value |
| --- | --- |
| Campaign id | `m7-event20-live-20261121` |
| Sidecar | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/campaigns/m7-event20-live-20261121.json` |
| Budget state / claim ledger | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-live-20261121/state-operator.json` |
| Launch counter | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/dsh/m7-event20-live-20261121/run/live-launch.json` (used 0, limit 1) |
| Candidate workspace | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/dsh/m7-event20-live-20261121/workspace` |
| Code revision | `3a2ed8f828005706a91f55df9a33a202583c1fd0` |
| Capture evidence | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-live-20261121/evidence` |
| Trusted reference (after capture) | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-live-20261121/trusted/seal` |
| Budgets | 1 launch, 1 seal, 1 shadow, 2 replays, 1 correction, 1800 s, 0 automatic merges |

The live launch is **blocked in this environment**: the BetterDisplay virtual
screen is not exposed (`gfx_display.displays()` is empty and
`find_display("EU4 Test")` returns None). `settings.txt` is present at SHA-256
`62b4902a3a999ad3e623dd92800c3406359b44e60497491393e6b191836dd496`.
The live campaign must not start until the BetterDisplay `EU4 Test` screen is
visible in a GUI session and the pre-launch checks in the runbook pass.

## 7. Current decisions

- **Ready for a manually initiated offline `/goal`: YES**, once the operator
  opens DSH with the prepared sidecar/state and pastes the offline goal.
- **Ready for the live `/goal`: NO.** Offline qualification, independent
  review, approved final head and a visible BetterDisplay screen are required.
- **M7_AUTONOMOUS_READY: NO** until the offline native multi-round run returns
  an independent checker pass.
- **EVENT20_EXACT: NO**.
- **Merge: NO.**

## 8. OPERATOR ACTION REQUIRED

The exact commands and goal texts are in
`analysis/m7/m7-operator-runbook.md`. Offline launch:

```fish
cd /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf
env EU4_OFFLINE_ONLY=1 EU4_DSH_PYTHON=/opt/homebrew/opt/python@3.14/bin/python3.14 EU4_CAMPAIGN_STATE=/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/state-operator.json EU4_M7_AUTONOMY_RUN_DIR=/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/run/operator-1 /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/scripts/run.sh --campaign /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/campaigns/m7-autonomy-offline.json --campaign-state /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/state-operator.json
```

Then open the DSH session in
`/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/dsh/m7-autonomy-offline/workspace`
and paste the offline goal from the runbook.

**DO NOT RUN THE LIVE CAMPAIGN UNTIL THE OFFLINE QUALIFICATION AND INDEPENDENT
REVIEW PASS.** Its command and goal are in the runbook, section B.

No `/goal`, headless agent substitute, or EU4 launch was executed in this
round.
