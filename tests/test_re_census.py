import unittest
from pathlib import Path

from reverse_engineering.census import build_census, classify_function, coupling_stats

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

    def test_coupling_counts_stub_and_direct(self) -> None:
        edges = [
            {
                "caller_rva": "0x2000",
                "callee_external_symbol": "_glDrawElements",
                "resolution": "external_via_stub",
            },
            {
                "caller_rva": "0x1000",
                "callee_external_symbol": "_glUseProgram",
                "resolution": "direct_external",
            },
        ]
        stats = coupling_stats(edges)
        self.assertEqual(stats["internal_functions_opengl_via_stub"], 1)
        self.assertEqual(stats["internal_functions_direct_opengl"], 1)

    def test_build_census_fixture(self) -> None:
        census = build_census(FIXTURE)
        self.assertIn("F_all_bytes", census["coverage"])
        self.assertIn("by_subsystem", census["renderer_subsystems"])


if __name__ == "__main__":
    unittest.main()
