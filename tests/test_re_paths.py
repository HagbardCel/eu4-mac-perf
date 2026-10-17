import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reverse_engineering.paths import (
    ReError,
    assert_ghidra_safe_path,
    binary_sha256,
    ghidra_project_dir,
    ghidra_work_root,
    local_binary_root,
)

TEST_SHA = "a" * 64


class GhidraPathTests(unittest.TestCase):
    def test_local_binary_root_uses_dot_local(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            root = local_binary_root(TEST_SHA)
        self.assertIn(".local", root.parts)
        self.assertEqual(root.name, TEST_SHA)

    def test_ghidra_project_dir_default_is_per_sha_outside_local(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            corpus = local_binary_root(TEST_SHA)
            project = ghidra_project_dir(corpus)
        self.assertEqual(project.parent, Path.home() / "GhidraEU4" / TEST_SHA)
        self.assertEqual(project.name, "ghidra-project")
        for part in project.parts:
            self.assertFalse(part.startswith("."))

    def test_ghidra_work_root_with_eu4_ghidra_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {"EU4_GHIDRA_WORK": tmp},
                clear=True,
            ):
                root = ghidra_work_root(TEST_SHA)
            self.assertEqual(root, Path(tmp) / TEST_SHA)

    def test_ghidra_project_dir_with_eu4_re_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"EU4_RE_EXPORT": tmp}, clear=True):
                corpus = local_binary_root(TEST_SHA)
                project = ghidra_project_dir(corpus)
            self.assertEqual(project, Path(tmp) / "ghidra-project")

    def test_assert_ghidra_safe_path_rejects_dot_component(self) -> None:
        with self.assertRaises(ReError):
            assert_ghidra_safe_path(Path("/foo/.local/bar/ghidra-project"))

    def test_assert_ghidra_safe_path_allows_home_ghidra(self) -> None:
        path = Path.home() / "GhidraEU4" / binary_sha256() / "ghidra-project"
        assert_ghidra_safe_path(path)


if __name__ == "__main__":
    unittest.main()
