"""PR 1 acceptance: session ownership, isolation, cleanup and multi-claim.

These tests use real child processes but never EU4.  They pin the rule that
matters for coexistence with the M6 worktree: a session may signal only the
processes it owns, after re-verifying their identity, and it must leave
unrelated processes untouched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_session, launch_ledger  # noqa: E402

CHILD = [sys.executable, "-c", "import time; time.sleep(60)"]


def spawn_child() -> subprocess.Popen:
    return subprocess.Popen(CHILD, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self._old_sessions = os.environ.get("EU4_GFX_SESSION_ROOT")
        os.environ["EU4_GFX_SESSION_ROOT"] = str(self.root / "registry")
        self.children: list[subprocess.Popen] = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait()
        if self._old_sessions is None:
            os.environ.pop("EU4_GFX_SESSION_ROOT", None)
        else:
            os.environ["EU4_GFX_SESSION_ROOT"] = self._old_sessions
        self._temporary.cleanup()

    def spawn(self) -> subprocess.Popen:
        child = spawn_child()
        self.children.append(child)
        return child

    def test_run_directories_are_unique(self):
        first = gfx_session.unique_run_dir(self.root / "runs", "dual")
        second = gfx_session.unique_run_dir(self.root / "runs", "dual")
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir() and second.is_dir())

    def test_register_verifies_direct_child(self):
        session = gfx_session.Session.create(
            self.root / "runs", workflow_id="w", profile="early",
            session_root_path=self.root / "registry",
        )
        child = self.spawn()
        identity = session.register("reference", child)
        self.assertEqual(identity.pid, child.pid)
        self.assertEqual(identity.ppid, os.getpid())
        self.assertEqual(identity.pgid, child.pid)
        session.close()

    def test_unrelated_process_is_refused(self):
        session = gfx_session.Session.create(
            self.root / "runs", workflow_id="w", profile="early",
            session_root_path=self.root / "registry",
        )
        # A grandchild is not this coordinator's child: exactly the shape of an
        # EU4 process owned by another agent/worktree.
        broker = subprocess.run(
            [sys.executable, "-c",
             "import subprocess,sys;"
             "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
             "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
             "print(p.pid)"],
            capture_output=True, text=True, check=True,
        )
        foreign_pid = int(broker.stdout.strip())
        try:
            fake = type("FakePopen", (), {"pid": foreign_pid, "poll": lambda self: None})()
            with self.assertRaises(gfx_session.SessionError) as caught:
                session.register("candidate", fake)
            self.assertEqual(caught.exception.kind, "not_a_child")
        finally:
            try:
                os.kill(foreign_pid, 9)
            except OSError:
                pass
            session.close()

    def test_only_owned_process_is_terminated(self):
        session = gfx_session.Session.create(
            self.root / "runs", workflow_id="w", profile="targeted",
            session_root_path=self.root / "registry",
        )
        owned = self.spawn()
        unrelated = self.spawn()  # deliberately not registered
        session.register("reference", owned)
        results = session.terminate_all(grace_seconds=3)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, "exited")
        self.assertIsNotNone(owned.poll())
        self.assertIsNone(unrelated.poll())
        session.close()

    def test_identity_mismatch_is_never_signalled(self):
        child = self.spawn()
        identity = gfx_session.ProcessIdentity.capture(child.pid, role="reference")
        stale = gfx_session.ProcessIdentity(
            pid=identity.pid, pgid=identity.pgid, ppid=identity.ppid,
            executable=identity.executable,
            start_seconds=identity.start_seconds + 1,
            start_microseconds=identity.start_microseconds,
            role="reference",
        )
        result = gfx_session.terminate_owned(stale, process=child, grace_seconds=1)
        self.assertEqual(result.outcome, "identity_mismatch")
        self.assertIsNone(child.poll())

    def test_terminal_status_is_written_after_failure(self):
        with self.assertRaises(RuntimeError):
            with gfx_session.Session.create(
                self.root / "runs", workflow_id="w", profile="targeted",
                session_root_path=self.root / "registry",
            ) as session:
                child = self.spawn()
                session.register("candidate", child)
                self.assertTrue((session.run_dir / "terminal-status.json").is_file())
                raise RuntimeError("coordinator crashed")
        terminal = json.loads((session.run_dir / "terminal-status.json").read_text())
        self.assertEqual(terminal["status"], gfx_session.FAILED)
        self.assertIn("RuntimeError", terminal["reason"])
        self.assertEqual(len(terminal["processes"]), 1)
        self.assertTrue(terminal["terminations"])
        self.assertIsNotNone(child.poll())

    def test_other_active_sessions_are_detected_and_not_owned(self):
        first = gfx_session.Session.create(
            self.root / "runs-a", workflow_id="a", profile="early",
            session_root_path=self.root / "registry",
        )
        second = gfx_session.Session.create(
            self.root / "runs-b", workflow_id="b", profile="early",
            session_root_path=self.root / "registry",
        )
        active = second.other_active_sessions()
        self.assertEqual([row["session_id"] for row in active], [first.session_id])
        first.close()
        self.assertEqual(second.other_active_sessions(), [])
        stale = [row for row in gfx_session.Session.active_sessions(
            session_root_path=self.root / "registry") if row["liveness"] == "stale"]
        self.assertTrue(any(row["session_id"] == first.session_id for row in stale))
        second.close()

    def test_cleanup_removes_only_session_temp_files(self):
        session = gfx_session.Session.create(
            self.root / "runs", workflow_id="w", profile="early",
            session_root_path=self.root / "registry",
        )
        temp = session.run_dir / "scratch.txt"
        temp.write_text("x")
        session.owned_temp(temp)
        outside = self.root / "outside.txt"
        outside.write_text("keep")
        with self.assertRaises(gfx_session.SessionError):
            session.owned_temp(outside)
        self.assertEqual(session.cleanup_temp(), [str(temp.resolve())])
        self.assertFalse(temp.exists())
        self.assertTrue(outside.exists())
        session.close()

    def test_load_reports_terminal_state(self):
        session = gfx_session.Session.create(
            self.root / "runs", workflow_id="w", profile="early",
            session_root_path=self.root / "registry",
        )
        session.close(reason="done")
        loaded = gfx_session.Session.load(session.run_dir)
        self.assertEqual(loaded["session_id"], session.session_id)
        self.assertEqual(loaded["terminal"]["status"], gfx_session.CLOSED)

    def test_dead_process_identity_does_not_match(self):
        child = self.spawn()
        identity = gfx_session.ProcessIdentity.capture(child.pid)
        child.kill()
        child.wait()
        self.assertFalse(identity.matches())
        self.assertEqual(gfx_session.terminate_owned(identity).outcome, "identity_mismatch")


class MultiClaimTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.ledger = self.root / "ledger.json"
        self.output_root = self.root / "out"
        self.output_root.mkdir()
        launch_ledger.create_ledger(
            self.ledger, workflow_id="dual", limit=2, deadline_seconds=120.0,
            executable={"path": "/bin/true", "sha256": "a" * 64},
            output_root=str(self.output_root),
        )

    def tearDown(self):
        self._temporary.cleanup()

    def claim(self, count: int, limit_ok: bool = True):
        return launch_ledger.claim_many(
            self.ledger,
            campaign_id="dual",
            run_root=self.root,
            outputs=[self.output_root / f"{index}" for index in range(count)],
            targets=["frame"],
            requested_timeout=60.0,
            executable_sha256="a" * 64,
            executable_path="/bin/true",
            require_pinned=True,
            roles=[f"role-{index}" for index in range(count)],
        )

    def test_two_claims_are_atomic_and_roles_are_recorded(self):
        tokens = self.claim(2)
        value = launch_ledger.load_ledger(self.ledger)
        self.assertEqual(len(tokens), 2)
        self.assertEqual(value["used"], 2)
        self.assertEqual([row["role"] for row in value["launches"]], ["role-0", "role-1"])
        self.assertEqual(len(set(tokens)), 2)

    def test_over_budget_claim_reserves_nothing(self):
        self.claim(1)
        before = launch_ledger.load_ledger(self.ledger)
        with self.assertRaises(launch_ledger.LedgerError) as caught:
            self.claim(2)
        self.assertEqual(caught.exception.kind, "exhausted")
        after = launch_ledger.load_ledger(self.ledger)
        self.assertEqual(before, after)

    def test_ledger_pointer_collision_is_refused(self):
        with self.assertRaises(launch_ledger.LedgerError) as caught:
            launch_ledger.authorize_workflow(
                self.root / "workflow-ledger.json", self.root,
                workflow_id="w", limit=1, deadline_seconds=10.0,
            )
        self.assertEqual(caught.exception.kind, "collision")

    def test_every_output_must_be_inside_the_pinned_root(self):
        with self.assertRaises(launch_ledger.LedgerError) as caught:
            launch_ledger.claim_many(
                self.ledger, campaign_id="dual", run_root=self.root,
                outputs=[self.output_root / "ok", self.root / "outside"],
                targets=[], requested_timeout=60.0,
                executable_sha256="a" * 64, executable_path="/bin/true",
                require_pinned=True, roles=["reference", "candidate"],
            )
        self.assertEqual(caught.exception.kind, "output_root")
        self.assertEqual(launch_ledger.load_ledger(self.ledger)["used"], 0)


if __name__ == "__main__":
    unittest.main()
