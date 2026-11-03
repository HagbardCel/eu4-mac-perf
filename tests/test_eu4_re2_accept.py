"""Offline checks for the RE2 pilot checker. Compile cases skip without a compiler."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ACCEPT = REPO / "campaigns" / "eu4-re2-pilot" / "accept.py"
PREPARE = REPO / "campaigns" / "eu4-re2-pilot" / "prepare.py"
RECON = REPO / "analysis/reconstruction/_GfxSetConstantBuffer/reconstruction"
HEADER = "analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/gfx_set_constant_buffer.hpp"
COMPILER = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")

spec = importlib.util.spec_from_file_location("eu4_re2_accept", ACCEPT)
assert spec and spec.loader
accept = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accept)


def git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), "-c", "user.email=t@example.com", "-c", "user.name=Test", *args],
        check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip()


def candidate_repo() -> tuple[Path, str]:
    root = Path(tempfile.mkdtemp(prefix="eu4-re2-candidate-"))
    git(root, "init", "-q")
    target = root / RECON.relative_to(REPO)
    target.parent.mkdir(parents=True)
    shutil.copytree(RECON, target)
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "candidate")
    return root, git(root, "rev-parse", "HEAD")


class BrokenReference:
    def load_corpus(self):
        raise FileNotFoundError("missing reference")

    def load_reference(self):
        raise FileNotFoundError("missing reference")

    def validate_reference(self, reference, corpus=None):
        return ["unqualified"]

    def compare_corpus(self, harness):
        raise AssertionError("candidate comparison ran")


class AcceptTests(unittest.TestCase):
    def test_timeout_fails_and_a_missing_reference_is_unavailable(self) -> None:
        def timed_out():
            raise subprocess.TimeoutExpired(["harness"], 60)

        self.assertEqual(accept.compare_result(timed_out)["verdict"], "fail")
        self.assertEqual(accept.reference_gate(BrokenReference())["verdict"], "unavailable")

    def test_scope_blocks_an_extra_file_a_deletion_and_a_rename(self) -> None:
        repo, _sha = candidate_repo()
        try:
            (repo / "notes.txt").write_text("x\n", encoding="utf-8")
            self.assertEqual(accept.check(repo, commit=git(repo, "rev-parse", "HEAD"), repo=repo)["verdict"], "fail")
            git(repo, "add", "notes.txt")
            git(repo, "commit", "-q", "-m", "notes")
            git(repo, "rm", "-q", "notes.txt")
            deleted = accept.check(repo, commit=git(repo, "rev-parse", "HEAD"), repo=repo)
            self.assertEqual(deleted["verdict"], "fail")
            self.assertIn("notes.txt", deleted["diagnostics"])
            git(repo, "reset", "-q", "--hard")
            git(repo, "mv", "notes.txt", "moved.txt")
            renamed = accept.check(repo, commit=git(repo, "rev-parse", "HEAD"), repo=repo)
            self.assertEqual(renamed["verdict"], "fail")
            self.assertIn("moved.txt", renamed["diagnostics"])
        finally:
            shutil.rmtree(repo)

    def test_broken_reference_cannot_pass(self) -> None:
        repo, sha = candidate_repo()
        try:
            result = accept.check(repo, commit=sha, repo=repo, diff=BrokenReference())
            self.assertEqual(result["verdict"], "unavailable")
        finally:
            shutil.rmtree(repo)

    def test_missing_compiler_is_unavailable(self) -> None:
        repo, sha = candidate_repo()
        original = accept._compiler
        accept._compiler = lambda: None
        try:
            result = accept.check(repo, commit=sha, repo=repo)
            self.assertEqual(result["verdict"], "unavailable")
        finally:
            accept._compiler = original
            shutil.rmtree(repo)

    def test_subprocess_ignores_a_candidate_pythonpath(self) -> None:
        repo, _sha = candidate_repo()
        fake = Path(tempfile.mkdtemp(prefix="eu4-re2-fake-"))
        try:
            package = fake / "reverse_engineering"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "reconstruction_diff.py").write_text(
                "raise SystemExit('LOADED-FROM-CANDIDATE')\n", encoding="utf-8")
            planted = repo / "reverse_engineering"
            shutil.copytree(package, planted)
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join((str(repo), str(fake)))
            completed = subprocess.run(
                [sys.executable, str(ACCEPT), str(repo)],
                cwd=repo, env=env, capture_output=True, text=True, timeout=120,
            )
            combined = completed.stdout + completed.stderr
            self.assertNotIn("LOADED-FROM-CANDIDATE", combined)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["verdict"], "fail")
            self.assertIn("pinned", payload["diagnostics"])
        finally:
            shutil.rmtree(repo)
            shutil.rmtree(fake)

    @unittest.skipUnless(COMPILER, "no C++ compiler")
    def test_known_good_passes_and_signed_division_fails(self) -> None:
        trusted = (REPO / HEADER).read_text(encoding="utf-8")
        repo, sha = candidate_repo()
        try:
            passed = accept.check(repo, commit=sha, repo=repo)
            self.assertEqual(passed["verdict"], "pass", passed["diagnostics"])
            header = repo / HEADER
            text = header.read_text(encoding="utf-8")
            anchor = "    return (first_dword + 3u) >> 2;"
            self.assertIn(anchor, text)
            header.write_text(text.replace(anchor, "    return static_cast<std::uint32_t>(static_cast<std::int32_t>(first_dword + 3u) / 4);", 1), encoding="utf-8")
            failed = accept.check(repo, commit=sha, repo=repo)
            self.assertEqual(failed["verdict"], "fail", failed["diagnostics"])
            self.assertEqual((REPO / HEADER).read_text(encoding="utf-8"), trusted)
            source = repo / HEADER.replace(".hpp", ".cpp")
            source.write_text("not c++\n", encoding="utf-8")
            broken = accept.check(repo, commit=sha, repo=repo)
            self.assertEqual(broken["verdict"], "fail")
            self.assertIn("compile", broken["diagnostics"])
        finally:
            shutil.rmtree(repo)

    def test_prepare_applies_the_anchor_and_refuses_a_dirty_tree(self) -> None:
        repo, _sha = candidate_repo()
        try:
            first = subprocess.run([sys.executable, str(PREPARE), str(repo)], capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("/ 4);", (repo / HEADER).read_text(encoding="utf-8"))
            self.assertIn(">> 2;", (REPO / HEADER).read_text(encoding="utf-8"))
            second = subprocess.run([sys.executable, str(PREPARE), str(repo)], capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
        finally:
            shutil.rmtree(repo)
        clean, _sha = candidate_repo()
        try:
            header = clean / HEADER
            header.write_text(header.read_text(encoding="utf-8").replace(
                "    return (first_dword + 3u) >> 2;", "    return 0;", 1), encoding="utf-8")
            git(clean, "add", ".")
            git(clean, "commit", "-q", "-m", "no anchor")
            missing = subprocess.run([sys.executable, str(PREPARE), str(clean)], capture_output=True, text=True)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("anchor", missing.stderr)
        finally:
            shutil.rmtree(clean)


if __name__ == "__main__":
    unittest.main()
