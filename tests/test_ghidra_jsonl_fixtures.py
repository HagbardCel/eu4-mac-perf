import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.graphics_agent import ghidra_queries
from reverse_engineering.paths import ReError

FIXTURE_DIR = Path(__file__).resolve().parent / "re_fixtures" / "ghidra"


class GhidraJsonlFixtureTests(unittest.TestCase):
    def test_synthetic_disassembly_validates(self) -> None:
        path = FIXTURE_DIR / "disassembly_ok.jsonl"
        rows = ghidra_queries.parse_jsonl_file(path)
        self.assertEqual(len(rows), 1)
        ghidra_queries._validate_jsonl_rows(rows, "disassembly", ["0x0000000000005000"])

    def test_wrong_operation_rejected(self) -> None:
        path = FIXTURE_DIR / "wrong_operation.jsonl"
        rows = ghidra_queries.parse_jsonl_file(path)
        with self.assertRaises(ReError):
            ghidra_queries._validate_jsonl_rows(rows, "disassembly", ["0x0000000000005000"])

    def test_malformed_json_line_fails_parse(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"ok":true}\nnot-json\n')
            bad = Path(f.name)
        try:
            with self.assertRaises(json.JSONDecodeError):
                ghidra_queries.parse_jsonl_file(bad)
        finally:
            bad.unlink()

    def test_validate_stored_artifact_requires_meta_hash(self) -> None:
        identity = {
            "ghidra_version": "fixture",
            "project_name": "fixture",
            "project_meta_hash": hashlib.sha256(b"fixture").hexdigest(),
            "program_name": "fixture",
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.jsonl"
            meta = Path(tmp) / "out.meta.json"
            out.write_text((FIXTURE_DIR / "disassembly_ok.jsonl").read_text())
            digest = hashlib.sha256(out.read_bytes()).hexdigest()
            ghidra_queries.write_meta(meta, identity, digest)
            rows = ghidra_queries.validate_stored_ghidra_artifact(
                out, meta, identity, "disassembly", ["0x0000000000005000"], digest
            )
            self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
