import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tooling.common.check_doc_links import (
    check_file,
    extract_links,
    resolve_strict_inputs,
    repo_root,
)

REPO_ROOT = repo_root()


class CheckDocLinksTests(unittest.TestCase):
    def test_extract_skips_http_and_fragment_only(self) -> None:
        text = "[a](https://x.com) [b](#foo) [c](other.md)"
        links = extract_links(Path("x.md"), text)
        self.assertEqual(links, [(1, "other.md")])

    def test_check_file_reports_missing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md = root / "a.md"
            md.write_text("[bad](missing.md)\n", encoding="utf-8")
            errors = check_file(md, root)
            self.assertEqual(len(errors), 1)
            self.assertIn("missing path:", errors[0])
            self.assertIn("missing.md", errors[0])

    def test_check_file_resolves_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.md").write_text("ok\n", encoding="utf-8")
            md = root / "a.md"
            md.write_text("[ok](b.md)\n", encoding="utf-8")
            self.assertEqual(check_file(md, root), [])

    def test_check_file_resolves_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "subdir").mkdir()
            md = root / "a.md"
            md.write_text("[dir](subdir)\n", encoding="utf-8")
            self.assertEqual(check_file(md, root), [])

    def test_resolve_strict_empty_fails(self) -> None:
        paths, errors = resolve_strict_inputs(REPO_ROOT, [])
        self.assertEqual(paths, [])
        self.assertTrue(errors)

    def test_resolve_strict_missing_file_fails(self) -> None:
        paths, errors = resolve_strict_inputs(REPO_ROOT, ["docs/this-file-does-not-exist.md"])
        self.assertEqual(paths, [])
        self.assertTrue(any("missing strict input" in e for e in errors))

    def test_resolve_strict_missing_directory_fails(self) -> None:
        paths, errors = resolve_strict_inputs(REPO_ROOT, ["docs/no-such-directory"])
        self.assertEqual(paths, [])
        self.assertTrue(any("missing strict input" in e for e in errors))

    def test_resolve_strict_non_markdown_file_fails(self) -> None:
        paths, errors = resolve_strict_inputs(REPO_ROOT, ["tooling/common/check_doc_links.py"])
        self.assertEqual(paths, [])
        self.assertTrue(any("unsupported strict input" in e for e in errors))

    def test_resolve_strict_directory_recurses_markdown(self) -> None:
        paths, errors = resolve_strict_inputs(REPO_ROOT, ["docs/findings"])
        self.assertFalse(errors)
        self.assertTrue(any(p.name == "README.md" for p in paths))

    def test_cli_strict_without_paths_fails(self) -> None:
        script = REPO_ROOT / "tooling/common/check_doc_links.py"
        proc = subprocess.run(
            [sys.executable, str(script), "--strict"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("at least one path", proc.stderr)


if __name__ == "__main__":
    unittest.main()
