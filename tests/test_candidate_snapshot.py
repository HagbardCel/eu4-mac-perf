"""Generic candidate-delta snapshot: enumeration, validation, round trip."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from automation import candidate_snapshot as snapshot


class RepoCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@local")
        self.git("config", "user.name", "test")
        (self.repo / "keep.txt").write_text("keep\n", encoding="utf-8")
        (self.repo / "gone.txt").write_text("gone\n", encoding="utf-8")
        (self.repo / "mod.txt").write_text("before\n", encoding="utf-8")
        (self.repo / "tool.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "baseline")
        self.baseline = self.git("rev-parse", "HEAD").strip()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True, text=True, check=True,
        ).stdout

    def mutate(self) -> None:
        (self.repo / "mod.txt").write_text("after\n", encoding="utf-8")
        (self.repo / "gone.txt").unlink()
        (self.repo / "added.txt").write_text("new\n", encoding="utf-8")
        os.chmod(self.repo / "tool.sh", 0o755)
        self.git("add", "-A")
        self.git("mv", "keep.txt", "moved.txt")


class EnumerateTests(RepoCase):
    def test_kinds_and_modes_survive(self) -> None:
        self.mutate()
        delta = {entry["path"]: entry for entry in snapshot.enumerate_delta(self.repo)}
        self.assertEqual(delta["mod.txt"]["kind"], "modify")
        self.assertEqual(delta["gone.txt"]["kind"], "delete")
        self.assertEqual(delta["added.txt"]["kind"], "add")
        self.assertEqual(delta["tool.sh"]["kind"], "modify")
        self.assertEqual(delta["tool.sh"]["mode"], "100755")
        self.assertEqual(delta["moved.txt"]["kind"], "rename")
        self.assertEqual(delta["moved.txt"]["old_path"], "keep.txt")

    def test_unstaged_delete_and_add_are_distinct(self) -> None:
        (self.repo / "gone.txt").unlink()
        (self.repo / "added.txt").write_text("new\n", encoding="utf-8")
        delta = {entry["path"]: entry for entry in snapshot.enumerate_delta(self.repo)}
        self.assertEqual(delta["gone.txt"]["kind"], "delete")
        self.assertEqual(delta["added.txt"]["kind"], "add")


class ValidateTests(RepoCase):
    def test_outside_allowlist_is_refused(self) -> None:
        self.mutate()
        delta = snapshot.enumerate_delta(self.repo)
        problems = snapshot.validate_delta(delta, allowed=("mod.txt",))
        self.assertTrue(any("outside allowlist" in p for p in problems))

    def test_frozen_path_is_refused(self) -> None:
        self.mutate()
        delta = snapshot.enumerate_delta(self.repo)
        problems = snapshot.validate_delta(
            delta, allowed=("*",), forbidden=("mod.txt",)
        )
        self.assertTrue(any("frozen path" in p for p in problems))

    def test_symlink_needs_explicit_permission(self) -> None:
        os.symlink(self.repo / "mod.txt", self.repo / "link.txt")
        delta = snapshot.enumerate_delta(self.repo)
        self.assertTrue(any("symlink" in p for p in snapshot.validate_delta(delta, ("*",))))
        self.assertEqual(
            snapshot.validate_delta(delta, ("*",), allow_symlinks=True), []
        )


class RoundTripTests(RepoCase):
    def test_snapshot_reproduces_the_delta_exactly(self) -> None:
        self.mutate()
        delta = snapshot.enumerate_delta(self.repo)
        self.assertEqual(snapshot.validate_delta(delta, ("*",)), [])
        built = snapshot.build_candidate(self.repo, self.repo, self.baseline, delta)
        self.assertEqual(
            snapshot.verify_round_trip(self.repo, self.baseline, built["candidate"], delta),
            [],
        )
        # The deletion is real in the snapshot, and the mode change persisted.
        files = self.git("ls-tree", "-r", built["candidate"]).splitlines()
        names = [line.split("\t")[1] for line in files]
        self.assertNotIn("gone.txt", names)
        self.assertIn("moved.txt", names)
        tool = [line for line in files if line.endswith("\ttool.sh")][0]
        self.assertTrue(tool.startswith("100755"))

    def test_missing_change_is_reported(self) -> None:
        (self.repo / "added.txt").write_text("new\n", encoding="utf-8")
        delta = snapshot.enumerate_delta(self.repo)
        built = snapshot.build_candidate(self.repo, self.repo, self.baseline, delta)
        # Verify against a delta that includes a change we never built.
        extra = delta + [{"path": "keep.txt", "kind": "modify", "mode": "100644",
                          "symlink": False, "exists": True}]
        problems = snapshot.verify_round_trip(
            self.repo, self.baseline, built["candidate"], extra
        )
        self.assertTrue(any("missing an intended change" in p for p in problems))

    def test_symlink_round_trip(self) -> None:
        os.symlink("mod.txt", self.repo / "link.txt")
        delta = snapshot.enumerate_delta(self.repo)
        built = snapshot.build_candidate(self.repo, self.repo, self.baseline, delta)
        self.assertEqual(
            snapshot.verify_round_trip(self.repo, self.baseline, built["candidate"], delta),
            [],
        )
        entry = [line for line in self.git("ls-tree", "-r", built["candidate"]).splitlines()
                 if line.endswith("\tlink.txt")][0]
        self.assertTrue(entry.startswith("120000"))


class FingerprintTests(RepoCase):
    def test_recheck_detects_a_change_during_the_snapshot(self) -> None:
        before = snapshot.fingerprint(self.repo)
        self.assertEqual(snapshot.recheck(self.repo, before), [])
        (self.repo / "mod.txt").write_text("changed\n", encoding="utf-8")
        self.assertTrue(snapshot.recheck(self.repo, before))


if __name__ == "__main__":
    unittest.main()


class IgnoredInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / ".gitignore").write_text("build/\n", encoding="utf-8")
        (self.repo / "build" / "nested").mkdir(parents=True)
        (self.repo / "build" / "nested" / "generated.bin").write_bytes(b"one")
        subprocess.run(["git", "-C", str(self.repo), "add", ".gitignore"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "init"], check=True,
        )

    def test_files_inside_an_ignored_directory_are_inventoried(self) -> None:
        from automation import candidate_snapshot as snapshot

        inventory = snapshot.ignored_inputs(self.repo)
        paths = {row["path"] for row in inventory["entries"]}
        # Git reports "build/" as one record; the contents still have to appear.
        self.assertIn("build/nested/generated.bin", paths)
        self.assertFalse(inventory["truncated"])

    def test_changing_an_ignored_input_changes_the_digest(self) -> None:
        from automation import candidate_snapshot as snapshot

        before = snapshot.ignored_digest(self.repo)
        (self.repo / "build" / "nested" / "generated.bin").write_bytes(b"two")
        self.assertNotEqual(before, snapshot.ignored_digest(self.repo))
