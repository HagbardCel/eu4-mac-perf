import ctypes
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))
from codegen_subrecord_ranges import OUT, generate  # noqa: E402
from mesh_record_layout import export_tables, load_layout  # noqa: E402
from mesh_record_layout_tables import C_TABLES  # noqa: E402

POLICY_TO_C = {"equal": 0, "ignore": 1, "barrier": 2}


class MeshRecordLayoutTests(unittest.TestCase):
    def test_layouts_cover_full_records(self):
        for name in ("sflushdata_0x50", "mesh_draw_subrecord_0xe8"):
            doc, ranges = load_layout(name)
            self.assertEqual(sum(item["size"] for item in ranges), doc["record_size_bytes"])

    def test_codegen_inc_matches_json(self):
        generated = generate()
        on_disk = OUT.read_text(encoding="utf-8")
        self.assertEqual(generated, on_disk)

    def test_python_tables_match_json(self):
        exported = export_tables()
        for key, table in exported.items():
            expected = [
                (item["offset"], item["size"], item["comparison_policy"])
                for item in table["ranges"]
            ]
            self.assertEqual(expected, list(C_TABLES[key]))

    def test_compiled_c_tables_match_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "libsubrecord_equivalence.so"
            bench = ROOT / "benchmark"
            subprocess.run(
                [
                    "cc",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-shared",
                    "-fPIC",
                    "-I",
                    str(bench),
                    "-o",
                    str(lib),
                    str(bench / "subrecord_equivalence.c"),
                ],
                check=True,
            )
            libcdll = ctypes.CDLL(str(lib))

            class Range(ctypes.Structure):
                _fields_ = [
                    ("offset", ctypes.c_uint32),
                    ("size", ctypes.c_uint32),
                    ("policy", ctypes.c_int),
                ]

            for c_symbol, count_symbol, key in (
                ("eu4_sflushdata_ranges", "eu4_sflushdata_range_count", "sflushdata_0x50"),
                ("eu4_mesh_draw_ranges", "eu4_mesh_draw_range_count", "mesh_draw_subrecord_0xe8"),
            ):
                count = ctypes.c_size_t.in_dll(libcdll, count_symbol).value
                base = Range.in_dll(libcdll, c_symbol)
                arr_ptr = ctypes.cast(ctypes.addressof(base), ctypes.POINTER(Range))
                expected = C_TABLES[key]
                self.assertEqual(count, len(expected))
                for index, (offset, size, policy) in enumerate(expected):
                    item = arr_ptr[index]
                    self.assertEqual(item.offset, offset)
                    self.assertEqual(item.size, size)
                    self.assertEqual(item.policy, POLICY_TO_C[policy])


if __name__ == "__main__":
    unittest.main()
