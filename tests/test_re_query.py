"""Tests for the structured reverse-engineering query (PR 5)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reverse_engineering import index_db, re_query  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "re_fixtures" / "export_mini"


class QueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        cls.index = Path(cls._temporary.name) / "index.sqlite"
        index_db.rebuild_index(cls.index, FIXTURE / "inventory", FIXTURE / "decompiled")

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def run_query(self, function, *args, **kwargs):
        with mock.patch.dict(os.environ, {"EU4_RE_INDEX": str(self.index)}):
            return function(*args, **kwargs)

    def test_resolve_by_name(self):
        result = self.run_query(re_query.query_resolve, "SShaderOpenGL")
        self.assertEqual(result["schema"], re_query.SCHEMA)
        self.assertEqual(result["status"], "ok")
        self.assertIn("0x1000", {row["entry_rva"] for row in result["result"]["matches"]})

    def test_function_by_rva_includes_pseudocode(self):
        result = self.run_query(re_query.query_function, "0x2000")
        self.assertEqual(result["status"], "ok")
        self.assertIn("Flush", result["result"]["name_demangled"])
        self.assertIn("indirect_call", result["result"]["pseudocode"])

    def test_function_by_name(self):
        result = self.run_query(re_query.query_function, "SShaderOpenGL")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["entry_rva"], "0x1000")

    def test_callers_and_callees(self):
        callers = self.run_query(re_query.query_edges, "0x3000", direction="callers")
        self.assertEqual(callers["status"], "ok")
        self.assertEqual(callers["result"]["edges"][0]["caller_rva"], "0x2000")
        callees = self.run_query(re_query.query_edges, "0x2000", direction="callees")
        self.assertEqual(callees["result"]["edges"][0]["callee_rva"], "0x3000")

    def test_xrefs_are_explicitly_partial(self):
        result = self.run_query(re_query.query_xrefs, "0x3000")
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["result"]["limitations"])

    def test_decompile_operation(self):
        result = self.run_query(re_query.query_function, "0x1000", max_bytes=24)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["result"]["pseudocode"]), 24)

    def test_abi_without_snapshot_is_unavailable(self):
        result = self.run_query(re_query.query_abi, "0x1000")
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("Ghidra", result["reason"])

    def test_struct_returns_a_tracked_layout(self):
        result = re_query.query_struct("mesh-draw")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["layouts"][0]["name"],
                         "mesh-draw-subrecord-0xe8-layout")
        self.assertEqual(result["result"]["layouts"][0]["layout"]["schema_version"], 1)

    def test_missing_index_is_unavailable(self):
        with mock.patch.dict(os.environ, {"EU4_RE_INDEX": str(self.index) + ".missing"}):
            result = re_query.query_resolve("anything")
        self.assertEqual(result["status"], "unavailable")

    def test_provenance_is_reported(self):
        result = self.run_query(re_query.query_provenance)
        self.assertEqual(result["status"], "ok")
        self.assertIn("provenance", result["result"])


if __name__ == "__main__":
    unittest.main()
