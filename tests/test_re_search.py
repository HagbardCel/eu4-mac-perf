import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reverse_engineering import index_db

FIXTURE = Path(__file__).resolve().parent / "re_fixtures" / "export_mini"


class SearchIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "index.sqlite"
        index_db.rebuild_index(self.db, FIXTURE / "inventory", FIXTURE / "decompiled")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_symbol_search(self) -> None:
        conn = index_db.connect(self.db)
        rows = index_db.search_symbol(conn, "SShaderOpenGL")
        self.assertEqual(len(rows), 1)
        self.assertIn("SetAll", rows[0]["name_demangled"])

    def test_text_search(self) -> None:
        conn = index_db.connect(self.db)
        rows = index_db.search_text(conn, "glUseProgram")
        self.assertGreaterEqual(len(rows), 1)

    def test_callers_and_callees(self) -> None:
        conn = index_db.connect(self.db)
        callees = list(index_db.callees(conn, "0x0000000000002000"))
        self.assertTrue(callees)
        self.assertEqual(callees[0]["resolution"], "thunk")

    def test_missing_manifest_batch_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(FIXTURE / "inventory", root / "inventory")
            decomp = root / "decompiled"
            shutil.copytree(FIXTURE / "decompiled", decomp)
            manifest = json.loads((decomp / "batch_manifest.json").read_text())
            (decomp / "batch-00001.jsonl").unlink()
            db = root / "index.sqlite"
            index_db.rebuild_index(db, root / "inventory", decomp, manifest=manifest)
            conn = index_db.connect(db)
            rows = index_db.search_text(conn, "glUseProgram")
            self.assertEqual(len(rows), 0)


if __name__ == "__main__":
    unittest.main()
