import json
import unittest

from reverse_engineering import readiness
from reverse_engineering.paths import ReError, load_target

TARGET = load_target()
BASE = TARGET["image_base"]


def _node(address: int, *, edges=(), indirect=(), depth=0) -> dict:
    return {
        "address": address,
        "depth": depth,
        "aliases": [],
        "edges": [{"address": address + 1, "target": t, "kind": "call", "confidence": "direct symbolized instruction"}
                  for t in edges],
        "indirect_edges": [{"address": address + 2, "operand": op, "status": "unresolved"} for op in indirect],
        "gl_pointer_references": [],
    }


def _row(rva: int, name: str, **extra) -> dict:
    row = {
        "entry_rva": f"0x{rva:016x}", "name_demangled": name, "kind": "internal", "tier": "core",
        "instruction_count": 4, "census_callsites": 0, "flow_only_sites": 0,
        "missing_indirect_sites": [], "pcode_callind_sites": 0,
    }
    row.update(extra)
    return row


def _build(nodes: dict, rows: list[dict], *, unvisited=(), roots=None, packages=None) -> dict:
    for key, node in nodes.items():
        node["aliases"] = node["aliases"] or [key]
    static = {
        "executable_sha256": TARGET["sha256"],
        "graph": {"roots": roots or sorted(nodes), "nodes": nodes, "max_depth": 4, "capacity": 512,
                  "unvisited_at_bound": sorted(unvisited), "limits": []},
    }
    return readiness.build_shortlist(static, rows, packages or {}, TARGET)


def _synthetic(nodes: dict, rows: list[dict], **kwargs) -> dict:
    return {f["symbol"]: f for f in _build(nodes, rows, **kwargs)["functions"]}


class CommittedShortlistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shortlist = readiness.build_committed()
        cls.by_symbol = {f["symbol"]: f for f in cls.shortlist["functions"]}

    def test_rebuild_is_byte_identical(self):
        self.assertEqual(readiness.render(self.shortlist), readiness.OUTPUT.read_bytes())

    def test_primitive_type_helper_joins_by_address(self):
        helper = self.by_symbol["__Z22GetOpenGLPrimitiveType13EGFXPrimitive"]
        self.assertEqual(helper["rva"], "0x15edff7")
        self.assertEqual(helper["corpus_join"], "address")
        self.assertEqual(helper["corpus"]["entry_rva"], "0x00000000015edff7")
        self.assertEqual(helper["corpus"]["name_demangled"], "GetOpenGLPrimitiveType")
        caller = self.by_symbol["_GfxSetPrimitiveType"]
        self.assertEqual(
            [(c["status"], c["rva"]) for c in caller["direct_callees"]], [("visited", "0x15edff7")]
        )

    def test_every_joined_function_matches_corpus_rva(self):
        for f in self.shortlist["functions"]:
            if f["corpus_join"] == "address":
                self.assertEqual(int(f["corpus"]["entry_rva"], 16), int(f["rva"], 16))
            else:
                self.assertIsNone(f["corpus"])
                self.assertNotEqual(f["label"], "ready-leaf")

    def test_ready_leaves_have_no_unresolved_or_unvisited_evidence(self):
        leaves = [f for f in self.shortlist["functions"] if f["label"] == "ready-leaf"]
        self.assertTrue(leaves)
        self.assertEqual(len(leaves), self.shortlist["counts"]["ready-leaf"])
        for f in leaves:
            self.assertEqual(f["blocking"], [], f["symbol"])
            self.assertEqual(f["indirect_sites"], [], f["symbol"])
            self.assertEqual(f["direct_callees"], [], f["symbol"])
            self.assertEqual(f["corpus"]["flow_only_sites"], 0, f["symbol"])
        unvisited = [f for f in self.shortlist["functions"]
                     if any(c["status"] != "visited" for c in f["direct_callees"]) or f["indirect_sites"]]
        self.assertTrue(unvisited)
        self.assertTrue(all(f["label"] != "ready-leaf" for f in unvisited))

    def test_existing_packages_are_not_this_stage(self):
        for symbol, level in (("_GfxSetConstantBuffer", "RE2"), ("_GfxForceWireframe", "RE0")):
            f = self.by_symbol[symbol]
            self.assertEqual(f["label"], "not-this-stage")
            self.assertEqual(f["re_package"]["level"], level)

    def test_binary_sha_and_rva_recorded(self):
        for f in self.shortlist["functions"]:
            self.assertEqual(f["binary_sha256"], TARGET["sha256"])
            self.assertTrue(f["rva"].startswith("0x"))

    def test_external_stubs_stay_recorded_without_relabeling(self):
        self.assertEqual(self.shortlist["counts"],
                         {"blocked": 388, "not-this-stage": 8, "ready-leaf": 87})
        self.assertIn(readiness.EXTERNAL_STUB_LIMIT, self.shortlist["bounds"]["limits"])
        self.assertTrue(any(leaf["external_api_calls"] for leaf in self.shortlist["ready_leaves"]))


class SyntheticGraphTests(unittest.TestCase):
    def test_unvisited_or_unresolved_edge_is_never_ready_leaf(self):
        nodes = {
            "leaf": _node(BASE + 0x100),
            "to_unvisited": _node(BASE + 0x200, edges=["far"]),
            "indirect": _node(BASE + 0x300, indirect=["*0x10(%rax)"]),
            "bad_stub": _node(BASE + 0x400, edges=["dyld_stub_binder+0x10"]),
            "caller": _node(BASE + 0x500, edges=["to_unvisited"]),
            "stub_leaf": _node(BASE + 0x600, edges=[f"dyld_stub_binder+0x{BASE + 0x9000:x}"]),
            "tail": _node(BASE + 0x700),
        }
        rows = [_row(n["address"] - BASE, k) for k, n in nodes.items()]
        rows[-1]["flow_only_sites"] = 1
        rows[-2]["census_callsites"] = 1
        out = _synthetic(nodes, rows, unvisited={"far"})
        self.assertEqual(out["leaf"]["label"], "ready-leaf")
        self.assertEqual(out["stub_leaf"]["label"], "ready-leaf")
        self.assertEqual(out["stub_leaf"]["external_api_calls"][0]["stub_va"], f"0x{BASE + 0x9000:x}")
        for key, reason in (
            ("to_unvisited", "edge_to_unvisited_function"),
            ("indirect", "unresolved_indirect_site"),
            ("bad_stub", "unresolved_call_target"),
            ("caller", "callee_blocked"),
            ("tail", "corpus_call_sites_not_in_static_graph"),
        ):
            self.assertEqual(out[key]["label"], "blocked", key)
            self.assertIn(reason, out[key]["blocking"], key)

    def test_name_match_at_other_address_is_unresolved(self):
        nodes = {"GetOpenGLPrimitiveType": _node(BASE + 0x100)}
        out = _synthetic(nodes, [_row(0x104, "GetOpenGLPrimitiveType")])
        f = out["GetOpenGLPrimitiveType"]
        self.assertEqual(f["corpus_join"], "unresolved")
        self.assertIsNone(f["corpus"])
        self.assertEqual(f["label"], "blocked")

    def test_cycle_is_collapsed_and_not_a_leaf(self):
        nodes = {
            "a": _node(BASE + 0x100, edges=["b"]),
            "b": _node(BASE + 0x200, edges=["a", "c"]),
            "c": _node(BASE + 0x300),
        }
        rows = [_row(n["address"] - BASE, k, census_callsites=len(n["edges"])) for k, n in nodes.items()]
        shortlist = _build(nodes, rows, roots=["a"])
        self.assertEqual(shortlist["cycles"], [["0x100", "0x200"]])
        labels = {f["symbol"]: f["label"] for f in shortlist["functions"]}
        self.assertEqual(labels, {"a": "not-this-stage", "b": "not-this-stage", "c": "ready-leaf"})

    def test_ambiguous_alias_is_unresolved_everywhere(self) -> None:
        nodes = {
            "left": _node(BASE + 0x100),
            "right": _node(BASE + 0x200),
            "caller": _node(BASE + 0x300, edges=["shared"]),
        }
        nodes["left"]["aliases"] = ["shared"]
        nodes["right"]["aliases"] = ["shared"]
        rows = [_row(node["address"] - BASE, key) for key, node in nodes.items()]
        shortlist = _build(nodes, rows, roots=["caller", "shared"])
        symbols = {item["symbol"] for item in shortlist["functions"]}
        self.assertEqual(symbols, {"caller"})
        self.assertIn("shared", shortlist["unresolved_anchors"])
        callee = shortlist["functions"][0]["direct_callees"]
        self.assertEqual(callee, [{"rva": None, "sites": 1, "status": "unresolved", "symbol": "shared"}])
        self.assertEqual(shortlist["functions"][0]["label"], "blocked")

    def test_cycle_member_inherits_block(self):
        nodes = {"a": _node(BASE + 0x100, edges=["b"]), "b": _node(BASE + 0x200, edges=["a"])}
        rows = [_row(0x100, "a", census_callsites=1)]
        out = _synthetic(nodes, rows)
        self.assertEqual(out["a"]["label"], "blocked")
        self.assertEqual(out["b"]["label"], "blocked")

    def test_package_detection_wins(self):
        nodes = {"_GfxForceWireframe": _node(BASE + 0x100)}
        out = _synthetic(nodes, [_row(0x100, "x")], packages={0x100: {"path": "p", "level": "RE0"}})
        self.assertEqual(out["_GfxForceWireframe"]["label"], "not-this-stage")

    def test_binary_mismatch_is_contract_conflict(self):
        static = {"executable_sha256": "0" * 64, "graph": {}}
        with self.assertRaises(ReError):
            readiness.build_shortlist(static, [], {}, TARGET)


class TarjanTests(unittest.TestCase):
    def test_collapses_cycle_callees_first(self):
        succ = {"a": ["b"], "b": ["c", "a"], "c": ["c"], "d": []}
        self.assertEqual(readiness.tarjan(["a", "b", "c", "d"], succ), [["c"], ["a", "b"], ["d"]])


if __name__ == "__main__":
    unittest.main()
