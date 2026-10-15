import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reverse_engineering.callsite_audit import (
    AUDITS_CALLSITES_FILE,
    AUDITS_REPORT_FILE,
    build_audit_report,
    build_graphics_section,
    canonical_json_bytes,
    publish_audits,
    reconcile_callsites,
    resolve_draw_borders_entry,
    run_ghidra_export,
    validate_census,
    coverage_bucket,
)
from reverse_engineering.gfx_subgraph import PINNED_BORDER_RVA
from reverse_engineering.manifest import inventory_bundle_digest
from reverse_engineering.paths import ReError

FIXTURE = Path(__file__).resolve().parent / "re_fixtures"
GRAPH_MINI = FIXTURE / "graph_mini" / "inventory"


def _scan_meta(total_callsites: int) -> dict:
    return {
        "total_instructions_scanned": 100,
        "total_callsites": total_callsites,
        "pcode_decode_failures": 0,
        "pcode_unimplemented_count": 0,
        "pcode_failure_sample_rvas": [],
        "executable_sha256": "aa" * 32,
        "program_name": "test",
        "image_base": 0,
        "exporter_status": "ok",
    }


def _row(
    rva: str,
    iclass: str,
    caller: str | None = "0x0000000000005000",
    status: str = "agreement",
    *,
    flow_type_is_call: bool = True,
    pcode_is_call: bool | None = None,
    classification_conflict: bool = False,
    flow_instruction_class: str | None = None,
) -> dict:
    if pcode_is_call is None:
        if status == "pcode_unavailable":
            pcode_is_call = False
        elif status == "agreement":
            pcode_is_call = flow_type_is_call
        else:
            pcode_is_call = not flow_type_is_call
    if flow_instruction_class is None and flow_type_is_call:
        flow_instruction_class = "indirect" if iclass == "indirect" else "direct"
    return {
        "callsite_rva": rva,
        "caller_rva": caller,
        "mnemonic": "CALL",
        "pcode_kind": "CALL" if iclass == "direct" else "CALLIND",
        "flow_type_is_call": flow_type_is_call,
        "pcode_is_call": pcode_is_call,
        "flow_instruction_class": flow_instruction_class,
        "classification_conflict": classification_conflict,
        "classifier_status": status,
        "instruction_class": iclass,
        "call_reference_count": 1,
    }


class CallsiteAuditTests(unittest.TestCase):
    def test_coverage_bucket_partition(self) -> None:
        callsites = [
            _row("0x0000000000005001", "direct"),
            _row("0x0000000000005002", "indirect"),
            _row("0x0000000000005003", "ambiguous"),
            _row("0x0000000000005004", "direct"),
        ]
        edges = [
            {"callsite_rva": "0x0000000000005001", "resolution": "direct_internal"},
        ]
        result = reconcile_callsites(callsites, edges)
        self.assertEqual(result["counts"]["matched"], 1)
        self.assertEqual(result["counts"]["missing_direct"], 1)
        self.assertEqual(result["counts"]["missing_indirect"], 1)
        self.assertEqual(result["counts"]["missing_ambiguous"], 1)
        self.assertEqual(result["total_callsites"], 4)

    def test_missing_ambiguous_explicit(self) -> None:
        bucket = coverage_bucket(_row("0x1", "ambiguous"), [])
        self.assertEqual(bucket, "missing_ambiguous")

    def test_orphan_edges(self) -> None:
        callsites = [_row("0x0000000000005001", "direct")]
        edges = [
            {"callsite_rva": "0x0000000000005001"},
            {"callsite_rva": "0x0000000000009999"},
        ]
        result = reconcile_callsites(callsites, edges)
        self.assertEqual(result["orphan_edge_callsite_count"], 1)
        self.assertIn("0x0000000000009999", result["orphan_edge_callsites"])

    def test_classifier_disagreement_partition(self) -> None:
        callsites = [
            _row(
                "0x0000000000005001",
                "direct",
                flow_type_is_call=False,
                pcode_is_call=True,
            ),
        ]
        edges = [{"callsite_rva": "0x0000000000005001"}]
        result = reconcile_callsites(callsites, edges)
        self.assertEqual(result["counts"]["matched"], 1)
        self.assertEqual(result["pcode_only_callsites"], 1)
        self.assertEqual(result["flow_only_callsites"], 0)
        self.assertEqual(result["classifier_disagreement_count"], 1)

    def test_pcode_unavailable_not_disagreement(self) -> None:
        callsites = [
            _row(
                "0x0000000000005002",
                "direct",
                status="pcode_unavailable",
                flow_type_is_call=True,
                pcode_is_call=False,
            ),
        ]
        result = reconcile_callsites(callsites, [])
        self.assertEqual(result["flow_with_pcode_unavailable"], 1)
        self.assertEqual(result["pcode_only_callsites"], 0)
        self.assertEqual(result["flow_only_callsites"], 0)
        self.assertEqual(result["classifier_disagreement_count"], 0)

    def test_validate_census_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "callsites-v1.jsonl"
            path.write_text(json.dumps(_row("0x0000000000005001", "direct")) + "\n")
            meta = _scan_meta(2)
            with self.assertRaises(ReError):
                validate_census(path, meta)

    def test_validate_duplicate_rva(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "callsites-v1.jsonl"
            line = json.dumps(_row("0x0000000000005001", "direct"))
            path.write_text(line + "\n" + line + "\n")
            meta = _scan_meta(2)
            with self.assertRaises(ReError):
                validate_census(path, meta)

    def test_publish_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = Path(tmp)
            a = b'{"x":1}\n'
            b = b'{"y":2}\n'
            publish_audits(exp, a, b)
            publish_audits(exp, a, b)
            self.assertTrue((exp / "audits" / AUDITS_REPORT_FILE).is_file())

    def test_publish_rejects_differing_audits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = Path(tmp)
            publish_audits(exp, b"a\n", b"b\n")
            with self.assertRaises(ReError):
                publish_audits(exp, b"c\n", b"d\n")

    def test_publish_rejects_incomplete_audits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = Path(tmp)
            incomplete = exp / "audits"
            incomplete.mkdir()
            (incomplete / AUDITS_CALLSITES_FILE).write_bytes(b"x\n")
            with self.assertRaises(ReError):
                publish_audits(exp, b"a\n", b"b\n")

    def test_deterministic_report_bytes(self) -> None:
        callsites = [_row("0x0000000000005001", "direct")]
        recon = reconcile_callsites(callsites, [])
        prov = {"baseline_revision": "rev", "inventory_bundle_digest": "abc"}
        meta = _scan_meta(1)
        gfx = {
            "mandatory_anchors": [],
            "gfx_helpers": [],
            "focused_renderers": [],
            "top_missing_by_function": [],
        }
        r1 = build_audit_report(
            provenance=prov,
            scan_meta=meta,
            callsites=callsites,
            reconciliation=recon,
            graphics=gfx,
            ghidra_meta={},
            pinned_sha="bb" * 32,
        )
        r2 = build_audit_report(
            provenance=prov,
            scan_meta=meta,
            callsites=callsites,
            reconciliation=recon,
            graphics=gfx,
            ghidra_meta={},
            pinned_sha="bb" * 32,
        )
        self.assertEqual(canonical_json_bytes(r1), canonical_json_bytes(r2))

    @mock.patch("reverse_engineering.callsite_audit.analyze_headless")
    def test_run_ghidra_export_invokes_headless(self, mock_headless: mock.Mock) -> None:
        staging = Path(tempfile.mkdtemp())
        meta = {
            "project_dir": "/proj",
            "project_name": "EU4_test",
            "program_name": "eu4",
        }
        run_ghidra_export(staging, meta)
        mock_headless.assert_called_once()
        args, kwargs = mock_headless.call_args
        self.assertEqual(kwargs, {})
        self.assertIn("ExportCallsites.java", args)

    def test_graphics_mandatory_anchors_from_graph_mini(self) -> None:
        callsites = [
            _row("0x0000000000005001", "direct", caller="0x0000000000005000"),
            _row("0x0000000000009001", "indirect", caller="0x0000000000009000"),
        ]
        edges = load_edges(GRAPH_MINI)
        functions = load_functions(GRAPH_MINI)
        gfx = build_graphics_section(callsites, edges, functions)
        self.assertGreaterEqual(len(gfx["mandatory_anchors"]), 3)
        names = {a["name_mangled"] for a in gfx["mandatory_anchors"]}
        self.assertIn("__ZN13SShaderOpenGL6SetAllEv", names)

    def test_draw_borders_pinned_identity_mismatch(self) -> None:
        functions = {
            PINNED_BORDER_RVA: {
                "entry_rva": PINNED_BORDER_RVA,
                "name_mangled": "wrong",
                "name_demangled": "Other::Fn",
            }
        }
        rva, warn = resolve_draw_borders_entry(functions)
        self.assertIsNone(rva)
        self.assertIsNotNone(warn)

    def test_draw_borders_in_focused_renderers(self) -> None:
        functions = dict(load_functions(GRAPH_MINI))
        functions[PINNED_BORDER_RVA] = {
            "entry_rva": PINNED_BORDER_RVA,
            "name_mangled": "DrawBorders",
            "name_demangled": "CPdxMapBorderLayer::DrawBorders",
            "namespace": "CPdxMapBorderLayer",
        }
        callsites = [_row("0x0000000000006001", "direct", caller=PINNED_BORDER_RVA)]
        gfx = build_graphics_section(callsites, [], functions)
        self.assertEqual(len(gfx["focused_renderers"]), 1)
        self.assertEqual(gfx["focused_renderers"][0]["entry_rva"], PINNED_BORDER_RVA)

    def test_graphics_missing_anchor_fails(self) -> None:
        functions = {"0x1": {"entry_rva": "0x1", "name_mangled": "other"}}
        with self.assertRaises(ReError):
            build_graphics_section([], [], functions)

    def test_digest_unchanged_publish_simulation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = Path(tmp) / "exports" / "rev"
            inv = exp / "inventory"
            shutil.copytree(GRAPH_MINI, inv)
            digest_before = inventory_bundle_digest(inv)
            provenance = {"inventory_bundle_digest": digest_before, "baseline_revision": "rev"}
            callsites = [_row("0x0000000000005001", "direct")]
            recon = reconcile_callsites(callsites, [])
            meta = _scan_meta(1)
            meta["executable_sha256"] = "cc" * 32
            gfx = build_graphics_section(
                callsites,
                load_edges(inv),
                load_functions(inv),
            )
            report = build_audit_report(
                provenance=provenance,
                scan_meta=meta,
                callsites=callsites,
                reconciliation=recon,
                graphics=gfx,
                ghidra_meta={},
                pinned_sha="cc" * 32,
            )
            publish_audits(
                exp,
                json.dumps(callsites[0], sort_keys=True).encode() + b"\n",
                canonical_json_bytes(report),
            )
            digest_after = inventory_bundle_digest(inv)
            self.assertEqual(digest_before, digest_after)


def load_edges(inv: Path) -> list:
    return [
        json.loads(line)
        for line in (inv / "call_edges.jsonl").read_text().splitlines()
        if line.strip()
    ]


def load_functions(inv: Path) -> dict:
    return {
        json.loads(line)["entry_rva"]: json.loads(line)
        for line in (inv / "functions.jsonl").read_text().splitlines()
        if line.strip()
    }


if __name__ == "__main__":
    unittest.main()
