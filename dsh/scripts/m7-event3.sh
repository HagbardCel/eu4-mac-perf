#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
[[ $# == 1 ]] || fail 'usage: m7-event3.sh {preflight|start|status|stop|finish}'
case $1 in preflight|start|status|stop|finish) ;; *) fail "unknown operation: $1" ;; esac
repo=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
cd -- "$repo"
unset EU4_CAMPAIGN_STATE EU4_CAMPAIGN_ADMIN_TOKEN EU4_CAMPAIGN_AUTHORIZATION EU4_CAMPAIGN_RESERVATION \
    EU4_M7_CANDIDATE_SIDECAR EU4_M7_EVENT20_TRUSTED_ROOT EU4_DSH_WORKSPACE EU4_DSH_SIDECAR
export EU4_OFFLINE_ONLY=1 EU4_TEST_PROFILE=offline PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0
export EU4_DSH_INFRA_ROOT="$repo" EU4_DSH_SKILL_ROOT="$repo/.opencode/skills"

if [[ $1 == preflight || $1 == start ]]; then
    trusted_paths=(automation benchmark campaigns dsh .opencode/skills)
    changed=$(git diff --name-only origin/main -- "${trusted_paths[@]}") || fail 'cannot compare trusted files with origin/main'
    dirty=$(git status --porcelain --untracked-files=all -- "${trusted_paths[@]}") || fail 'cannot inspect trusted files'
    [[ -z $changed && -z $dirty ]] || fail "trusted files must match reviewed origin/main: ${changed:-$dirty}"
    git check-ignore -q -- .local/m7/event3/launches/ || fail 'launch directory must be gitignored'
fi
bootstrap=$(command -v python3.14 || command -v python3) || fail 'Python 3 is missing from PATH'
EU4_DSH_PYTHON=$("$bootstrap" -m automation.python_runtime resolve --print-path) || fail 'Python/NumPy resolution failed'
export EU4_DSH_PYTHON

exec "$EU4_DSH_PYTHON" - "$1" 3<&0 <<'PY'
import json, os, stat, subprocess, sys, tempfile
from dataclasses import replace
from pathlib import Path
from automation import campaign_guard, dsh_bridge, dsh_campaign, gfx_prefix_check

repo = Path.cwd()
launches = repo / '.local/m7/event3/launches'
template = repo / 'dsh/campaigns/m7-event3.json'
source = repo / 'campaigns/eu4-m7-event3/campaign.json'
workspace = repo / '.local/dsh/m7-event3/workspace'
reference = repo / '.local/m7/event20/trusted-copy/47590918'
target, deadline = 3, 3600


def require(condition, detail):
    if not condition:
        raise SystemExit(f'FAIL: {detail}')


def active():
    marker = campaign_guard._marker_path()
    try:
        require(stat.S_ISREG(marker.lstat().st_mode), f'invalid campaign marker: {marker}')
    except FileNotFoundError:
        return None
    guard = campaign_guard.from_env()
    require(guard is not None and guard.path != marker.resolve(),
            f'corrupt or unreadable campaign marker: {marker}')
    return guard


def managed(guard):
    require(guard is not None, 'no host-wide campaign is active')
    state = guard.path
    launch = state.parent
    sidecar_path, run = launch / 'sidecar.json', launch / 'run'
    require(launch.parent == launches and launch.name.startswith('m7-event3.')
            and state.name == 'state.json' and not sidecar_path.is_symlink() and not run.is_symlink(),
            f'active campaign was not created by this launcher: {state}')
    sidecar = dsh_campaign.load_sidecar(sidecar_path)
    status = guard.status()
    marker = json.loads(campaign_guard._marker_path().read_text(encoding='utf-8'))
    require(sidecar.campaign_id == launch.name and sidecar.run_root == run
            and sidecar.source_campaign == source and sidecar.workspace == workspace
            and sidecar.mode == 'worktree' and sidecar.require_checker_pass and not sidecar.live_capture_enabled
            and isinstance(marker, dict) and marker.get('schema') == campaign_guard.MARKER_SCHEMA
            and marker.get('state') == str(state) and status['campaign_id']
            and marker.get('campaign_id') == status['campaign_id']
            and status['mode'] == campaign_guard.MODE_AUTONOMOUS and not status['live_capture'],
            f'active campaign identity does not match the generated sidecar: {sidecar_path}')
    return sidecar, status


def main(operation):
    guard = active()
    require(launches.resolve() == launches, f'launch directory must not traverse symlinks: {launches}')
    if operation in ('preflight', 'start'):
        require(guard is None, f'host-wide campaign marker is occupied by {guard.path}' if guard else '')
        sidecar = dsh_campaign.load_sidecar(template)
        config, campaign = (json.loads(path.read_text(encoding='utf-8')) for path in (template, source))
        require(sidecar.source_campaign == source and sidecar.workspace == workspace
                and sidecar.mode == 'worktree', 'template source or retained worktree changed')
        require(sidecar.accept[2:] == ('--target-end', str(target))
                and sidecar.checker == repo / 'campaigns/eu4-m7-event20/accept.py',
                'source campaign must use the trusted checker with --target-end 3')
        require(not sidecar.live_capture_enabled and sidecar.require_checker_pass
                and config['limits']['deadline_seconds'] == deadline,
                'template must require the checker, be offline, and allow 3600 seconds')
        root = gfx_prefix_check.resolve_trusted_reference(config['trusted_reference_root'], workspace, root=repo)
        frame = reference / 'frame'
        require(root == reference and (repo / campaign['fixture_frame']).resolve() == frame,
                f'template must use the fixed trusted frame: {frame}')
        result = gfx_prefix_check.preflight_fixture(frame, require_end=target, trusted_root=root)
        require(not result['problems'], '; '.join(result['problems']))
        require(result.get('authority') == 'authentic' and result.get('trusted_evidence') is True,
                'reference must have authentic authority and verified trusted evidence')
        dsh_bridge._candidate(replace(sidecar, run_root=launches))
        profile = subprocess.run([repo / 'dsh/scripts/preflight.sh'], capture_output=True, text=True)
        require(profile.returncode == 0, f'DSH profile preflight: {(profile.stderr + profile.stdout).strip()}')
        print(f'PASS: DSH profile, retained candidate and authentic reference for events 0..{target}', flush=True)
        if operation == 'preflight':
            return
        launches.mkdir(parents=True, exist_ok=True)
        launch = Path(tempfile.mkdtemp(prefix='m7-event3.', dir=launches))
        sidecar_path, state, run = (launch / name for name in ('sidecar.json', 'state.json', 'run'))
        config.update(campaign_id=launch.name, run_root=str(run))
        with sidecar_path.open('x', encoding='utf-8') as handle:
            handle.write(json.dumps(config, indent=2) + '\n')
        print(f'Candidate workspace: {workspace}\nTrusted reference: {frame}\nSidecar: {sidecar_path}'
              f'\nState: {state}\nRun root: {run}\nTarget: 0..{target}\nDeadline: {deadline} seconds'
              '\nStop new work: bash dsh/scripts/m7-event3.sh stop'
              '\nAfter DSH exits: bash dsh/scripts/m7-event3.sh finish', flush=True)
        launcher = repo / 'dsh/scripts/run.sh'
        os.environ['EU4_CAMPAIGN_STATE'] = str(state)
        os.dup2(3, 0)  # Restore the operator's stdin after the Python here-document.
        os.close(3)
        os.execv(launcher, [str(launcher), '--campaign', str(sidecar_path), '--campaign-state',
                            str(state), '--campaign-mode', campaign_guard.MODE_AUTONOMOUS])

    sidecar, status = managed(guard)
    state = guard.path
    sidecar_path, run = sidecar.sidecar_path, sidecar.run_root
    if operation == 'finish':
        print('Type stopped to confirm the DSH session has ended: ', end='', file=sys.stderr, flush=True)
        with os.fdopen(3) as confirmation:
            require(confirmation.readline().strip() == 'stopped', 'DSH session stop was not confirmed')
        current = active()
        require(current is not None and current.path == state, 'active campaign changed during confirmation')
        current_sidecar, current_status = managed(current)
        require(current_status['campaign_id'] == status['campaign_id']
                and current_sidecar.sidecar_sha256 == sidecar.sidecar_sha256,
                'active campaign identity changed during confirmation')
        if not current_status['stop']:
            current.stop('operator_stop')
        require(current.deactivate() and active() is None, 'host-wide marker could not be released or is still active')
        print(f'Campaign finished. Retained evidence: {run}\nState: {state}\nSidecar: {sidecar_path}')
    else:
        if operation == 'stop':
            guard.stop('operator_stop')
        print(json.dumps({'campaign_id': sidecar.campaign_id, 'state': str(state),
                          'sidecar': str(sidecar_path), 'run_root': str(run),
                          'guard': guard.status()}, indent=2))


try:
    main(sys.argv[1])
except (OSError, ValueError, RuntimeError, KeyError) as exc:
    require(False, str(exc))
PY
