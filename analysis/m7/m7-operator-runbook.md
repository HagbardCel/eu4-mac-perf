# M7 operator runbook — autonomous campaigns

Repository: `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf`
DSH home: `/Users/fabian/.local/share/eu4-dsh`
Python: `/opt/homebrew/opt/python@3.14/bin/python3.14`

The commands below are fish-compatible. Do not run the `/goal` commands from an
agent session; the operator starts DSH and pastes the goal manually.

## A. Offline native DSH qualification

### Fixed identities

| Item | Absolute path |
| --- | --- |
| Sidecar | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/campaigns/m7-autonomy-offline.json` |
| Budget state / claim ledger | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/state-operator-offline.json` |
| Candidate worktree / workspace | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/dsh/m7-autonomy-offline/workspace` |
| Trusted reference | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/trusted/offline-b` |
| Checker outputs | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/run/operator-1` |
| Campaign source | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/campaigns/eu4-m7-autonomy-offline/campaign.json` |

The candidate worktree is pinned to the seeded defective commit
`977330aed582d47d09df0777f2e00a1560ff1809`. The trusted reference is the
derived offline fixture (original events 10, 15, 17), exact scope 0..2.

### Fresh budget at launch

The prepared budget file is deliberately not pre-aged. Initialize the fresh
operator state immediately before launch:

```fish
/opt/homebrew/opt/python@3.14/bin/python3.14 -m automation.campaign_guard ensure --state /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/state-operator-offline.json --budget eu4_launches=0 --budget sealed_frames=0 --budget shadow_executions=0 --budget replays=4 --budget corrections=1 --budget wall_clock_seconds=1800
```

The `ensure` command is idempotent for an unused state: it refreshes the
wall-clock window and refuses a state with consumed budget or reservations.
If the code head changed after the last anchor, refresh the anchor explicitly:

```fish
/opt/homebrew/opt/python@3.14/bin/python3.14 -m automation.dsh_bridge prepare --create-anchor --reanchor --sidecar /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/campaigns/m7-autonomy-offline.json
```

### Launch

```fish
cd /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf
env EU4_OFFLINE_ONLY=1 EU4_DSH_PYTHON=/opt/homebrew/opt/python@3.14/bin/python3.14 EU4_CAMPAIGN_STATE=/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/state-operator-offline.json EU4_M7_AUTONOMY_RUN_DIR=/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/run/operator-1 /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/scripts/run.sh --campaign /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/campaigns/m7-autonomy-offline.json --campaign-state /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/state-operator-offline.json
```

Open the DSH Web session in the registered workspace:

`/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/dsh/m7-autonomy-offline/workspace`

### Goal to paste

```text
You are validating the M7 autonomous OpenGL-to-Metal reconstruction workflow. The candidate renderer in this worktree contains a deliberately introduced rendering discrepancy. Diagnose it using the captured reference evidence, identify the smallest causal renderer correction, implement it, and submit the result to the independent checker at campaigns/eu4-m7-autonomy-offline/accept.py. Work autonomously across native DSH goal rounds. Modify only benchmark/**, tests/**, analysis/m7/**, progress.md or .local/**. Never modify original reference evidence, the checker, campaign limits or receipts. Use focused tests and trusted replay executions. If the checker rejects the correction, use its diagnostics to revise your hypothesis within the budget. Do not claim success from your own screenshots or reports; success requires the independent checker to pass on actual GPU output from your modified source. Do not merge. Stop with explicit evidence if the correction cannot be justified or the budget is exhausted.
```

### Expected outcome

- The seeded defect makes the first event fail (checker `fail`, first divergence
  event 0) before correction.
- A correction removes the deliberate clear-colour shift.
- The checker rebuilds the candidate, executes it against the trusted reference
  and returns `pass` with scope 0..2.
- At least two goal rounds are expected: diagnosis, then correction/validation.
- The checker must not pass candidate-authored screenshots or metadata.

### Inspect

```fish
python3.14 -m automation.campaign_guard status --state /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/state-operator-offline.json
ls -la /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/run/operator-1
cat /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/run/operator-1/exec-*/metal/trusted-execution.json
ls -lt /Users/fabian/.local/share/eu4-dsh/logs | head
ls -lt /Users/fabian/.local/share/eu4-dsh/sessions | head
```

Checker output in the DSH transcript is the authoritative validator response.
`trusted-execution.json` must name the execution directory, candidate
revision, source digest, compiled host SHA-256 and output digests.  Every
validation creates a new `exec-*` child under the checker run root; previous
attempts are retained for diagnosis.

### Stop safely

Close the DSH session or press Ctrl-C in the launcher terminal. To record an
explicit stop:

```fish
python3.14 -m automation.campaign_guard stop --state /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/state-operator-offline.json --reason blocked
```

No EU4 launch is possible in this campaign (`EU4_OFFLINE_ONLY=1`, launch budget
0).

### Pass criterion

The offline autonomy integration test passed when all of the following hold:

1. The DSH session ran at least two native goal rounds.
2. The candidate renderer source changed from the seeded defect revision.
3. The independent checker returned `pass` with `EXACT_PREFIX` scope 0..2.
4. `trusted-execution.json` binds the new source digest and compiled host.
5. No reference, checker, campaign or receipt file was modified.
6. Campaign status shows the replay/validation charge and no unauthorized
   action.

## B. Live M7 event-20 campaign

**DO NOT RUN UNTIL THE OFFLINE QUALIFICATION PASSES AND THE PR HEAD IS
INDEPENDENTLY APPROVED.**

### Fixed identities

| Item | Absolute path |
| --- | --- |
| Sidecar | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/campaigns/m7-event20.json` (set `EU4_M7_EVENT20_TRUSTED_ROOT` to this run's trusted seal) |
| Budget state / claim ledger | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/state-operator-live.json` |
| Launch counter | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/dsh/m7-event20-final2/run/live-launch.json` |
| Candidate worktree / workspace | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/dsh/m7-event20-final2/workspace` |
| Capture evidence | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/evidence` |
| Trusted reference | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/trusted/seal` |
| Checker outputs | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/checker-1` |
| Campaign source | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/campaigns/eu4-m7-event20/campaign.json` |
| Candidate baseline | `6619f13a753bb2183b14655392dacc4436a34435` (sidecar and campaign pin agree) |
| Preserved trusted reference | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20/trusted-copy/47590918` (read-only; the default sidecar root) |

Budgets: **1 EU4 launch, 1 sealed frame, 1 shadow execution, 2 replays,
1 correction evaluation, 1800 s, 0 automatic merges.** Do not reset a consumed
budget.

### Pre-launch checks

```fish
cd /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf
pgrep -fl 'eu4.app'
shasum -a 256 "/Users/fabian/Documents/Paradox Interactive/Europa Universalis IV GOG/settings.txt"
python3.14 -c "from automation import gfx_display; d=gfx_display.find_display('EU4 Test'); print(d); raise SystemExit(0 if d is not None else 1)"
env EU4_DSH_PYTHON=/opt/homebrew/opt/python@3.14/bin/python3.14 /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/scripts/preflight.sh
python3.14 -m automation.campaign_guard status --state /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/state-operator-live.json
```

Expected settings SHA-256 before capture:
`62b4902a3a999ad3e623dd92800c3406359b44e60497491393e6b191836dd496`.
`pgrep` must print nothing. The BetterDisplay check must exit 0. The launch
counter must show `used=0`, `limit=1`.

### Fresh budget at launch

Initialize the fresh live state immediately before capture. The launch counter
already exists with `used=0, limit=1`.

```fish
/opt/homebrew/opt/python@3.14/bin/python3.14 -m automation.campaign_guard ensure --state /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/state-operator-live.json --budget eu4_launches=1 --budget sealed_frames=1 --budget shadow_executions=1 --budget replays=2 --budget corrections=1 --budget wall_clock_seconds=1800
```

`ensure` is idempotent while the state is unused. If the code head changed
after the last anchor, refresh it explicitly:

```fish
/opt/homebrew/opt/python@3.14/bin/python3.14 -m automation.dsh_bridge prepare --create-anchor --reanchor --sidecar /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/campaigns/m7-event20.json
```

### Capture one sealed frame (consumes the launch)

```fish
cd /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf
env EU4_CAMPAIGN_STATE=/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/state-operator-live.json EU4_DSH_PYTHON=/opt/homebrew/opt/python@3.14/bin/python3.14 /opt/homebrew/opt/python@3.14/bin/python3.14 -m automation.m6_frame capture --output /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/evidence --campaign-run-root /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/dsh/m7-event20-final2/run --callers 0x10d52fc,0x1486ad9 --min-draws 8 --max-draws 512 --duration 120 --timeout 300 --prefix-end 20 --prefix-max-bytes 700000000 --prefix-max-ns 10000000000 --seal --shadow --background --shadow-timeout-ms 360000 --shadow-wait-ms 360000 --seal-timeout-ms 600000
```

The harness must terminate the owned EU4 process and restore `settings.txt`
byte-for-byte. Verify the post-capture SHA-256 is still
`62b4902a3a999ad3e623dd92800c3406359b44e60497491393e6b191836dd496` and that
the seal is complete.

### Admit the trusted reference

```fish
python3.14 -m automation.gfx_prefix_check prepare --frame /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/evidence/frame --destination /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/trusted/seal --note "live M7 event-20 seal"
python3.14 -m automation.gfx_prefix_check verify --root /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/trusted/seal --frame /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/trusted/seal/frame
```

The copy must be outside the candidate workspace and the verify command must
report its root SHA-256 and file count.  `prepare` refuses a destination that
overlaps the frame and requires `--replace` for an existing evidence root; the
previous root is retained as a dated sibling.  The read-only flag is POSIX
permission hardening, not a read-only filesystem guarantee.

### Launch DSH and paste the goal

```fish
cd /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf
env EU4_M7_EVENT20_TRUSTED_ROOT=/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/trusted/seal EU4_DSH_PYTHON=/opt/homebrew/opt/python@3.14/bin/python3.14 EU4_CAMPAIGN_STATE=/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/state-operator-live.json EU4_M7_CHECKER_RUN_DIR=/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/checker-1 /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/scripts/run.sh --campaign /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/campaigns/m7-event20.json --campaign-state /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/state-operator-live.json
```

Workspace expected in DSH:

`/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/dsh/m7-event20-final2/workspace`

Goal to paste:

```text
You are executing the M7 event-20 exact-prefix campaign against the sealed original-EU4-OpenGL reference at /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/trusted/seal. The independent checker is campaigns/eu4-m7-event20/accept.py. Work autonomously across native DSH goal rounds. Diagnose the earliest divergence using the sealed evidence, propose at most three bounded hypotheses, modify the smallest candidate renderer component, run focused tests, and submit the result to the trusted checker. The checker builds and executes your candidate renderer against the immutable reference; candidate-authored screenshots or reports are not evidence. Compare every event 0 through 20, including the R32F payload at event 11. Stop at the first uncorrected divergence. Do not modify reference evidence, the checker, campaign limits or receipts. Do not merge. If the correction cannot be justified or the budget is exhausted, stop with requires_additional_evidence and the exact missing observation.
```

### Expected outcome and first-divergence handling

- The checker's first result may diverge at event 1 (the prior seal's first
  divergence); it is a prior observation, not a guarantee.
- The agent may use offline experiments and the trusted checker within the
  two-replay budget; no extra EU4 launch.
- If a correction is proposed, the checker must show an independently verified
  improvement, not a candidate-authored claim.
- If the first divergence is corrected but later events still diverge, stop at
  the next earliest mismatch and report the exact verified prefix.
- If the budget cannot cover the remaining corrections, stop with the verified
  progress and request a separately authorized continuation.

### Acceptance

`EVENT20_EXACT` is allowed only when the independent checker returns it for the
one sealed frame, every event 0..20 is valid and compared, no operation is
excluded, R32F is bitwise exact, resource/event identities agree, candidate
source/build identities are verified, and every applicable payload matches
exactly. A green `M7_AUTONOMOUS_READY` gate does not imply this.

### Evidence and stop

- Campaign status:
  `python3.14 -m automation.campaign_guard status --state /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/state-operator-live.json`
- Checker receipt: `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/checker-1/exec-*/metal/trusted-execution.json`
- DSH logs/sessions: `/Users/fabian/.local/share/eu4-dsh/logs`, `/Users/fabian/.local/share/eu4-dsh/sessions`
- Explicit stop:
  `python3.14 -m automation.campaign_guard stop --state /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/event20-final2/state-operator-live.json --reason blocked`
- Do not merge. Request independent review of the exact head.
