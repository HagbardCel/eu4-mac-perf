import unittest
from pathlib import Path
from unittest.mock import patch

from reverse_engineering.census import (
    build_census,
    classify_function,
    coupling_stats,
    edges_by_caller,
    subsystem_metric_eligible,
)

FIXTURE = Path(__file__).resolve().parent / "re_fixtures" / "export_mini"


class CensusTests(unittest.TestCase):
    def test_classify_positive_opengl_evidence(self) -> None:
        row = {
            "entry_rva": "0x1000",
            "kind": "internal",
            "name_mangled": "__ZN13SShaderOpenGL6SetAllEv",
            "name_demangled": "SShaderOpenGL::SetAll()",
            "namespace": "",
        }
        edges = [
            {
                "caller_rva": "0x1000",
                "callee_external_symbol": "_glUseProgram",
                "resolution": "direct_external",
            }
        ]
        cls = classify_function(row, edges, "glUseProgram(1);")
        self.assertEqual(cls["primary_subsystem"], "B0")
        self.assertIn("direct_opengl_caller", cls["tags"])

    def test_pseudocode_gl_does_not_set_b0(self) -> None:
        row = {
            "entry_rva": "0x4000",
            "kind": "internal",
            "name_mangled": "__ZN4util6mallocEm",
            "name_demangled": "util::malloc(unsigned long)",
            "namespace": "",
        }
        cls = classify_function(row, [], "glUseProgram(1);")
        self.assertEqual(cls["primary_subsystem"], "U")

    def test_callee_alone_not_graphics(self) -> None:
        row = {
            "entry_rva": "0x4000",
            "kind": "internal",
            "name_mangled": "__ZN4util6mallocEm",
            "name_demangled": "util::malloc(unsigned long)",
            "namespace": "",
        }
        cls = classify_function(row, [], None)
        self.assertEqual(cls["primary_subsystem"], "U")

    def test_csingleton_not_classified_b0(self) -> None:
        row = {
            "entry_rva": "0x5000",
            "kind": "internal",
            "name_mangled": "Get",
            "name_demangled": "CSingleton::Get",
            "namespace": "CSingleton",
        }
        cls = classify_function(row, [], None)
        self.assertNotEqual(cls["primary_subsystem"], "B0")

    def test_global_init_not_opengl_coupling(self) -> None:
        edges = [
            {
                "caller_rva": "0x6000",
                "callee_external_symbol": "_global_init",
                "resolution": "direct_external",
            }
        ]
        stats = coupling_stats(edges)
        self.assertEqual(stats["internal_functions_direct_opengl"], 0)
        self.assertEqual(stats["internal_functions_opengl_via_stub"], 0)
        self.assertEqual(stats["internal_functions_opengl_via_thunk"], 0)

    def test_coupling_counts_stub_direct_and_thunk(self) -> None:
        edges = [
            {
                "caller_rva": "0x2000",
                "callee_external_symbol": "stub",
                "ultimate_external_symbol": "_glDrawElements",
                "resolution": "external_via_stub",
            },
            {
                "caller_rva": "0x1000",
                "callee_external_symbol": "_glUseProgram",
                "resolution": "direct_external",
            },
            {
                "caller_rva": "0x3000",
                "callee_internal_rva": "0x5000",
                "ultimate_external_symbol": "_glBindTexture",
                "resolution": "thunk",
            },
        ]
        stats = coupling_stats(edges)
        self.assertEqual(stats["internal_functions_opengl_via_stub"], 1)
        self.assertEqual(stats["internal_functions_direct_opengl"], 1)
        self.assertEqual(stats["internal_functions_opengl_via_thunk"], 1)

    def test_caller_edge_tags_isolated(self) -> None:
        edges = [
            {
                "caller_rva": "0x1000",
                "callee_external_symbol": "_glUseProgram",
                "resolution": "direct_external",
            },
            {"caller_rva": "0x2000", "resolution": "unresolved_indirect"},
        ]
        by_caller = edges_by_caller(edges)
        row_a = {"entry_rva": "0x1000", "kind": "internal", "name_demangled": "a", "name_mangled": "a"}
        row_b = {"entry_rva": "0x2000", "kind": "internal", "name_demangled": "b", "name_mangled": "b"}
        cls_a = classify_function(row_a, by_caller["0x1000"], None)
        cls_b = classify_function(row_b, by_caller["0x2000"], None)
        self.assertIn("direct_opengl_caller", cls_a["tags"])
        self.assertNotIn("unresolved_indirect_callsite", cls_a["tags"])
        self.assertIn("unresolved_indirect_callsite", cls_b["tags"])
        self.assertNotIn("direct_opengl_caller", cls_b["tags"])

    def test_build_census_passes_only_caller_local_edges(self) -> None:
        with patch("reverse_engineering.census.classify_function", wraps=classify_function) as spy:
            build_census(FIXTURE)
        self.assertGreater(spy.call_count, 0)
        for call in spy.call_args_list:
            row, fn_edges, _ = call.args
            self.assertTrue(all(e["caller_rva"] == row["entry_rva"] for e in fn_edges))

    def test_map_border_layer_b2_and_metric_eligible(self) -> None:
        row = {
            "entry_rva": "0x00000000010cbb00",
            "kind": "internal",
            "name_demangled": "CPdxMapBorderLayer::DrawBorders(...)",
            "name_mangled": "DrawBorders",
            "namespace": "CPdxMapBorderLayer",
        }
        cls = classify_function(row, [], None)
        self.assertEqual(cls["primary_subsystem"], "B2")
        self.assertEqual(cls["confidence"], "probable")
        self.assertTrue(subsystem_metric_eligible(row, cls))

    def test_subsystem_counts_match_eligible_set(self) -> None:
        from reverse_engineering.census import subsystem_bytes

        functions = [
            {
                "entry_rva": "0x1000",
                "kind": "internal",
                "name_mangled": "__ZN13SShaderOpenGL6SetAllEv",
                "instruction_ranges": [{"start_rva": "0x1000", "end_rva": "0x1010"}],
            },
            {
                "entry_rva": "0x2000",
                "kind": "internal",
                "name_mangled": "util",
                "instruction_ranges": [{"start_rva": "0x2000", "end_rva": "0x2005"}],
            },
        ]
        classifications = {
            "0x1000": {"primary_subsystem": "B0", "confidence": "confirmed", "tags": []},
            "0x2000": {"primary_subsystem": "U", "confidence": "possible", "tags": []},
        }
        sizes = subsystem_bytes(functions, classifications)
        self.assertEqual(sizes["by_subsystem"]["B0"]["functions"], 1)

    def test_build_census_fixture(self) -> None:
        census = build_census(FIXTURE)
        self.assertIn("F_all_bytes", census["coverage"])
        self.assertIn("by_subsystem", census["renderer_subsystems"])


if __name__ == "__main__":
    unittest.main()
