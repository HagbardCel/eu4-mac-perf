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

# 4. Trusted reference is intact and independently verifiable.
$PY -m automation.gfx_prefix_check verify --root $TRUSTED --frame $TRUSTED/frame

# 4b. The fixture can qualify the requested prefix (same check the launcher runs).
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

## C. Authentic event-3 migration milestone

This campaign targets events 0..3 of the same authentic sealed frame.  It
reuses `campaigns/eu4-m7-event20/accept.py` with `--target-end 3`; the event-20
configuration remains the complete-scope target.

Fixed identities:

| Item | Path |
| --- | --- |
| Campaign | `campaigns/eu4-m7-event3/campaign.json` |
| Sidecar | `dsh/campaigns/m7-event3.json` |
| Candidate workspace | `.local/dsh/m7-event3/workspace` |
| Run root | `.local/dsh/m7-event3/run` |
| Trusted reference | `.local/m7/event20/trusted-copy/47590918` |

Preflight (read-only) before launch:

```fish
set -l REPO /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf
set -l PY /opt/homebrew/opt/python@3.14/bin/python3.14
set -l TRUSTED $REPO/.local/m7/event20/trusted-copy/47590918
cd $REPO

$PY -m automation.gfx_prefix_check verify --root $TRUSTED --frame $TRUSTED/frame
$PY -c "from automation import gfx_prefix_check as g; import json; r = g.preflight_fixture('$TRUSTED/frame', require_end=3, trusted_root='$TRUSTED'); print(json.dumps(r['problems']))"
```

The second command must print `[]`.  The launcher runs the same check plus the
candidate policy check and refuses `fixture_ineligible` before starting the
agent.

Launch (operator action):

```fish
set -l STATE $REPO/.local/m7/event3/state-operator.json
set -l SIDECAR $REPO/dsh/campaigns/m7-event3.json
test -f $STATE; and echo "STATE EXISTS - pick a new suffix"; or echo fresh

env EU4_OFFLINE_ONLY=1 EU4_DSH_PYTHON=$PY EU4_CAMPAIGN_STATE=$STATE \
    $REPO/dsh/scripts/run.sh --campaign $SIDECAR --campaign-state $STATE \
      --campaign-mode autonomous_reconstruction
```

Paste the printed `goal_text` block into a new `/goal`.  The current expected
result is `FIRST_DIVERGENCE` at event 1 (`tex:433`, 23 pixels, max channel
error 1).  A pass requires `qualified=true`, authentic authority and the exact
prefix `{start: 0, end: 3}`; a final image or candidate report never qualifies.

The deadline claim rule is the same for every campaign: only
`campaign_guard status --state $STATE` is authoritative.  Do not stop as
"deadline" merely because a correction may not finish in time.

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

## Achievement distinctions

- **Governance implementation verified** — these code paths and their tests.
- **M7 offline autonomous qualification passed** — the operator's `/goal`
  campaign above reached the checker's `EXACT_PREFIX` 0..2 pass.
- **Event-20 exactness achieved** — the live event-20 campaign returned
  `EVENT20_EXACT` on the complete scope.

Passing governance tests does not constitute either of the latter two.
