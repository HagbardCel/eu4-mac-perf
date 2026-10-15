import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.callsite_audit import (
    build_audit_report,
    build_graphics_section,
    canonical_json_bytes,
    canonical_jsonl_bytes,
    coverage_bucket,
    publish_audits,
    reconcile_callsites,
    validate_census,
    validate_scan_meta,
)
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
) -> dict:
    return {
        "callsite_rva": rva,
        "caller_rva": caller,
        "mnemonic": "CALL",
        "pcode_kind": "CALL" if iclass == "direct" else "CALLIND",
        "flow_type_is_call": True,
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

    def test_classifier_disagreement_orthogonal(self) -> None:
        callsites = [
            _row("0x0000000000005001", "direct", status="disagreement"),
        ]
        edges = [{"callsite_rva": "0x0000000000005001"}]
        result = reconcile_callsites(callsites, edges)
        self.assertEqual(result["counts"]["matched"], 1)
        self.assertEqual(result["classifier_disagreement_count"], 1)

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
            audits = Path(tmp) / "audits"
            a = b'{"x":1}\n'
            b = b'{"y":2}\n'
            publish_audits(a, b, audits)
            publish_audits(a, b, audits)
            self.assertTrue((audits / "callsite-audit.json").is_file())

    def test_publish_rejects_differing_audits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audits = Path(tmp) / "audits"
            publish_audits(b"a\n", b"b\n", audits)
            with self.assertRaises(ReError):
                publish_audits(b"c\n", b"d\n", audits)

    def test_deterministic_report_bytes(self) -> None:
        callsites = [_row("0x0000000000005001", "direct")]
        recon = reconcile_callsites(callsites, [])
        prov = {"baseline_revision": "rev", "inventory_bundle_digest": "abc"}
        meta = _scan_meta(1)
        gfx = {"mandatory_anchors": [], "gfx_helpers": [], "top_missing_by_function": []}
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
        self.assertEqual(
            canonical_jsonl_bytes(callsites),
            canonical_jsonl_bytes(callsites),
        )

    def test_graphics_mandatory_anchors_from_graph_mini(self) -> None:
        callsites = [
            _row("0x0000000000005001", "direct", caller="0x0000000000005000"),
            _row("0x0000000000009001", "indirect", caller="0x0000000000009000"),
        ]
        edges = [
            json.loads(line)
            for line in GRAPH_MINI.joinpath("call_edges.jsonl").read_text().splitlines()
            if line.strip()
        ]
        functions = {
            json.loads(line)["entry_rva"]: json.loads(line)
            for line in GRAPH_MINI.joinpath("functions.jsonl").read_text().splitlines()
            if line.strip()
        }
        gfx = build_graphics_section(callsites, edges, functions)
        self.assertGreaterEqual(len(gfx["mandatory_anchors"]), 3)
        names = {a["name_mangled"] for a in gfx["mandatory_anchors"]}
        self.assertIn("__ZN13SShaderOpenGL6SetAllEv", names)

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
                canonical_jsonl_bytes(callsites),
                canonical_json_bytes(report),
                exp / "audits",
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
