import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))
from mesh_record_layout import export_tables, load_layout  # noqa: E402
from mesh_record_layout_tables import C_TABLES  # noqa: E402


class MeshRecordLayoutTests(unittest.TestCase):
    def test_layouts_cover_full_records(self):
        for name in ("sflushdata_0x50", "mesh_draw_subrecord_0xe8"):
            doc, ranges = load_layout(name)
            self.assertEqual(sum(item["size"] for item in ranges), doc["record_size_bytes"])

    def test_c_tables_match_json(self):
        exported = export_tables()
        for key, table in exported.items():
            expected = [
                (item["offset"], item["size"], item["comparison_policy"])
                for item in table["ranges"]
            ]
            self.assertEqual(expected, list(C_TABLES[key]))

    def test_compile_and_run_equivalence_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "libsubrecord_equivalence.so"
            source = ROOT / "benchmark/subrecord_equivalence.c"
            cmd = ["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-o", str(lib), str(source)]
            subprocess.run(cmd, check=True)
            self.assertTrue(lib.is_file())


if __name__ == "__main__":
    unittest.main()
