import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tooling.common import check_oracle_bundle as cob


def _write_bundle(root: Path, blobs: dict[str, bytes]) -> None:
    bundle = root / cob.BUNDLE_DIR
    bundle.mkdir(parents=True, exist_ok=True)
    files = []
    for name, content in blobs.items():
        target = bundle / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        files.append(
            {
                "path": name,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    (bundle / cob.MANIFEST_NAME).write_text(json.dumps({"files": files}), encoding="utf-8")


def _tracked(*names: str) -> set[str]:
    return {f"{cob.BUNDLE_DIR}/{n}" for n in names}


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


class ManifestHonestyTests(unittest.TestCase):
    def test_clean_bundle_has_no_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle(root, {"a.txt": b"hello", "sub/b.txt": b"world"})
            errors = cob.check_worktree_vs_manifest(root, tracked=_tracked("a.txt", "sub/b.txt", cob.MANIFEST_NAME))
            self.assertEqual(errors, [])

    def test_tampered_bytes_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle(root, {"a.txt": b"hello"})
            (root / cob.BUNDLE_DIR / "a.txt").write_bytes(b"HELLO")
            errors = cob.check_worktree_vs_manifest(root, tracked=_tracked("a.txt", cob.MANIFEST_NAME))
            self.assertTrue(any("sha256 mismatch" in e for e in errors), errors)

    def test_extra_tracked_file_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle(root, {"a.txt": b"hello"})
            errors = cob.check_worktree_vs_manifest(
                root, tracked=_tracked("a.txt", "extra.txt", cob.MANIFEST_NAME)
            )
            self.assertTrue(any("tracked but not in manifest: extra.txt" in e for e in errors), errors)

    def test_missing_tracked_file_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle(root, {"a.txt": b"hello"})
            errors = cob.check_worktree_vs_manifest(root, tracked=_tracked(cob.MANIFEST_NAME))
            self.assertTrue(any("not tracked: a.txt" in e for e in errors), errors)

    def test_symlink_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle(root, {"a.txt": b"hello"})
            bundle = root / cob.BUNDLE_DIR
            outside = root / "outside.txt"
            outside.write_bytes(b"hello")
            (bundle / "a.txt").unlink()
            (bundle / "a.txt").symlink_to(outside)
            errors = cob.check_worktree_vs_manifest(root, tracked=_tracked("a.txt", cob.MANIFEST_NAME))
            self.assertTrue(any("symlink" in e for e in errors), errors)

    def test_unsafe_manifest_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / cob.BUNDLE_DIR
            bundle.mkdir(parents=True)
            (bundle / cob.MANIFEST_NAME).write_text(
                json.dumps({"files": [{"path": "../escape", "bytes": 0, "sha256": "00"}]}),
                encoding="utf-8",
            )
            errors = cob.check_worktree_vs_manifest(root, tracked=_tracked(cob.MANIFEST_NAME))
            self.assertTrue(any("unsafe manifest path" in e for e in errors), errors)


class RepositoryInvariantTests(unittest.TestCase):
    """The checker must pass on the real repository it guards."""

    def test_bundle_checker_passes(self) -> None:
        self.assertEqual(cob.main([]), 0)

    def test_no_root_opencode_activation(self) -> None:
        self.assertEqual(cob.check_no_root_opencode_activation(cob.repo_root()), [])


if __name__ == "__main__":
    unittest.main()
