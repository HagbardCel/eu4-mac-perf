"""Tests for the host-wide EU4 process lease and bounded operator authorization.

These tests never launch EU4. Stub child processes hold the inherited lease
descriptor, which is the same mechanism a real game uses.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

from automation import launch_ledger, launch_lease  # noqa: E402
import eu4_benchmark  # noqa: E402
import eu4_metal_boot  # noqa: E402

STUB_LAUNCHER = textwrap.dedent(
    """
    import os, sys, time
    sys.path.insert(0, {root!r})
    from automation import launch_lease
    try:
        lease = launch_lease.acquire(sys.argv[1], check_manual=False)
    except launch_lease.LeaseError as exc:
        print(exc.kind, exc.detail, file=sys.stderr)
        raise SystemExit(3)
    print(lease.fd, flush=True)
    time.sleep(float(sys.argv[2]))
    lease.release()
    """
)


class LeaseEnvironment:
    def __init__(self, test: unittest.TestCase) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.exe = self.root / "eu4.app" / "Contents" / "MacOS" / "eu4"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b"stub executable")
        self.lease_dir = self.root / "lease"
        self._env = patch.dict(os.environ, {"EU4_LAUNCH_LEASE_DIR": str(self.lease_dir)})
        self._env.start()
        test.addCleanup(self._env.stop)

    def acquire(self, *, check_manual: bool = False) -> launch_lease.ProcessLease:
        return launch_lease.acquire(self.exe, check_manual=check_manual)


class ProcessLeaseTests(unittest.TestCase):
    def test_one_lease_per_installation(self) -> None:
        env = LeaseEnvironment(self)
        first = env.acquire()
        self.addCleanup(first.release)
        with self.assertRaises(launch_lease.LeaseError) as caught:
            env.acquire()
        self.assertEqual(caught.exception.kind, "busy")
        self.assertIn(str(os.getpid()), str(caught.exception))

    def test_a_different_worktree_and_output_do_not_bypass_the_lease(self) -> None:
        env = LeaseEnvironment(self)
        other = env.root / "other-worktree"
        other.mkdir()
        first = env.acquire()
        self.addCleanup(first.release)
        # Same canonical executable, different cwd/output: same lease file.
        script = STUB_LAUNCHER.format(root=str(ROOT))
        ran = subprocess.run(
            [sys.executable, "-c", script, str(env.exe), "5"],
            cwd=other,
            capture_output=True,
            text=True,
            timeout=20,
            env=os.environ.copy(),
        )
        self.assertEqual(ran.returncode, 3, ran.stderr)
        self.assertIn("busy", ran.stderr)

    def test_inherited_descriptor_survives_launcher_exit(self) -> None:
        """A launcher crash must not release the lease while the child lives."""
        env = LeaseEnvironment(self)
        lease = env.acquire()
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            pass_fds=lease.child_fds(),
        )
        self.addCleanup(child.kill)
        # Simulate the launcher dying: release the parent's descriptor only.
        lease.release()
        time.sleep(0.2)
        with self.assertRaises(launch_lease.LeaseError) as caught:
            env.acquire()
        self.assertEqual(caught.exception.kind, "busy")
        child.terminate()
        child.wait(timeout=10)
        time.sleep(0.2)
        second = env.acquire()
        second.release()

    def test_release_after_timeout_and_error(self) -> None:
        env = LeaseEnvironment(self)
        first = env.acquire()
        with self.assertRaises(launch_lease.LeaseError) as caught:
            launch_lease.acquire(env.exe, wait=0.1, check_manual=False)
        self.assertEqual(caught.exception.kind, "busy")
        first.release()
        first.release()  # idempotent
        again = env.acquire()
        again.release()

    def test_a_manual_game_is_refused(self) -> None:
        env = LeaseEnvironment(self)
        with patch.object(launch_lease, "manual_game_pids", return_value=[4242]):
            with self.assertRaises(launch_lease.LeaseError) as caught:
                env.acquire(check_manual=True)
        self.assertEqual(caught.exception.kind, "manual")
        self.assertIn("4242", str(caught.exception))
        # The failed acquisition released its descriptor.
        lease = env.acquire(check_manual=False)
        lease.release()

    def test_manual_detection_matches_the_canonical_path(self) -> None:
        env = LeaseEnvironment(self)
        output = "  111 /usr/bin/other\n  222 /Applications/eu4.app/Contents/MacOS/eu4\n"
        pids = launch_lease.manual_game_pids(
            env.exe,
            ps_output=output,
            resolve_pid=lambda pid: str(env.exe) if pid == 222 else "/usr/bin/other",
        )
        self.assertEqual(pids, [222])

    def test_injected_process_data_works_on_non_macos_hosts(self) -> None:
        """Portable CI is Linux; explicit test data must not be platform-gated."""
        env = LeaseEnvironment(self)
        output = "  111 /usr/bin/other\n  222 /Applications/eu4.app/Contents/MacOS/eu4\n"
        with patch.object(launch_lease.sys, "platform", "linux"):
            resolved = launch_lease.manual_game_pids(
                env.exe,
                ps_output=output,
                resolve_pid=lambda pid: str(env.exe) if pid == 222 else "/usr/bin/other",
            )
            self.assertEqual(resolved, [222])
            # Without a resolver, the Linux path uses the recorded comm column.
            comm = launch_lease.manual_game_pids(
                env.exe, ps_output=f"  222 {env.exe}\n"
            )
            self.assertEqual(comm, [222])
            self.assertEqual(launch_lease.process_executables(), [])

    def test_stale_json_body_is_not_a_lock(self) -> None:
        env = LeaseEnvironment(self)
        lease = env.acquire()
        path = lease.path
        lease.release()
        self.assertTrue(path.is_file())
        # The file still contains the old holder body. The kernel lock is free.
        self.assertIn("pid", json.loads(path.read_text()))
        again = env.acquire()
        again.release()


class AuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_root = self.root / "run"
        self.run_root.mkdir()
        self.output_root = self.run_root / "output"
        self.ledger = self.root / "authority" / "workflow.json"

    def authorize(self, **overrides):
        values = {
            "workflow_id": "wf-lease",
            "limit": 2,
            "deadline_seconds": 60.0,
            "executable": {"path": str(self.root / "eu4"), "sha256": "pinned"},
            "output_root": self.output_root,
        }
        values.update(overrides)
        return launch_ledger.authorize_workflow(
            self.ledger, self.run_root, **values
        )

    def claim(self, *, output: Path | None = None, sha: str | None = "pinned",
              timeout: float = 30.0):
        return launch_ledger.claim(
            self.ledger,
            campaign_id="wf-lease",
            run_root=self.run_root,
            output=output or self.output_root / "one",
            targets=["_GfxDraw"],
            requested_timeout=timeout,
            executable_sha256=sha,
        )

    def test_an_unattested_launch_is_refused_before_spawn(self) -> None:
        self.authorize()
        with self.assertRaises(launch_ledger.LedgerError) as caught:
            self.claim(sha=None)
        self.assertEqual(caught.exception.kind, "executable")

    def test_a_different_executable_is_refused(self) -> None:
        self.authorize()
        with self.assertRaises(launch_ledger.LedgerError) as caught:
            self.claim(sha="different")
        self.assertEqual(caught.exception.kind, "executable")

    def test_output_must_stay_under_the_authorized_root(self) -> None:
        self.authorize()
        with self.assertRaises(launch_ledger.LedgerError) as caught:
            self.claim(output=self.root / "elsewhere")
        self.assertEqual(caught.exception.kind, "output_root")

    def test_failed_launch_remains_charged(self) -> None:
        self.authorize()
        token = self.claim()
        launch_ledger.finish(self.ledger, token, pid=None, status="failed")
        value = launch_ledger.load_ledger(self.ledger)
        self.assertEqual(value["used"], 1)
        self.assertEqual(value["launches"][0]["status"], "failed")

    def test_authorize_never_replenishes_an_existing_budget(self) -> None:
        first = self.authorize()
        self.assertTrue(first["created"])
        token = self.claim()
        launch_ledger.finish(self.ledger, token, pid=1, status="completed")
        second = self.authorize()
        self.assertFalse(second["created"])
        self.assertEqual(second["used"], 1)
        self.assertEqual(second["limit"], 2)
        value = launch_ledger.load_ledger(self.ledger)
        self.assertEqual(value["launches"][0]["status"], "completed")

    def test_identical_reauthorization_preserves_receipts_and_remaining_budget(self) -> None:
        self.authorize(limit=3)
        token = self.claim()
        launch_ledger.finish(self.ledger, token, pid=1, status="completed")
        again = self.authorize(limit=3)
        self.assertFalse(again["created"])
        self.assertEqual(again["used"], 1)
        value = launch_ledger.load_ledger(self.ledger)
        self.assertEqual(len(value["launches"]), 1)
        self.assertEqual(value["launches"][0]["status"], "completed")
        self.assertEqual(value["limit"] - value["used"], 2)

    def test_reauthorization_cannot_change_the_contract(self) -> None:
        self.authorize(limit=3)
        for overrides in (
            {"limit": 4},
            {"deadline_seconds": 61.0},
            {"executable": {"path": str(self.root / "other"), "sha256": "pinned"}},
            {"executable": {"path": str(self.root / "eu4"), "sha256": "other"}},
            {"output_root": self.root / "other-output"},
        ):
            requested = {"limit": 3}
            requested.update(overrides)
            with self.assertRaises(launch_ledger.LedgerError) as caught:
                self.authorize(**requested)
            self.assertEqual(caught.exception.kind, "conflict")
        value = launch_ledger.load_ledger(self.ledger)
        self.assertEqual(value["used"], 0)

    def test_an_unpinned_ledger_cannot_be_adopted_as_pinned(self) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        launch_ledger.create_ledger(
            self.ledger, workflow_id="wf-lease", limit=2, deadline_seconds=None
        )
        with self.assertRaises(launch_ledger.LedgerError) as caught:
            self.authorize()
        self.assertEqual(caught.exception.kind, "conflict")
        value = launch_ledger.load_ledger(self.ledger)
        self.assertNotIn("executable", value)
        self.assertEqual(value["used"], 0)


class TimeoutValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_root = self.root / "run"
        self.run_root.mkdir()
        self.ledger = self.root / "ledger.json"
        self.output_root = self.root / "experiments"
        launch_ledger.create_ledger(
            self.ledger, workflow_id="wf-timeout", limit=2, deadline_seconds=60.0
        )

    def _claim(self, timeout, *, deadline=None):
        if deadline is not None:
            self.ledger.unlink()
            launch_ledger.create_ledger(
                self.ledger, workflow_id="wf-timeout", limit=2,
                deadline_seconds=deadline,
            )
        return launch_ledger.claim(
            self.ledger, campaign_id="wf-timeout", run_root=self.run_root,
            output=self.output_root / "one", targets=["_GfxDraw"],
            requested_timeout=timeout,
        )

    def test_non_finite_zero_and_negative_timeouts_are_rejected(self) -> None:
        for value in (math.nan, math.inf, -math.inf, 0, 0.0, -1, -1.0, True, "30"):
            with self.assertRaises(launch_ledger.LedgerError) as caught:
                launch_ledger.validate_timeout(value)
            self.assertEqual(caught.exception.kind, "timeout")

    def test_claim_rejects_invalid_requested_timeout_before_writing(self) -> None:
        for value in (math.nan, math.inf, -math.inf, 0, -1, True, "30"):
            with self.assertRaises(launch_ledger.LedgerError) as caught:
                self._claim(value)
            self.assertEqual(caught.exception.kind, "timeout")
        value = launch_ledger.load_ledger(self.ledger)
        self.assertEqual(value["used"], 0)

    def test_claim_rejects_invalid_persisted_deadline(self) -> None:
        for value in (0, -1, math.inf, math.nan):
            self.ledger.unlink()
            launch_ledger.create_ledger(
                self.ledger, workflow_id="wf-timeout", limit=2,
                deadline_seconds=60.0,
            )
            raw = json.loads(self.ledger.read_text())
            raw["deadline_seconds"] = value
            self.ledger.write_text(json.dumps(raw))
            with self.assertRaises(launch_ledger.LedgerError):
                launch_ledger.load_ledger(self.ledger)

    def test_valid_timeout_equal_to_deadline_is_accepted(self) -> None:
        token = self._claim(60.0, deadline=60.0)
        self.assertTrue(token)

    def test_timeout_over_deadline_is_refused(self) -> None:
        with self.assertRaises(launch_ledger.LedgerError) as caught:
            self._claim(60.001, deadline=60.0)
        self.assertEqual(caught.exception.kind, "deadline")
        value = launch_ledger.load_ledger(self.ledger)
        self.assertEqual(value["used"], 0)

    def test_invalid_deadline_cannot_create_a_ledger(self) -> None:
        for value in (math.nan, math.inf, -math.inf, 0, -1, True, "60"):
            path = self.root / f"bad-{str(value).replace('.', '_')}.json"
            with self.assertRaises(launch_ledger.LedgerError):
                launch_ledger.create_ledger(
                    path, workflow_id="wf", limit=1, deadline_seconds=value
                )
            self.assertFalse(path.exists())

    def test_metal_boot_rejects_invalid_wait_before_spawn(self) -> None:
        for value in (math.nan, math.inf, -math.inf, 0, -1, True, "180"):
            with patch.object(eu4_metal_boot.subprocess, "Popen") as popen:
                with self.assertRaises(eu4_benchmark.BenchmarkError):
                    eu4_metal_boot.launch_session(
                        self.root / f"boot-{str(value)}", wait_seconds=value
                    )
                popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
