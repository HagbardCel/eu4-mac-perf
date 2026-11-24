"""Operator wrapper tests with a real temporary worktree and no native hosts."""

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest

from automation import campaign_guard


ROOT = Path(__file__).resolve().parents[1]


class Event3LauncherTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.repo = self.root / 'infra checkout'
        self.repo.mkdir()
        self.workspace = self.repo / '.local/dsh/m7-event3/workspace'
        self.launches = self.repo / '.local/m7/event3/launches'
        self.marker = self.root / 'active-campaign.json'
        self.calls = self.root / 'launcher-calls.jsonl'
        self.script = self.repo / 'dsh/scripts/m7-event3.sh'
        self.write('.gitignore', '.local/\n')
        self.write('automation/frozen.py', '# protected\n')
        self.write('benchmark/renderer.mm', '// candidate baseline\n')
        self.write('campaigns/eu4-m7-event20/objective.md', 'Exact prefix\n')
        self.write('campaigns/eu4-m7-event20/accept.py', '# never executed\n')
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Launcher fixture')
        self.commit()
        baseline = self.git('rev-parse', 'HEAD')
        self.git('worktree', 'add', '--quiet', '--detach', str(self.workspace), baseline)
        # Allowed retained renderer work must survive every operation.
        self.retained = self.workspace / 'benchmark/renderer.mm'
        self.retained.write_text('// retained renderer changes\n')

        campaign_path = 'campaigns/eu4-m7-event3/campaign.json'
        template_path = 'dsh/campaigns/m7-event3.json'
        campaign = json.loads((ROOT / campaign_path).read_text())
        campaign['workspace']['commit'] = baseline
        self.template = json.loads((ROOT / template_path).read_text())
        self.template['candidate_baseline_commit'] = baseline
        self.write(campaign_path, json.dumps(campaign))
        self.write(template_path, json.dumps(self.template))
        self.write('.local/m7/event20/trusted-copy/47590918/trusted-evidence.json', '{}')
        self.write(str(self.template['run_root']) + '/identity.json', 'historical anchor\n')
        self.historical_state = self.write('.local/m7/event3/state-operator-60m-05.json', 'historical state\n')
        self.write('dsh/scripts/m7-event3.sh', (ROOT / 'dsh/scripts/m7-event3.sh').read_text(), executable=True)
        self.write('dsh/scripts/preflight.sh', '#!/bin/sh\n[ "${TEST_BAD_PROFILE:-}" != yes ] || { echo "bad toolchain"; exit 1; }\n',
                   executable=True)
        self.write('dsh/scripts/run.sh', '#!/bin/sh\nexec "$EU4_DSH_PYTHON" "$TEST_ROOT/mock-run.py" "$@"\n', executable=True)
        self.commit()
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')

        self.write(self.root / 'numpy.py', '__version__ = "fixture"\n')
        self.write(self.root / 'sitecustomize.py', '''
import os
from pathlib import Path
from automation import campaign_guard, dsh_bridge, dsh_campaign, gfx_prefix_check

marker = Path(os.environ['TEST_ROOT']) / 'active-campaign.json'
campaign_guard._marker_path = lambda: marker
dsh_bridge.infra_root = dsh_campaign.infra_root = lambda: Path(os.environ['TEST_REPO'])

def fixture(frame, *, require_end, trusted_root):
    assert require_end == 3 and frame == trusted_root / 'frame'
    return {'authority': os.environ.get('TEST_AUTHORITY', 'authentic'),
            'trusted_evidence': True,
            'problems': ['fixture digest mismatch'] if os.environ.get('TEST_BAD_FIXTURE') else []}

gfx_prefix_check.preflight_fixture = fixture
if os.environ.get('TEST_UNREADABLE_MARKER'):
    read_text = Path.read_text
    def read(path, *args, **kwargs):
        if path == marker:
            raise PermissionError('fixture marker unreadable')
        return read_text(path, *args, **kwargs)
    Path.read_text = read
''')
        self.write(self.root / 'mock-run.py', '''
import json, os, sys
from pathlib import Path
from automation import campaign_guard

flags = sys.argv[1:]
assert flags[0::2] == ['--campaign', '--campaign-state', '--campaign-mode']
sidecar, state = map(Path, flags[1:5:2])
assert flags[-1] == campaign_guard.MODE_AUTONOMOUS
assert os.environ['EU4_CAMPAIGN_STATE'] == str(state)
assert os.environ['EU4_OFFLINE_ONLY'] == '1' and os.environ['EU4_TEST_PROFILE'] == 'offline'
assert str(Path.cwd()) == os.environ['EU4_DSH_INFRA_ROOT']
assert not any(key in os.environ for key in ('EU4_CAMPAIGN_ADMIN_TOKEN',
    'EU4_CAMPAIGN_AUTHORIZATION', 'EU4_CAMPAIGN_RESERVATION', 'EU4_M7_CANDIDATE_SIDECAR', 'EU4_M7_EVENT20_TRUSTED_ROOT',
    'EU4_DSH_WORKSPACE', 'EU4_DSH_SIDECAR'))
if os.environ.get('TEST_OPERATOR_INPUT'):
    assert sys.stdin.readline().strip() == os.environ['TEST_OPERATOR_INPUT']
data = json.loads(sidecar.read_text())
run = Path(data['run_root'])
with (Path(os.environ['TEST_ROOT']) / 'launcher-calls.jsonl').open('a') as calls:
    calls.write(json.dumps({'sidecar': str(sidecar), 'state': str(state), 'run': str(run)}) + '\\n')
campaign_guard.Guard(state).ensure(deadline_seconds=data['limits']['deadline_seconds'])
if os.environ.get('TEST_START_FAILURE'):
    raise SystemExit(47)
run.mkdir()
(run / 'identity.json').write_text('new mock anchor')
print('authoritative goal from run.sh')
''')
        self.env = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, (self.root, ROOT))),
                        TEST_ROOT=str(self.root),
                        TEST_REPO=str(self.repo), EU4_DSH_PYTHON=sys.executable,
                        EU4_CAMPAIGN_STATE='/stale/shell/state.json',
                        EU4_CAMPAIGN_ADMIN_TOKEN='stale', EU4_CAMPAIGN_AUTHORIZATION='stale',
                        EU4_CAMPAIGN_RESERVATION='stale',
                        EU4_M7_CANDIDATE_SIDECAR='stale', EU4_M7_EVENT20_TRUSTED_ROOT='stale',
                        EU4_DSH_WORKSPACE='stale', EU4_DSH_SIDECAR='stale',
                        EU4_DSH_INFRA_ROOT='stale', RUN='stale', PY='stale', FRAME='stale', OUT='stale')

    def write(self, path, text, *, executable=False):
        path = self.repo / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if executable:
            path.chmod(0o755)
        return path

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')

    def invoke(self, operation, *, answer='', **overrides):
        return subprocess.run(['bash', str(self.script), operation], cwd=self.root,
                              env=dict(self.env, **overrides), capture_output=True,
                              input=answer, text=True, timeout=30)

    def assert_refused(self, result, reason):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('FAIL:', result.stderr)
        self.assertIn(reason, result.stderr)

    def start(self, **overrides):
        result = self.invoke('start', **overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('authoritative goal from run.sh', result.stdout)
        return json.loads(self.calls.read_text().splitlines()[-1])

    def test_preflight_is_read_only_and_works_from_another_directory(self):
        result = self.invoke('preflight')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PASS:', result.stdout)
        self.assertFalse(self.launches.exists())
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.calls.exists())
        self.assertEqual(self.retained.read_text(), '// retained renderer changes\n')

    def test_active_marker_refuses_even_after_stop(self):
        self.start()
        self.assert_refused(self.invoke('start'), 'host-wide campaign marker is occupied')
        self.assertEqual(self.invoke('stop').returncode, 0)
        self.assert_refused(self.invoke('preflight'), 'host-wide campaign marker is occupied')
        self.assertEqual(len(list(self.launches.iterdir())), 1)

    def test_corrupt_unreadable_and_dangling_markers_fail_closed(self):
        self.marker.write_text('{broken')
        self.assert_refused(self.invoke('start'), 'corrupt or unreadable campaign marker')
        self.assert_refused(self.invoke('start', TEST_UNREADABLE_MARKER='yes'),
                            'corrupt or unreadable campaign marker')
        self.marker.unlink()
        self.marker.symlink_to(self.root / 'missing')
        self.assert_refused(self.invoke('start'), 'invalid campaign marker')
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.launches.exists())

    def test_bad_fixture_or_candidate_refuses_before_start(self):
        self.assert_refused(self.invoke('start', TEST_BAD_FIXTURE='yes'), 'fixture digest mismatch')
        self.assert_refused(self.invoke('start', TEST_AUTHORITY='derived'), 'authentic authority')
        (self.workspace / 'automation/frozen.py').write_text('# forbidden edit\n')
        self.assert_refused(self.invoke('start'), 'automation/frozen.py')
        self.assertFalse(self.launches.exists())
        self.assertFalse(self.calls.exists())

    def test_unreviewed_infrastructure_and_profile_failure_refuse(self):
        self.assert_refused(self.invoke('start', TEST_BAD_PROFILE='yes'), 'bad toolchain')
        self.write('automation/frozen.py', '# unreviewed infrastructure\n')
        self.assert_refused(self.invoke('start'), 'trusted files must match reviewed origin/main')
        self.assertFalse(self.calls.exists())

    def test_wrong_acceptance_target_refuses_even_from_reviewed_infra(self):
        path = self.repo / 'campaigns/eu4-m7-event3/campaign.json'
        source = json.loads(path.read_text())
        source['accept'][-1] = '20'
        path.write_text(json.dumps(source))
        self.commit()
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.assert_refused(self.invoke('start'), 'trusted checker with --target-end 3')
        self.assertFalse(self.calls.exists())

    def test_fresh_starts_preserve_sidecar_fields_and_never_reuse_paths(self):
        first = self.start(answer='operator stdin\n', TEST_OPERATOR_INPUT='operator stdin')
        finish = self.invoke('finish', answer='stopped\n')
        self.assertEqual(finish.returncode, 0, finish.stderr)
        second = self.start()
        for row in (first, second):
            generated = json.loads(Path(row['sidecar']).read_text())
            self.assertEqual(generated.pop('campaign_id'), Path(row['state']).parent.name)
            self.assertEqual(generated.pop('run_root'), row['run'])
            self.assertEqual(generated, {key: value for key, value in self.template.items()
                                         if key not in ('campaign_id', 'run_root')})
            self.assertTrue(Path(row['state']).is_file())
            self.assertTrue((Path(row['run']) / 'identity.json').is_file())
        self.assertNotEqual(first['state'], second['state'])
        self.assertNotEqual(first['run'], second['run'])

    def test_failed_start_preserves_historical_and_fresh_state(self):
        result = self.invoke('start', TEST_START_FAILURE='yes')
        self.assertEqual(result.returncode, 47, result.stderr)
        self.assertEqual(self.historical_state.read_text(), 'historical state\n')
        history = self.repo / self.template['run_root'] / 'identity.json'
        self.assertEqual(history.read_text(), 'historical anchor\n')
        status = self.invoke('status')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertTrue(Path(json.loads(status.stdout)['state']).is_file())
        self.assertEqual(self.invoke('finish', answer='stopped\n').returncode, 0)

    def test_stop_and_finish_only_act_on_verified_active_campaign(self):
        active = self.start()
        status = json.loads(self.invoke('status').stdout)
        self.assertEqual(status['run_root'], active['run'])
        self.assertEqual(status['campaign_id'], Path(active['state']).parent.name)
        self.assertNotEqual(status['campaign_id'], status['guard']['campaign_id'])
        self.assertEqual(status['guard']['mode'], campaign_guard.MODE_AUTONOMOUS)
        self.assertFalse(status['guard']['live_capture'])
        self.assertGreater(status['guard']['seconds_remaining'], 0)
        self.assertLessEqual(status['guard']['seconds_remaining'], 3600)
        self.assert_refused(self.invoke('finish'), 'DSH session stop was not confirmed')
        generated = Path(active['sidecar'])
        original = generated.read_text()
        generated.write_text(json.dumps(dict(json.loads(original), campaign_id='different')))
        for op in ('status', 'stop', 'finish'):
            self.assert_refused(self.invoke(op, answer='stopped\n'), 'identity does not match')
        self.assertTrue(self.marker.exists())
        generated.write_text(original)
        stopped = self.invoke('stop')
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(json.loads(stopped.stdout)['guard']['stop']['reason'], 'operator_stop')
        state = Path(active['state'])
        stopped_state = state.read_bytes()
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.invoke('finish', answer='stopped\n').returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertEqual(state.read_bytes(), stopped_state)
        self.assertTrue((Path(active['run']) / 'identity.json').is_file())

    def test_finish_records_operator_stop_and_preserves_finalization_receipts(self):
        for kind in ('abbreviated', 'unrelated', 'complete'):
            with self.subTest(kind=kind):
                active = self.start()
                state = Path(active['state'])
                session = Path(active['run']) / 'sessions/session-example'
                session.mkdir(parents=True)
                receipt = {'schema': 'eu4-dsh-finalization/v1',
                           'campaign_id': state.parent.name, 'session_root': str(session)}
                if kind == 'unrelated':
                    receipt['campaign_id'] = 'another-campaign'
                if kind == 'complete':
                    receipt.update(identity_digest='e' * 64, frozen_candidate={}, replay={},
                                   ignored_inputs=[], fresh_verdict='pass', duration_ms=1,
                                   session={'session_id': session.name}, session_layout='session',
                                   created_at='2026-11-24T00:00:00Z')
                finalization = session / 'finalization.json'
                finalization.write_text(json.dumps(receipt))
                before = finalization.read_bytes()
                result = self.invoke('finish', answer='stopped\n')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(state.read_text())['stop']['reason'], 'operator_stop')
                self.assertFalse(self.marker.exists())
                self.assertEqual(finalization.read_bytes(), before)

    def test_finish_rechecks_marker_after_confirmation(self):
        active = self.start()
        state = Path(active['state'])
        before = state.read_bytes()
        prompt = 'Type stopped to confirm the DSH session has ended: '
        with subprocess.Popen(['bash', str(self.script), 'finish'], cwd=self.root, env=self.env,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            try:
                self.assertTrue(select.select([process.stderr], [], [], 10)[0])
                self.assertEqual(process.stderr.read(len(prompt)), prompt)
                foreign = self.write(self.root / 'foreign-state.json',
                                     json.dumps(dict(json.loads(before), campaign_id='foreign')))
                marker = dict(json.loads(self.marker.read_text()), state=str(foreign), campaign_id='foreign')
                self.marker.write_text(json.dumps(marker))
                _, stderr = process.communicate('stopped\n', timeout=10)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn('active campaign changed during confirmation', stderr)
            finally:
                if process.poll() is None:
                    process.kill()
        self.assertEqual(state.read_bytes(), before)
        self.assertEqual(json.loads(self.marker.read_text()), marker)
        foreign_before = foreign.read_bytes()
        for op in ('status', 'stop', 'finish'):
            self.assert_refused(self.invoke(op, answer='stopped\n'), 'not created by this launcher')
        self.assertEqual(foreign.read_bytes(), foreign_before)

    def test_historical_marker_is_never_treated_as_managed(self):
        self.marker.write_text(json.dumps({'schema': campaign_guard.MARKER_SCHEMA,
                                          'state': str(self.historical_state)}))
        for op in ('status', 'stop', 'finish'):
            self.assert_refused(self.invoke(op, answer='stopped\n'), 'not created by this launcher')
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.historical_state.read_text(), 'historical state\n')
