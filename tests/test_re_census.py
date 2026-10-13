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
