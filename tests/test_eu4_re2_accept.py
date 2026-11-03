"""Offline checks for the RE2 pilot checker. Compile cases skip without a compiler."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ACCEPT = REPO / "campaigns" / "eu4-re2-pilot" / "accept.py"
PREPARE = REPO / "campaigns" / "eu4-re2-pilot" / "prepare.py"
RECON = REPO / "analysis/reconstruction/_GfxSetConstantBuffer/reconstruction"
HEADER = "analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/gfx_set_constant_buffer.hpp"
PINNED = "2923c1407371656fd51d962b085eaebc52650993"
COMPILER = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
UNTRUSTED_CLI = """
import importlib.util, sys, types
fake = types.ModuleType("reverse_engineering.reconstruction_diff")
fake.__file__ = "/tmp/evil/reconstruction_diff.py"
sys.modules[fake.__name__] = fake
spec = importlib.util.spec_from_file_location("eu4_re2_accept_cli", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
raise SystemExit(module.main([sys.argv[2]]))
"""
UNLOADED_IMPORT = """
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("eu4_re2_accept_unloaded", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
before = list(sys.path)
module.import_diff(Path(sys.argv[2]))
print("RESTORED" if list(sys.path) == before else "CHANGED")
"""

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

    def test_loaded_modules_keep_their_identity(self) -> None:
        import reverse_engineering
        import reverse_engineering.reconstruction_diff as diff
        before = {
            name: sys.modules[name]
            for name in list(sys.modules)
            if name == "reverse_engineering" or name.startswith("reverse_engineering.")
        }
        path_before = list(sys.path)
        loaded = accept.import_diff(Path(tempfile.mkdtemp(prefix="eu4-re2-id-")))
        self.assertIs(loaded, diff)
        self.assertEqual(sys.path, path_before)
        for name, module in before.items():
            self.assertIs(sys.modules[name], module)

    def test_parent_without_comparator_keeps_the_parent(self) -> None:
        import reverse_engineering
        parent = sys.modules["reverse_engineering"]
        key = "reverse_engineering.reconstruction_diff"
        saved = sys.modules.pop(key, None)
        saved_attr = getattr(parent, "reconstruction_diff", None)
        path_before = list(sys.path)
        try:
            loaded = accept.import_diff(Path(tempfile.mkdtemp(prefix="eu4-re2-parent-")))
            self.assertIs(sys.modules["reverse_engineering"], parent)
            self.assertEqual(
                Path(loaded.__file__).resolve(),
                REPO / "reverse_engineering" / "reconstruction_diff.py",
            )
            self.assertEqual(sys.path, path_before)
        finally:
            if saved is not None:
                sys.modules[key] = saved
            if saved_attr is not None:
                parent.reconstruction_diff = saved_attr

    def test_untrusted_comparator_is_unavailable_json(self) -> None:
        key = "reverse_engineering.reconstruction_diff"
        saved = sys.modules.get(key)
        fake = types.ModuleType(key)
        fake.__file__ = "/tmp/evil/reconstruction_diff.py"
        sys.modules[key] = fake
        repo, sha = candidate_repo()
        try:
            result = accept.check(repo, commit=sha, repo=repo)
            self.assertEqual(result["verdict"], "unavailable")
            self.assertIn("untrusted", result["diagnostics"])
            self.assertIs(sys.modules[key], fake)
        finally:
            if saved is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = saved
            shutil.rmtree(repo)
        completed = subprocess.run(
            [sys.executable, "-c", UNTRUSTED_CLI, str(ACCEPT), str(repo)],
            capture_output=True, text=True, timeout=60,
        )
        self.assertNotIn("Traceback", completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["verdict"], "unavailable")

    def test_unloaded_import_restores_sys_path(self) -> None:
        fake = Path(tempfile.mkdtemp(prefix="eu4-re2-fake-"))
        workspace = Path(tempfile.mkdtemp(prefix="eu4-re2-empty-"))
        try:
            package = fake / "reverse_engineering"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "reconstruction_diff.py").write_text(
                "raise SystemExit('LOADED-FROM-CANDIDATE')\n", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(fake)
            completed = subprocess.run(
                [sys.executable, "-c", UNLOADED_IMPORT, str(ACCEPT), str(workspace)],
                cwd=workspace, env=env, capture_output=True, text=True, timeout=60,
            )
            combined = completed.stdout + completed.stderr
            self.assertNotIn("LOADED-FROM-CANDIDATE", combined, combined)
            self.assertIn("RESTORED", completed.stdout)
        finally:
            shutil.rmtree(fake)
            shutil.rmtree(workspace)

    def test_prepare_rejects_the_wrong_tree_and_mutates_the_pinned_worktree(self) -> None:
        trusted = (REPO / HEADER).read_text(encoding="utf-8")
        primary = subprocess.run([sys.executable, str(PREPARE), str(REPO)], capture_output=True, text=True)
        self.assertNotEqual(primary.returncode, 0)
        self.assertIn("primary", primary.stderr)
        self.assertEqual((REPO / HEADER).read_text(encoding="utf-8"), trusted)
        parent = Path(tempfile.mkdtemp(prefix="eu4-re2-foreign-"))
        foreign_root = parent / "repo"
        foreign = parent / "wt"
        try:
            foreign_root.mkdir()
            git(foreign_root, "init", "-q")
            target = foreign_root / RECON.relative_to(REPO)
            target.parent.mkdir(parents=True)
            shutil.copytree(RECON, target)
            git(foreign_root, "add", ".")
            git(foreign_root, "commit", "-q", "-m", "foreign")
            git(foreign_root, "worktree", "add", "--detach", str(foreign), "HEAD")
            rejected = subprocess.run(
                [sys.executable, str(PREPARE), str(foreign)], capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("different repository", rejected.stderr)
            self.assertIn(">> 2;", (foreign / HEADER).read_text(encoding="utf-8"))
        finally:
            subprocess.run(
                ["git", "-C", str(foreign_root), "worktree", "remove", "--force", str(foreign)],
                check=False, capture_output=True)
            shutil.rmtree(parent, ignore_errors=True)
        self._prepare_worktree("HEAD", accept=False)
        self._prepare_worktree(PINNED, accept=True)
        self.assertEqual((REPO / HEADER).read_text(encoding="utf-8"), trusted)

    def _prepare_worktree(self, commit: str, *, accept: bool) -> None:
        parent = Path(tempfile.mkdtemp(prefix="eu4-re2-linked-"))
        worktree = parent / "worktree"
        try:
            git(REPO, "worktree", "add", "--detach", str(worktree), commit)
            header = worktree / HEADER
            before = header.read_text(encoding="utf-8")
            first = subprocess.run(
                [sys.executable, str(PREPARE), str(worktree)], capture_output=True, text=True)
            if not accept:
                self.assertNotEqual(first.returncode, 0, first.stderr)
                self.assertEqual(header.read_text(encoding="utf-8"), before)
                return
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("/ 4);", header.read_text(encoding="utf-8"))
            second = subprocess.run(
                [sys.executable, str(PREPARE), str(worktree)], capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already modified", second.stderr)
        finally:
            subprocess.run(
                ["git", "-C", str(REPO), "worktree", "remove", "--force", str(worktree)],
                check=False, capture_output=True)
            shutil.rmtree(parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
