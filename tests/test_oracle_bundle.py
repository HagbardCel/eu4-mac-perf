import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tooling.common import check_oracle_bundle as cob

_TAG = "test-blueprint"
_BLOBS = {
    "README.md": b"# blueprint\n",
    "design/master.md": b"design payload\n",
    "oracle_proto/__init__.py": b"",
}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)


class TempBundle:
    """A throwaway git repo containing a miniature preserved bundle."""

    def __init__(self, tmp: str):
        self.root = Path(tmp)
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "tester@example.invalid")
        _git(self.root, "config", "user.name", "Tester")
        self.bundle = self.root / cob.BUNDLE_DIR
        self.bundle.mkdir(parents=True, exist_ok=True)

    # -- construction ---------------------------------------------------------
    def write(self, rel: str, data: bytes) -> Path:
        target = self.bundle / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def write_manifest(self, blobs: dict[str, bytes]) -> None:
        entries = [
            {"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for rel, data in blobs.items()
        ]
        (self.bundle / cob.MANIFEST_NAME).write_text(
            json.dumps({"kind": "test", "status": "test", "files": entries}), encoding="utf-8"
        )

    def stage_all(self) -> None:
        _git(self.root, "add", "-A")

    def commit(self, message: str = "snapshot") -> str:
        self.stage_all()
        _git(self.root, "commit", "-q", "-m", message)
        return self.rev("HEAD")

    def rev(self, ref: str) -> str:
        return _git(self.root, "rev-parse", ref).stdout.strip()

    def tag_annotated(self, tag: str) -> None:
        _git(self.root, "tag", "-a", tag, "-m", "preserve")

    def check(self, commit: str, tag: str = _TAG) -> list[str]:
        return cob.check_all(self.root, commit=commit, tag=tag)


def _preserved(tmp: str) -> tuple[TempBundle, str]:
    repo = TempBundle(tmp)
    for rel, data in _BLOBS.items():
        repo.write(rel, data)
    repo.write_manifest(_BLOBS)
    commit = repo.commit("preserve")
    repo.tag_annotated(_TAG)
    return repo, commit


class SafeRelpathTests(unittest.TestCase):
    def test_accepts_nested_relative(self) -> None:
        self.assertTrue(cob.safe_relpath("examples/diagnostic.example.json"))

    def test_rejects_parent_escape(self) -> None:
        self.assertFalse(cob.safe_relpath("../secret"))
        self.assertFalse(cob.safe_relpath("a/../../b"))

    def test_rejects_absolute_and_empty(self) -> None:
        self.assertFalse(cob.safe_relpath("/etc/passwd"))
        self.assertFalse(cob.safe_relpath(""))
        self.assertFalse(cob.safe_relpath(None))


class PreservationInvariantTests(unittest.TestCase):
    def test_clean_bundle_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, commit = _preserved(tmp)
            self.assertEqual(repo.check(commit), [])

    def test_uncommitted_byte_edit_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, commit = _preserved(tmp)
            repo.write("README.md", b"# tampered\n")
            errors = repo.check(commit)
            self.assertTrue(any("worktree bytes differ" in e for e in errors), errors)

    def test_committed_content_change_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, commit = _preserved(tmp)
            repo.write("README.md", b"# changed\n")
            repo.commit("change")
            errors = repo.check(commit)
            self.assertTrue(any("content identity changed" in e for e in errors), errors)

    def test_delete_file_and_trim_manifest_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, commit = _preserved(tmp)
            (repo.bundle / "design" / "master.md").unlink()
            remaining = {k: v for k, v in _BLOBS.items() if k != "design/master.md"}
            repo.write_manifest(remaining)
            repo.stage_all()
            errors = repo.check(commit)
            self.assertTrue(any("historical file missing from index" in e for e in errors), errors)

    def test_manifest_metadata_tamper_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, commit = _preserved(tmp)
            manifest = json.loads((repo.bundle / cob.MANIFEST_NAME).read_text())
            manifest["kind"] = "not-the-blueprint"
            (repo.bundle / cob.MANIFEST_NAME).write_text(json.dumps(manifest))
            errors = repo.check(commit)
            self.assertTrue(
                any("worktree bytes differ" in e and cob.MANIFEST_NAME in e for e in errors), errors
            )

    def test_staged_executable_bit_change_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, commit = _preserved(tmp)
            _git(repo.root, "update-index", "--chmod=+x", f"{cob.BUNDLE_DIR}/README.md")
            errors = repo.check(commit)
            self.assertTrue(any("mode changed" in e for e in errors), errors)

    def test_unstaged_executable_bit_change_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, commit = _preserved(tmp)
            os.chmod(repo.bundle / "README.md", 0o755)
            errors = repo.check(commit)
            self.assertTrue(any("executable bit mismatch" in e for e in errors), errors)

    def test_unexpected_untracked_file_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, commit = _preserved(tmp)
            repo.write("scratch.txt", b"local scratch\n")
            errors = repo.check(commit)
            self.assertTrue(any("unexpected file inside preserved bundle" in e for e in errors), errors)

    def test_symlinked_parent_directory_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, commit = _preserved(tmp)
            external = repo.root / "external"
            (external / "design").mkdir(parents=True)
            (external / "design" / "master.md").write_bytes(_BLOBS["design/master.md"])
            (repo.bundle / "design" / "master.md").unlink()
            (repo.bundle / "design").rmdir()
            (repo.bundle / "design").symlink_to(external / "design")
            errors = repo.check(commit)
            self.assertTrue(any("symlink" in e for e in errors), errors)

    def test_lightweight_tag_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, commit = _preserved(tmp)
            _git(repo.root, "tag", "lightweight", commit)
            errors = repo.check(commit, tag="lightweight")
            self.assertTrue(any("not annotated" in e for e in errors), errors)

    def test_tag_pointing_at_wrong_commit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, commit = _preserved(tmp)
            _git(repo.root, "commit", "--allow-empty", "-q", "-m", "later")
            later = repo.rev("HEAD")
            _git(repo.root, "tag", "-a", "moved", "-m", "preserve", later)
            errors = repo.check(commit, tag="moved")
            self.assertTrue(any("resolves to" in e for e in errors), errors)

    def test_duplicate_manifest_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, commit = _preserved(tmp)
            entries = [
                {"path": "README.md", "bytes": 1, "sha256": "00"},
                {"path": "README.md", "bytes": 1, "sha256": "00"},
            ]
            (repo.bundle / cob.MANIFEST_NAME).write_text(json.dumps({"files": entries}))
            errors = repo.check(commit)
            self.assertTrue(any("duplicate path in manifest" in e for e in errors), errors)

    def test_unsafe_manifest_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, commit = _preserved(tmp)
            entries = [{"path": "../escape", "bytes": 0, "sha256": "00"}]
            (repo.bundle / cob.MANIFEST_NAME).write_text(json.dumps({"files": entries}))
            errors = repo.check(commit)
            self.assertTrue(any("unsafe manifest path" in e for e in errors), errors)


class RepositoryInvariantTests(unittest.TestCase):
    """The checker must pass on the real repository it guards."""

    def test_bundle_checker_passes(self) -> None:
        self.assertEqual(cob.main([]), 0)

    def test_no_root_opencode_activation(self) -> None:
        self.assertEqual(cob.check_no_root_opencode_activation(cob.repo_root()), [])

    def test_pinned_tree_matches_real_repository(self) -> None:
        self.assertEqual(cob.check_pinned_tree_identity(cob.repo_root()), [])
        self.assertEqual(cob.check_worktree_integrity(cob.repo_root()), [])
        self.assertEqual(cob.check_manifest_consistency(cob.repo_root()), [])


if __name__ == "__main__":
    unittest.main()
