# M7 operator runbook — autonomous campaigns

Repository: `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf`
DSH home: `/Users/fabian/.local/share/eu4-dsh`
Python: `/opt/homebrew/opt/python@3.14/bin/python3.14` (must import NumPy)

The commands below are fish-compatible. Do not run the `/goal` commands from an
agent session; the operator starts DSH and pastes the goal manually.

Governance: there are **no replay, correction, validation or goal-round
quotas**. A campaign has one safety mode, one-use operator launch
authorizations for live capture, and one operator execution deadline. See
[governance](../../docs/governance.md). Historical run transcripts and
old objective texts elsewhere in `analysis/m7/` remain as recorded evidence; they
do not describe the current policy.

## A. Offline autonomous campaign

### Fixed identities

| Item | Absolute path |
| --- | --- |
| Tracked sidecar | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/dsh/campaigns/m7-autonomy-offline.json` |
| Campaign state (new, fresh) | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/state-operator-offline.json` |
| Campaign run root (sidecar `run_root`) | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/dsh/m7-autonomy-offline/run` |
| Candidate workspace | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/dsh/m7-autonomy-offline/workspace` |
| Trusted reference | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/trusted/offline-b` |
| Checker outputs | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/m7/autonomy-offline/run/operator-1` |
| Campaign source | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/campaigns/eu4-m7-autonomy-offline/campaign.json` |
| Objective | `/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/campaigns/eu4-m7-autonomy-offline/objective.md` |

The candidate worktree is pinned to the seeded defective commit
`977330aed582d47d09df0777f2e00a1560ff1809`. The trusted reference is the
derived offline fixture (original events 10, 15, 17), exact scope 0..2.
`state-operator-offline.json` from the previous campaign is a historical v1
state; do not reuse it. The campaign state and run root are both fresh for a
new launch; pick a new suffix instead of deleting old evidence.

### Preflight (read-only; run before launch)

```fish
set -l REPO /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf
set -l PY /opt/homebrew/opt/python@3.14/bin/python3.14
set -l STATE $REPO/.local/m7/autonomy-offline/state-operator-offline.json
set -l SIDECAR $REPO/dsh/campaigns/m7-autonomy-offline.json
set -l RUN $REPO/.local/dsh/m7-autonomy-offline/run
set -l CHECKER $REPO/.local/m7/autonomy-offline/run/operator-1
set -l WS $REPO/.local/dsh/m7-autonomy-offline/workspace
set -l TRUSTED $REPO/.local/m7/autonomy-offline/trusted/offline-b
cd $REPO

# 0. Checkout and DSH toolchain (must print ok:true, dsh 0.2.2-rc.5).
git rev-parse HEAD
git status --short
env EU4_DSH_PYTHON=$PY ./dsh/scripts/preflight.sh

# 1. No stale host-wide campaign marker may be active.
$PY -c 'from automation import campaign_guard; g = campaign_guard.from_env(); print("active:", None if g is None else g.path)'

# 2. Fresh state and run root: both must be absent for a new campaign.
test -f $STATE; and echo "STATE EXISTS - pick a new suffix before launch"; or echo "state is fresh"
test -d $CHECKER; and echo "CHECKER ROOT EXISTS - pick a new suffix before launch"; or echo "checker root is fresh"

# 3. Seed defect and clean candidate worktree.
git -C $WS rev-parse HEAD
git -C $WS status --short

# 4. The trusted reference is intact and the fixture can qualify the prefix.
#    preflight_fixture() verifies the trusted root itself; one command is enough.
$PY -c "from automation import gfx_prefix_check as g; import json; r = g.preflight_fixture('$TRUSTED/frame', require_end=2, trusted_root='$TRUSTED'); print(json.dumps(r['problems']))"

# 5. Checker identity and candidate restrictions.
shasum -a 256 campaigns/eu4-m7-autonomy-offline/accept.py
$PY -c 'import json; d = json.load(open("dsh/campaigns/m7-autonomy-offline.json")); print(d["candidate"]["policy"]); print(d["limits"])'

# 6. The objective no longer contains superseded governance instructions.
grep -n -i "budget\|max_validations\|max_rounds\|quota" campaigns/eu4-m7-autonomy-offline/objective.md
```

Expected preflight: `git rev-parse` prints the reviewed head (a commit on this
branch), the worktree is the pinned seed commit and clean, `preflight.sh` is
`ok:true` with `0.2.2-rc.5`, the marker is `None`, the trusted verify returns a
root digest, and the objective grep prints the explicit "no ... quota"
sentence only.

### Launch (operator action; starts the deadline)

```fish
set -l REPO /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf
set -l PY /opt/homebrew/opt/python@3.14/bin/python3.14
set -l STATE $REPO/.local/m7/autonomy-offline/state-operator-offline.json
set -l SIDECAR $REPO/dsh/campaigns/m7-autonomy-offline.json
set -l CHECKER $REPO/.local/m7/autonomy-offline/run/operator-1

env EU4_OFFLINE_ONLY=1 EU4_DSH_PYTHON=$PY \
    EU4_CAMPAIGN_STATE=$STATE \
    EU4_M7_AUTONOMY_RUN_DIR=$CHECKER \
    $REPO/dsh/scripts/run.sh \
      --campaign $SIDECAR \
      --campaign-state $STATE \
      --campaign-mode autonomous_reconstruction
```

`run.sh` calls `campaign_guard ensure` (validating the mode of an existing state,
starting the sidecar's `limits.deadline_seconds` at launch, and publishing the
host-wide marker) and then prepares the candidate anchor. Open the DSH Web
session in the registered workspace:

`/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/.local/dsh/m7-autonomy-offline/workspace`

### Goal to paste (manually create `/goal`)

`run.sh` prints the complete, verified `goal_text` (the objective plus the
trusted startup context) between the two dashed lines of its `Campaign
prepared` block. Submit **that whole block** verbatim; do not paste only
`campaigns/eu4-m7-autonomy-offline/objective.md`. The objective file is public
reference text, not the complete goal.

Do not add budget, round or correction limits to the goal.

### Inspect progress and trusted results

```fish
set -l REPO /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf
set -l PY /opt/homebrew/opt/python@3.14/bin/python3.14
set -l STATE $REPO/.local/m7/autonomy-offline/state-operator-offline.json
set -l RUN $REPO/.local/dsh/m7-autonomy-offline/run
set -l CHECKER $REPO/.local/m7/autonomy-offline/run/operator-1

$PY -m automation.campaign_guard status --state $STATE
cat $RUN/sessions/*/validations.jsonl 2>/dev/null | tail -10
find $RUN -name experiment-rationale.json -maxdepth 4 2>/dev/null | tail -5
find $CHECKER -name 'trusted-execution.json' -maxdepth 5 2>/dev/null | tail -3
ls -lt ~/.local/share/eu4-dsh/logs | head
ls -lt ~/.local/share/eu4-dsh/sessions | head
```

Checker output in the DSH transcript is the authoritative validator response.
Prefer the prepared `eu4_validate_candidate` tool for the checker run; use
`automation.agent_tools` for evidence discovery and replay diagnosis instead of
composing new wrappers.  A direct `accept.py` invocation is valid only when it
uses the campaign's run directory and the same sidecar binding.
A validation receipt is written under the session root; it names the candidate
revision/digest, checker verdict and identity digest. `trusted-execution.json`
in the checker run directory names the compiled host and output digests. A
rationale record beside a replay explains why it ran; it is not evidence.
Only the trusted checker establishes equivalence.

### Stop, resume and deactivate

```fish
set -l REPO /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf
set -l PY /opt/homebrew/opt/python@3.14/bin/python3.14
set -l STATE $REPO/.local/m7/autonomy-offline/state-operator-offline.json

# Explicit stop (Ctrl-C only ends the session; this records the reason).
$PY -m automation.campaign_guard stop --state $STATE --reason operator_stop

# Resume after the blocker is actually resolved. The deadline is not rewound.
$PY -m automation.campaign_guard resume --state $STATE --detail "blocker resolved"
# ... then relaunch the same run.sh command; `ensure` keeps the state and mode.

# End the campaign. The activation marker is host-wide and must be removed.
$PY -m automation.campaign_guard stop --state $STATE --reason objective_met
$PY -m automation.campaign_guard deactivate --state $STATE
```

An interrupted session reconnects to the same session root; no trusted state is
reset. If the operator deadline expired, start a new campaign state; `resume`
does not extend it.

### Expected offline outcome

- The seeded defect fails the checker at the first event before correction.
- The agent may reject several hypotheses; each expensive replay records a
  rationale; there is no automatic cache, so repeating an experiment is a
  deliberate choice.
- A correction is independently rebuilt and executed; the checker returns
  `EXACT_PREFIX` with scope 0..2 (`pass`), `independent_equality=true` and no
  shared input/output payload.
- At least two native goal rounds are expected: diagnosis, then correction and
  verification. There is no attempt or round cap.
- Candidate-authored screenshots or metadata never pass the checker.

## B. Live event-20 capture (separately controlled)

Live capture is not part of the offline campaign and is never started by the
autonomous agent. The campaign must be created in `controlled_capture` mode, and
**every launch needs its own operator-issued authorization**:

```fish
set -l REPO /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf
set -l PY /opt/homebrew/opt/python@3.14/bin/python3.14
set -l STATE $REPO/.local/m7/event20-live/state-operator-live.json

# Operator token: created by the operator, never inherited by the agent.
set -l TOKEN (cat ~/.local/share/eu4-dsh/operator-token)
env EU4_CAMPAIGN_ADMIN_TOKEN=$TOKEN $PY -m automation.campaign_guard ensure \
    --state $STATE --mode controlled_capture --deadline-seconds 3600

# One authorization per launch; capture cannot mint its own.
env EU4_CAMPAIGN_ADMIN_TOKEN=$TOKEN $PY -m automation.campaign_guard authorize-launch \
    --state $STATE
```

Copy the printed authorization id and run the capture from the operator
terminal (not from the agent session):

```fish
env EU4_CAMPAIGN_STATE=$STATE EU4_CAMPAIGN_AUTHORIZATION=<authorization-id> \
    $PY -m automation.m6_frame capture --output <new-output-dir> \
      --callers <reviewed-caller-set> --min-draws 8 --max-draws 512 \
      --prefix-end 20 --seal --shadow --timeout 300 ...
```

After the campaign, `campaign_guard deactivate --state $STATE`. Use a fresh
output directory; never overwrite an existing sealed capture. The event-20
scientific criterion (`EVENT20_EXACT`, complete event 0..20, required R32F and
format-specific exactness) is unchanged and is only established by the
independent checker.

## C. Authentic event-3 migration milestone

This campaign targets events 0..3 of the same authentic sealed frame.  It
reuses `campaigns/eu4-m7-event20/accept.py` with `--target-end 3`; the event-20
configuration remains the complete-scope target.

Run from the infrastructure checkout after the launcher and objective changes
are reviewed and merged into the locally fetched `origin/main`. Bash resolves
paths relative to the script, including when invoked from Fish or from another
working directory. Set `EU4_DSH_PYTHON` only to select a specific interpreter.

Check that `pnpm --version` matches the pin in [the DSH runtime manifest](../../dsh/runtime.json).
If it differs, select the pinned installation on `PATH` before running preflight.

```bash
bash dsh/scripts/m7-event3.sh preflight
bash dsh/scripts/m7-event3.sh start
bash dsh/scripts/m7-event3.sh status
bash dsh/scripts/m7-event3.sh stop
bash dsh/scripts/m7-event3.sh finish
```

- `preflight` checks the reviewed infrastructure, DSH profile, authentic
  fixture and retained candidate without creating a campaign state, anchor or
  session. `start` includes preflight and runs the existing interactive DSH
  launcher with a 60-minute deadline in `autonomous_reconstruction` mode.
  Start a new DSH session in the registered candidate workspace and paste the
  launcher's complete printed goal text as one `/goal` message.
- `status` reads the host marker and prints the active managed campaign's
  paths, mode, stop reason and remaining time. `stop` records `operator_stop`
  so the guard refuses new expensive actions. End the DSH session yourself.
- `finish` requires you to type `stopped` after the DSH session has ended. It
  checks the marker again, records `operator_stop` when the guard has no stop,
  releases only that campaign's marker, and prints the retained paths.
  Finalization receipts remain available to the DSH bridge and checker.
  A start that fails after creating a guard leaves its
  fresh records available to inspect and finish. If the marker belongs to a
  historical or different campaign, its operator must release it using that
  campaign's verified state after its session ends.

| Item | Path |
| --- | --- |
| Source campaign and reusable sidecar template | `campaigns/eu4-m7-event3/campaign.json`, `dsh/campaigns/m7-event3.json` |
| Retained candidate workspace | `.local/dsh/m7-event3/workspace` |
| Trusted reference | `.local/m7/event20/trusted-copy/47590918/frame` |
| Fresh per-run inputs and receipts | `.local/m7/event3/launches/<id>/{sidecar.json,state.json,run/}` |

Each generated sidecar changes only the campaign ID and run root. The wrapper
uses the launch directory and its generated `sidecar.json`, `state.json` and
`run/` paths to identify a managed campaign. The sidecar ID names the launch
directory. The guard ID is assigned by `run.sh` and binds the host marker to
the state. Both appear in `status`. The wrapper preserves all candidate edits,
including allowed uncommitted renderer work.
Run compatible focused tests inside the candidate and leave tests requiring
shared Git metadata writes to operator-side verification.

### Diagnostic starting evidence

The merged PR #293 Metal smoke replay (`.local/m7/pr293-metal-ch0ohkqj/metal/`)
verified the authentic reference and resource provenance and wrote a verified
producer receipt. It encoded 283/283 events with zero failures and returned 26
checkpoints with zero invalid. Event 0 on `tex:433` matched. Event 1 differed
by 23 pixels with a maximum channel error of 1. The final framebuffer differed
by 632 of 1,930,176 pixels. The standalone result is `incomplete_diagnostics`
because there is no verified in-process shadow identity. These measurements
belong to the merged-main renderer. Check the candidate's renderer-source
identity and existing reports before deciding whether to validate it again.
Start with the retained `progress.md` and
`analysis/m7/reports/event3-prefix-diagnosis.md`, including the precise-math
and depth experiments and the superseded interpretations in §25. Use
`eu4_case_inspect` to inspect the event-0 clear and event-1 draw on `tex:433`.
The event-1 cause remains unresolved. Exact event-3 equivalence requires an
independent checker pass with `qualified=true`, authentic authority and exact
prefix `{start: 0, end: 3}`.

### Historical runs

`run-60m-01` through `run-60m-05`, their state files, anchors, receipts and
research reports are historical. The tracked sidecar carries the prior run's
ID and path as template values. `start` replaces those two values in a fresh
sidecar and leaves the originals in place. `run-60m-04` ended as `blocked` and
its marker was released before preparing `run-60m-05`.

The established baseline before `run-60m-05` was `FIRST_DIVERGENCE` at event 1
(`tex:433`). The candidate's only modified replay input for that baseline was
`benchmark/eu4_gfx_frame_replay.mm` (file sha256 `a756652c…`, git blob
`9a1c53e4`: the 16-line precise-math default). The other four replay sources
were unmodified, so the shader/build source set
digested to `026925b7…`.  The latest trusted result for that source is
`run-60m-03` candidate `dd09494a…` (`fail`, earliest verified divergence event
1).  A receipt binds a candidate digest over every changed file, not per-source
digests, so the binding to blob `9a1c53e4` rests on the `run-60m-03` session
record: the exact rollback diff `749f6ff6..9a1c53e4` at CT 00:33:23 and the same
16-line diff at 00:34:30 bracket the 00:33:47 → 00:34:27 validation.  The later
`9d4a9376…` and `c9b101b0…` receipts ran while the temporary depth-readback
diagnostic was in the renderer source (141-line `.mm` diff at 00:37:56) and are
excluded; `run-60m-04` recorded no validation at all.  At that point the candidate
digest differed from `dd09494a…` only by documentation (`progress.md` and
`analysis/m7/reports/**`). The later `run-60m-05` report records its own
diagnostic variants and limitations in the retained candidate. Reuse a result
only after checking the renderer-source and replay-input identity it covers.

Use the guard status printed by `m7-event3.sh status` to establish expiry.
An estimate that a correction may take too long does not establish expiry.

## Achievement distinctions

- **Governance implementation verified** — these code paths and their tests.
- **M7 offline autonomous qualification passed** — the operator's `/goal`
  campaign above reached the checker's `EXACT_PREFIX` 0..2 pass.
- **Event-20 exactness achieved** — the live event-20 campaign returned
  `EVENT20_EXACT` on the complete scope.

Passing governance tests does not constitute either of the latter two.
