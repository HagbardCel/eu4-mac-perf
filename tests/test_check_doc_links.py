import tempfile
import unittest
from pathlib import Path

from tooling.common.check_doc_links import check_file, extract_links


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
            self.assertIn("missing.md", errors[0])

    def test_check_file_resolves_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.md").write_text("ok\n", encoding="utf-8")
            md = root / "a.md"
            md.write_text("[ok](b.md)\n", encoding="utf-8")
            self.assertEqual(check_file(md, root), [])


if __name__ == "__main__":
    unittest.main()
