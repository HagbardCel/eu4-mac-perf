import json
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.call_graph import (
    ENDPOINT_STATUS_DIRECT_BYPASS,
    ENDPOINT_STATUS_INDETERMINATE,
    load_call_graph,
)
from reverse_engineering.metal_census import (
    build_census,
    check_classification,
    classify_static_site,
    publish_census,
    site_instruction,
    trace_call_operand,
)
from reverse_engineering.paths import ReError


def _fn(rva: str, name: str, kind: str = "internal") -> dict:
    return {
        "entry_rva": rva,
        "kind": kind,
        "name_demangled": name,
        "name_mangled": name,
        "instruction_count": 4,
        "decoded_instruction_bytes": 8,
    }


def _edge(caller: str, site: str, resolution: str, internal: str | None = None, symbol: str | None = None) -> dict:
    return {
        "caller_rva": caller,
        "callsite_rva": site,
        "callee_internal_rva": internal,
        "callee_external_symbol": symbol,
        "ultimate_external_symbol": symbol,
        "resolution": resolution,
    }


SCAFFOLD = [
    _fn("0x1000", "CInGameIdler::Render"),
    _fn("0x1100", "CEU3GraphicalMap::Render"),
    _fn("0x1200", "CPdxMeshObject::RenderBuckets"),
    _fn("0x1300", "CApplication::CreateApplicationWindow"),
    _fn("0x1400", "ShutdownCaller"),
    _fn("0x1500", "_GfxShutdown"),
    _fn("0x1600", "_GfxDraw"),
]
SCAFFOLD_EDGES = [
    _edge("0x1400", "0x1401", "direct_internal", internal="0x1500"),
]


def _graph(functions: list[dict], edges: list[dict]):
    with tempfile.TemporaryDirectory() as tmp:
        inv = Path(tmp) / "inventory"
        inv.mkdir()
        (inv / "functions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in functions))
        (inv / "call_edges.jsonl").write_text("".join(json.dumps(row) + "\n" for row in edges))
        (inv / "coverage.json").write_text("{}\n")
        return load_call_graph(inv)


def _census(functions: list[dict], edges: list[dict], scope: list[dict] | None = None):
    graph = _graph(SCAFFOLD + functions, SCAFFOLD_EDGES + edges)
    return build_census(graph, scope_rows=scope or [], thunk_symbols=["_glDrawElements"])


class MetalCensusTests(unittest.TestCase):
    def test_in_cut_caller_is_intercepted(self) -> None:
        bundle = _census(
            [],
            [
                _edge("0x1000", "0x1001", "direct_internal", internal="0x1600"),
                _edge("0x1600", "0x1601", "thunk", symbol="_glDrawElements"),
            ],
        )
        self.assertEqual(bundle["boundary_verdict"], "compact_gfx_boundary")
        self.assertEqual(bundle["bypasses"], [])
        self.assertEqual(bundle["direct_gl_caller_count"], 1)
        self.assertEqual(bundle["direct_gl_in_gfx"], 1)

    def test_transitive_helper_is_behind_cut(self) -> None:
        functions = [
            _fn("0x2100", "HelperA"),
            _fn("0x2200", "HelperB"),
            _fn("0x2300", "_GfxSetShader"),
        ]
        edges = [
            _edge("0x1000", "0x1001", "direct_internal", internal="0x2300"),
            _edge("0x2300", "0x2301", "direct_internal", internal="0x2100"),
            _edge("0x2100", "0x2101", "direct_internal", internal="0x2200"),
            _edge("0x2200", "0x2201", "thunk", symbol="_glUseProgram"),
        ]
        bundle = _census(functions, edges)
        labels = {row["name"]: row["known_root"] for row in bundle["helpers"]}
        self.assertEqual(labels["HelperA"], "behind_cut")
        self.assertEqual(labels["HelperB"], "behind_cut")
        self.assertEqual(bundle["bypasses"], [])

    def test_shared_helper_is_not_exclusive(self) -> None:
        functions = [_fn("0x2100", "Helper"), _fn("0x2300", "_GfxSetShader")]
        edges = [
            _edge("0x1000", "0x1001", "direct_internal", internal="0x2300"),
            _edge("0x2300", "0x2301", "direct_internal", internal="0x2100"),
            _edge("0x1000", "0x1002", "direct_internal", internal="0x2100"),
            _edge("0x2100", "0x2101", "thunk", symbol="_glUseProgram"),
        ]
        bundle = _census(functions, edges)
        helper = next(row for row in bundle["helpers"] if row["name"] == "Helper")
        self.assertEqual(helper["known_root"], "shared")
        self.assertTrue(bundle["bypasses"])

    def test_direct_bypass_uses_direct_adj(self) -> None:
        bundle = _census([], [_edge("0x1000", "0x1001", "direct_external", symbol="_glDrawElements")])
        self.assertEqual(bundle["bypasses"][0]["status"], ENDPOINT_STATUS_DIRECT_BYPASS)
        self.assertEqual(bundle["boundary_verdict"], "dispersed_backend")

    def test_thunk_survivor_is_not_a_clean_miss(self) -> None:
        bundle = _census([], [_edge("0x1000", "0x1001", "thunk", symbol="_glDrawElements")])
        self.assertEqual(bundle["bypasses"][0]["status"], ENDPOINT_STATUS_INDETERMINATE)
        self.assertTrue(bundle["bypasses"][0]["established"])
        self.assertNotEqual(bundle["bypasses"][0]["status"], "intercepted_in_recovered_graph")

    def test_in_cut_indirect_site_does_not_flip_boundary(self) -> None:
        scope = [
            {
                "entry_rva": "0x1600",
                "name_demangled": "_GfxDraw",
                "name_mangled": "_GfxDraw",
                "missing_indirect_sites": ["0x1602"],
            }
        ]
        bundle = _census(
            [],
            [
                _edge("0x1000", "0x1001", "direct_internal", internal="0x1600"),
                _edge("0x1600", "0x1601", "thunk", symbol="_glDrawElements"),
            ],
            scope,
        )
        self.assertEqual(bundle["boundary_verdict"], "compact_gfx_boundary")
        coverage = bundle["indirect_dispatch_coverage"]
        self.assertEqual(coverage["selected_sites"], 1)
        self.assertEqual(coverage["unresolved"], 1)
        self.assertEqual(coverage["confirmed_graphics_dispatch"], 0)

    def test_adapter_path_is_not_a_bypass(self) -> None:
        functions = [_fn("0x1700", "_SDL_GL_SetAttribute")]
        edges = [_edge("0x1300", "0x1301", "direct_internal", internal="0x1700")]
        bundle = _census(functions, edges)
        self.assertEqual(bundle["bypasses"], [])
        self.assertEqual(bundle["platform_gaps"]["adapter_path"][0]["callsite_rva"], "0x1301")
        self.assertEqual(bundle["boundary_verdict"], "compact_gfx_boundary")

    def test_unrooted_gl_caller_is_not_behind_cut(self) -> None:
        functions = [_fn("0x1800", "LoneGL")]
        edges = [
            _edge("0x1000", "0x1001", "direct_internal", internal="0x1600"),
            _edge("0x1600", "0x1601", "thunk", symbol="_glDrawElements"),
            _edge("0x1800", "0x1801", "thunk", symbol="_glGetString"),
        ]
        bundle = _census(functions, edges)
        unrooted = {row["name"] for row in bundle["unrooted_in_selected_graph"]}
        self.assertIn("LoneGL", unrooted)
        self.assertNotIn("LoneGL", {row["name"] for row in bundle["helpers"] if row["known_root"] == "behind_cut"})
        self.assertTrue(any(row["name"] == "LoneGL" for row in bundle["outside_gfx_gl_callers"]))

    def test_digest_mismatch_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with self.assertRaises(ReError):
                publish_census(out, {}, computed_digest="aa", expected_digest="bb")
            self.assertEqual(list(out.iterdir()), [])

    def test_census_is_deterministic(self) -> None:
        edges = [
            _edge("0x1000", "0x1001", "direct_internal", internal="0x1600"),
            _edge("0x1600", "0x1601", "thunk", symbol="_glDrawElements"),
        ]
        first = _census([], edges)
        second = _census([], edges)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_nearby_pointer_is_not_confirmed(self) -> None:
        instructions = [
            {
                "instruction_rva": "0x10",
                "mnemonic": "CALL",
                "operands": "CALL 0x20",
                "pcode_is_call": True,
                "flow_type_is_call": True,
                "references_from": [{"ref_type": "UNCONDITIONAL_CALL", "target_rva": "0x20"}],
            },
            {
                "instruction_rva": "0x11",
                "mnemonic": "CALL",
                "operands": "CALL qword ptr [RBX]",
                "pcode_is_call": True,
                "flow_type_is_call": True,
                "references_from": [],
            },
        ]
        traced = trace_call_operand(instructions, 1)
        self.assertNotEqual(traced["classification"], "confirmed_graphics_dispatch")
        self.assertEqual(traced["classification"], "unresolved")

    def test_missing_evidence_rva_is_rejected(self) -> None:
        with self.assertRaises(ReError):
            check_classification(
                {
                    "classification": "bounded_candidates",
                    "target_evidence": ["0x99"],
                    "reviewed": False,
                },
                {"0x10"},
            )

    def test_non_call_instruction_is_rejected(self) -> None:
        row = {
            "entry_rva": "0x1600",
            "instructions": [
                {
                    "instruction_rva": "0x1602",
                    "mnemonic": "MOV",
                    "operands": "MOV EAX, 1",
                    "pcode_is_call": False,
                    "flow_type_is_call": False,
                    "references_from": [],
                }
            ],
        }
        with self.assertRaises(ReError):
            site_instruction(row, "0x1602")

    def test_traced_slot_is_a_candidate_not_confirmed(self) -> None:
        static = {
            "entry_rva": "0x1600",
            "instructions": [
                {
                    "instruction_rva": "0x1601",
                    "mnemonic": "LEA",
                    "operands": "LEA RAX,[0x102252010]",
                    "pcode_is_call": False,
                    "flow_type_is_call": False,
                    "references_from": [{"ref_type": "DATA", "target_rva": "0x2252010"}],
                },
                {
                    "instruction_rva": "0x1602",
                    "mnemonic": "CALL",
                    "operands": "CALL qword ptr [RAX]",
                    "pcode_is_call": True,
                    "flow_type_is_call": True,
                    "references_from": [{"ref_type": "READ", "target_rva": "0x2252010"}],
                },
            ],
        }
        row = classify_static_site(static, "0x1602", prior=None)
        self.assertEqual(row["classification"], "bounded_candidates")
        self.assertEqual(row["mechanism_id"], "slot_loaded_register")
        self.assertIn("0x0000000002252010", row["target_evidence"])
        self.assertFalse(row["reviewed"])


if __name__ == "__main__":
    unittest.main()
