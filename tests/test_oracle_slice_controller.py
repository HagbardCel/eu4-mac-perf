"""P3 slice: workspace freezing, receipts and the process supervisor."""
from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from oracle.slice import compare, corpus, harness, receipts, workspace
from oracle.slice.adapters import ProcessSupervisor
from oracle.slice.bridge import certify_candidate, write_controller_token
from oracle.slice.budget import RunBudgetLedger

SLICE_DIR = Path(__file__).resolve().parents[1] / "oracle" / "slice"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - defensive
        return True
    return True


def _spawner_script() -> str:
    """A parent that forks a long-lived child, both writing their pid."""
    return (
        "import os, subprocess, sys, time\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "subprocess.Popen([sys.executable, '-c',\n"
        "    'import os,sys,time; open(sys.argv[1],\"w\").write(str(os.getpid())); time.sleep(300)',\n"
        "    sys.argv[2]])\n"
        "time.sleep(300)\n"
    )


def _sigterm_ignoring_script() -> str:
    """A leader that forks a descendant which *ignores* SIGTERM, then sleeps.

    The leader dies on SIGTERM; the descendant only dies on SIGKILL. Terminating
    just the leader therefore leaves it running, which is exactly the defect the
    group-wide escalation must fix.
    """
    child = (
        "import os, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "open(sys.argv[1], 'w').write(str(os.getpid())); "
        "time.sleep(300)"
    )
    return (
        "import os, subprocess, sys, time\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        f"child = {child!r}\n"
        "subprocess.Popen([sys.executable, '-c', child, sys.argv[2]])\n"
        "time.sleep(300)\n"
    )


def _exiting_leader_script() -> str:
    """A leader that forks a long-lived descendant, waits for it, then exits 0.

    Nothing here times out: the leader finishes successfully while its
    descendant is still alive, which is the case a timeout-only cleanup misses.
    """
    child = (
        "import os, sys, time; "
        "open(sys.argv[1], 'w').write(str(os.getpid())); "
        "time.sleep(300)"
    )
    return (
        "import os, subprocess, sys, time\n"
        f"child = {child!r}\n"
        "subprocess.Popen([sys.executable, '-c', child, sys.argv[1]])\n"
        "limit = time.time() + 10.0\n"
        "while not os.path.exists(sys.argv[1]) and time.time() < limit:\n"
        "    time.sleep(0.01)\n"
        "sys.exit(0)\n"
    )


class WorkspaceTests(unittest.TestCase):
    def test_materialized_workspace_is_candidate_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = workspace.materialize_workspace(
                Path(tmp) / "workspace",
                candidate_src=SLICE_DIR / "target.c",
                header_src=SLICE_DIR / "synth.h",
            )
            self.assertTrue(ws.candidate_path.is_file())
            self.assertTrue((ws.root / "synth.h").is_file())
            self.assertTrue((ws.root / "fixtures" / "visible").is_dir())
            # No held-out corpus and no Oracle source.
            self.assertFalse((ws.root / "fixtures" / "held_out").exists())
            names = {p.name for p in ws.root.rglob("*")}
            self.assertNotIn("held_out", names)
            for forbidden in ("held_out", "hid-"):
                self.assertNotIn(forbidden, "".join(sorted(names)))

    def test_contains_no_held_out_fixture_ids(self):
        held = {f.fixture_id for f in corpus.load_corpus(corpus.HELD_OUT)}
        with tempfile.TemporaryDirectory() as tmp:
            ws = workspace.materialize_workspace(
                Path(tmp) / "workspace",
                candidate_src=SLICE_DIR / "target.c",
                header_src=SLICE_DIR / "synth.h",
            )
            blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                             for p in ws.root.rglob("*") if p.is_file())
            for fixture_id in held:
                self.assertNotIn(fixture_id, blob)

    def test_refuses_non_empty_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "workspace"
            dest.mkdir()
            (dest / "leftover").write_text("x", encoding="utf-8")
            with self.assertRaises(workspace.WorkspaceError):
                workspace.materialize_workspace(
                    dest, candidate_src=SLICE_DIR / "target.c", header_src=SLICE_DIR / "synth.h")


class FreezeTests(unittest.TestCase):
    def test_freeze_is_hashed_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            frozen = workspace.freeze_candidate(SLICE_DIR / "reference.c", Path(tmp) / "snap")
            self.assertTrue(frozen.path.is_file())
            self.assertEqual(frozen.sha256, receipts.file_sha256(SLICE_DIR / "reference.c"))
            mode = frozen.path.stat().st_mode
            self.assertFalse(mode & stat.S_IWUSR)
            with self.assertRaises(PermissionError):
                frozen.path.write_bytes(b"tampered")

    def test_freeze_refuses_non_c_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "notes.txt"
            bad.write_text("hello", encoding="utf-8")
            with self.assertRaises(workspace.WorkspaceError):
                workspace.freeze_candidate(bad, Path(tmp) / "snap")

    def test_freeze_refuses_reused_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "snap"
            workspace.freeze_candidate(SLICE_DIR / "reference.c", dest)
            with self.assertRaises(workspace.WorkspaceError):
                workspace.freeze_candidate(SLICE_DIR / "target.c", dest)

    def test_certification_uses_the_frozen_bytes_after_workspace_mutation(self):
        """Mutating the workspace after freezing must not change the verdict."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            RunBudgetLedger.create(run_dir, "r", 10).close()
            token = write_controller_token(run_dir)

            ws = workspace.materialize_workspace(
                Path(tmp) / "workspace",
                candidate_src=SLICE_DIR / "reference.c",
                header_src=SLICE_DIR / "synth.h",
            )
            frozen = workspace.freeze_candidate(ws.candidate_path, run_dir / "snapshot" / "final")

            # A late writer corrupts the still-writable workspace candidate.
            ws.candidate_path.write_text((SLICE_DIR / "target.c").read_text(), encoding="utf-8")

            verdict = certify_candidate(frozen.path, run_dir=run_dir, run_id="r", token=token)
            self.assertEqual(verdict["status"], compare.STATUS_PASS)
            self.assertEqual(verdict["candidate_sha256"], frozen.sha256)


class ReceiptTests(unittest.TestCase):
    def test_seal_verify_roundtrip(self):
        payload = {"run_id": "r", "verdict": {"status": "certified"}}
        receipt = receipts.seal(payload)
        receipts.verify(receipt)
        self.assertEqual(receipt["sha256"], receipts.receipt_digest(payload))

    def test_tampered_payload_is_rejected(self):
        receipt = receipts.seal({"run_id": "r"})
        receipt["payload"]["run_id"] = "other"
        with self.assertRaises(receipts.ReceiptError):
            receipts.verify(receipt)

    def test_trusted_reference_match(self):
        receipt = receipts.seal({"run_id": "r"})
        receipts.verify(receipt, expected_sha256=receipt["sha256"])
        with self.assertRaises(receipts.ReceiptError):
            receipts.verify(receipt, expected_sha256="0" * 64)

    def test_write_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            receipt = receipts.seal({"run_id": "r"})
            receipts.write(path, receipt)
            self.assertEqual(receipts.load(path), receipt)


class ProcessSupervisorTests(unittest.TestCase):
    def test_run_group_kills_live_descendants_on_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_pid_file = root / "parent.pid"
            child_pid_file = root / "child.pid"
            supervisor = ProcessSupervisor(deadline=time.monotonic() + 2.0, log_dir=root / "logs")
            run = supervisor.run_group(
                [sys.executable, "-c", _spawner_script(), str(parent_pid_file), str(child_pid_file)]
            )
            self.assertTrue(run.timed_out)
            self.assertTrue(parent_pid_file.is_file(), "parent never started")
            self.assertTrue(child_pid_file.is_file(), "grandchild never started")
            parent_pid = int(parent_pid_file.read_text())
            child_pid = int(child_pid_file.read_text())
            self.assertFalse(_pid_alive(parent_pid), "parent group survived the timeout")
            self.assertFalse(_pid_alive(child_pid), "grandchild survived the timeout")

    def test_run_group_cleans_up_descendants_after_a_successful_exit(self):
        """A leader that exits 0 must not leave a live descendant behind."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_pid_file = root / "child.pid"
            supervisor = ProcessSupervisor(deadline=time.monotonic() + 30.0, log_dir=root / "logs")
            run = supervisor.run_group(
                [sys.executable, "-c", _exiting_leader_script(), str(child_pid_file)]
            )
            self.assertFalse(run.timed_out, "the leader exits normally")
            self.assertEqual(run.returncode, 0)
            self.assertTrue(child_pid_file.is_file(), "descendant never started")
            child_pid = int(child_pid_file.read_text())
            self.assertFalse(_pid_alive(child_pid),
                             "a descendant of a successfully-exited leader survived the run")

    def test_terminate_all_reaps_remaining_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_pid_file = root / "parent.pid"
            child_pid_file = root / "child.pid"
            supervisor = ProcessSupervisor(deadline=time.monotonic() + 60.0, log_dir=root / "logs")
            supervisor.spawn(
                [sys.executable, "-c", _spawner_script(), str(parent_pid_file), str(child_pid_file)]
            )
            deadline = time.monotonic() + 5.0
            while not child_pid_file.is_file() and time.monotonic() < deadline:
                time.sleep(0.05)
            killed = supervisor.terminate_all()
            self.assertTrue(killed)
            self.assertFalse(_pid_alive(int(parent_pid_file.read_text())))
            self.assertFalse(_pid_alive(int(child_pid_file.read_text())))

    def test_group_termination_kills_a_sigterm_ignoring_descendant(self):
        """The leader's exit is not evidence that its descendants are gone."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_pid_file = root / "parent.pid"
            child_pid_file = root / "child.pid"
            supervisor = ProcessSupervisor(deadline=time.monotonic() + 60.0, log_dir=root / "logs")
            supervisor.spawn(
                [sys.executable, "-c", _sigterm_ignoring_script(),
                 str(parent_pid_file), str(child_pid_file)]
            )
            deadline = time.monotonic() + 5.0
            while not child_pid_file.is_file() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(child_pid_file.is_file(), "descendant never started")
            child_pid = int(child_pid_file.read_text())
            # Control: prove the descendant really ignores SIGTERM.
            os.kill(child_pid, signal.SIGTERM)
            time.sleep(0.3)
            self.assertTrue(_pid_alive(child_pid), "control: descendant must ignore SIGTERM")

            supervisor.terminate_all()
            self.assertFalse(_pid_alive(int(parent_pid_file.read_text())))
            self.assertFalse(_pid_alive(child_pid),
                             "a SIGTERM-ignoring descendant survived group termination")

    def test_run_group_does_not_spawn_after_the_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid_file = root / "never.pid"
            supervisor = ProcessSupervisor(deadline=time.monotonic() - 1.0, log_dir=root / "logs")
            run = supervisor.run_group(
                [sys.executable, "-c", "import sys; open(sys.argv[1], 'w').write('x')", str(pid_file)]
            )
            self.assertTrue(run.timed_out)
            self.assertEqual(supervisor._procs, [])
            self.assertIn("deadline", run.stderr)
            self.assertFalse(pid_file.exists(), "the process must not have been started")


if __name__ == "__main__":
    unittest.main()
