#!/usr/bin/env python3
"""Portable tests for the L3.2a draw packet and draw capture reader."""

from __future__ import annotations

import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))
sys.path.insert(0, str(ROOT))

import eu4_gfx_draw_packet as packet_builder  # noqa: E402
import eu4_gfx_op_capture as capture  # noqa: E402
import automation.live_gfx as live_gfx  # noqa: E402

BASE_CAPTURE = ROOT / "analysis" / "gfx-ops" / "v0" / "capture"
DRAW_RVA = "0x15ea789"
CALLER = 0xEDFCD1


def _master(mode: int = 5, count: int = 4, shader: int = 0x1234) -> bytes:
    raw = bytearray(0x171)
    struct.pack_into("<i", raw, 0x08, mode)
    struct.pack_into("<i", raw, 0x64, count)
    struct.pack_into("<Q", raw, 0x70, shader)
    struct.pack_into("<i", raw, 0xA0, 800)
    struct.pack_into("<i", raw, 0xA4, 600)
    struct.pack_into("<i", raw, 0xAC, 600)
    struct.pack_into("<i", raw, 0xB0, 800)
    return bytes(raw)


VB_HEADER = 0x60000125A750
CB_HEADER = 0x60000125A580
VB_BYTES = bytes(range(96))
CB_BYTES = bytes(range(64))


def _full_master() -> bytes:
    raw = bytearray(_master())
    struct.pack_into("<I", raw, 0xC8, 4)
    struct.pack_into("<Q", raw, 0xD0, CB_HEADER)
    struct.pack_into("<Q", raw, 0x130, VB_HEADER)
    struct.pack_into("<I", raw, 0x150, 0)
    struct.pack_into("<I", raw, 0x160, 24)
    return bytes(raw)


def _resource_row(
    call_id: int, symbol: str, rva: str, **fields
) -> dict:
    row = {
        "schema": "gfx-op/v0",
        "call_id": call_id,
        "completion_seq": call_id - 2,
        "symbol": symbol,
        "rva": rva,
        "thread_id": 7,
        "caller_rva": CALLER,
        "caller_known": True,
        "overlap": False,
        "fields_ok": True,
        "output_base_non_null": False,
        "input_size": 0,
        "output_size": 0,
        "input_hex": "",
        "output_hex": "",
        "return": None,
    }
    row.update(fields)
    return row


def _draw_row(
    call_id: int,
    *,
    mode: int = 5,
    raw_count: int = 0,
    effective: int = 4,
    qualified: bool = True,
    primitive_enum: int = 4,
    payload: str | None = "payloads/4-4.bin",
) -> dict:
    return {
        "schema": "gfx-op/v0",
        "call_id": call_id,
        "completion_seq": 1,
        "symbol": "_GfxDraw",
        "rva": DRAW_RVA,
        "thread_id": 7,
        "caller_rva": CALLER,
        "caller_known": True,
        "overlap": False,
        "fields_ok": True,
        "output_base_non_null": False,
        "input_size": 32,
        "output_size": 0,
        "input_hex": ("00" * 32),
        "output_hex": "",
        "return": None,
        "kind": "draw",
        "contents_known": False,
        "mode": mode,
        "raw_count": raw_count,
        "first": 0,
        "effective_count": effective,
        "count_source": "master" if raw_count <= 0 else "esi",
        "shader_non_null": True,
        "master_complete": True,
        "caller_match": True,
        "qualified": qualified,
        "primitive_enum": primitive_enum,
        "primitive_call_id": 3,
        "payload": {"size": 0x171, "file": payload} if payload else None,
    }


def _write_capture(
    directory: Path,
    *,
    rows: list[dict],
    summary_overrides: dict | None = None,
    payload: bytes | None = None,
    payload_path: str = "payloads/4-4.bin",
    payloads: dict[str, bytes] | None = None,
    targets: list[dict] | None = None,
) -> Path:
    target = directory / "capture"
    shutil.copytree(BASE_CAPTURE, target)
    for name in ("operations.jsonl", "operations-summary.json"):
        (target / name).unlink(missing_ok=True)
    payload_dir = target / "payloads"
    if payload_dir.exists():
        shutil.rmtree(payload_dir)
    payload_dir.mkdir()
    metadata = json.loads((target / "metadata.json").read_text())
    if payload is not None:
        (target / payload_path).write_bytes(payload)
    for name, data in (payloads or {}).items():
        (target / name).write_bytes(data)
    (target / "operations.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    init_call_id = 2
    for line in (target / "events.jsonl").read_text().splitlines():
        event = json.loads(line)
        if event.get("site") == "init" and event.get("phase") == "exit":
            init_call_id = event["call_id"]
    summary = {
        "schema": "gfx-op-summary/v0",
        "recorder_version": "1",
        "session": metadata["session"],
        "pid": metadata["pid"],
        "executable_sha256": metadata["executable_sha256"],
        "image_base": metadata["image_base"],
        "enabled": True,
        "targets": targets or [
            {"symbol": "_GfxDraw", "rva": DRAW_RVA, "length": 6,
             "relocation": "safe"},
            {"symbol": "_GfxSetPrimitiveType", "rva": "0x15ea76f", "length": 5,
             "relocation": "safe"},
        ],
        "max_events": 1024,
        "capacity": 1024,
        "duration_ms": 10000,
        "retention": "state",
        "stop_policy": "target_draw",
        "observed": max(3, len(rows)),
        "state": "sealed",
        "events": len(rows),
        "integrity_ok": True,
        "records_copied": True,
        "overlaps": 0,
        "refused": 0,
        "unmatched": 0,
        "unavailable": 0,
        "overflow": 0,
        "in_flight": 0,
        "init_seen": True,
        "init_call_id": init_call_id,
        "target_draw_caller": CALLER,
        "target_draw_qualified": True,
        "target_draw_call_id": 4,
        "draw_observed": 3,
        "draw_at_caller": 2,
        "draw_qualified": 1,
        "retention_budget_exhausted": False,
        "payload_gaps": 0,
        "primitive_sets": 2,
        "draw_caller_overflow": 0,
        "first_event_ms": 0,
        "last_event_ms": 10,
        "draw_modes": [0, 0, 0, 0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        "draw_counts": {"zero": 1, "one": 0, "two": 1, "at_least_three": 1},
        "draw_callers": [{"rva": CALLER, "count": 2}],
        "coverage": {"events": len(rows), "observed": 3, "overlaps": 0},
        "reason": "",
    }
    if summary_overrides:
        summary.update(summary_overrides)
    (target / "operations-summary.json").write_text(json.dumps(summary) + "\n")
    return target


class DrawPacketTests(unittest.TestCase):
    def test_qualified_skeleton_is_incomplete_with_real_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            capture_dir = _write_capture(
                directory, rows=[_draw_row(4)], payload=_master()
            )
            packet = packet_builder.build_packet(capture_dir)
            self.assertEqual(packet["status"], "incomplete")
            self.assertFalse(packet["packet_ready"])
            self.assertTrue(packet["target"]["qualified"])
            self.assertEqual(packet["target"]["mode"], 5)
            self.assertEqual(packet["target"]["effective_count"], 4)
            self.assertEqual(packet["target"]["caller_rva"], "0xedfcd1")
            self.assertEqual(packet["target"]["primitive_enum"], 4)
            self.assertIn("shader_identity", packet["missing"])
            self.assertIn("texture_slot_0_contents", packet["missing"])
            self.assertIn("matrix_constant", packet["missing"])
            self.assertEqual(
                packet["inputs"]["primitive_semantics"]["status"], "established"
            )
            self.assertEqual(
                packet["inputs"]["viewport"]["value"]["gl_viewport"],
                {"x": 0, "y": 0, "width": 800, "height": 600},
            )
            self.assertEqual(packet["provenance"]["session"],
                             capture.load_capture(capture_dir).session)

    def test_no_qualifying_draw_reports_observed_callsites(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir = _write_capture(
                Path(tmp),
                rows=[_draw_row(4, mode=4, effective=2, qualified=False)],
                payload=_master(mode=4, count=2),
                summary_overrides={
                    "target_draw_qualified": False,
                    "draw_qualified": 0,
                    "draw_modes": [0, 0, 0, 0, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                    "draw_counts": {"zero": 0, "one": 0, "two": 3,
                                    "at_least_three": 0},
                },
            )
            packet = packet_builder.build_packet(capture_dir)
            self.assertEqual(packet["status"], "draw_not_qualified")
            self.assertFalse(packet["packet_ready"])
            self.assertEqual(packet["diagnostics"]["observed_draws"], 3)
            self.assertEqual(packet["diagnostics"]["modes"], {"4": 3})
            self.assertEqual(packet["diagnostics"]["callers"][0]["rva"], CALLER)
            self.assertIn("draw_invocation", packet["missing"])

    def test_zero_retained_draws_is_draw_not_qualified(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir = _write_capture(
                Path(tmp),
                rows=[],
                summary_overrides={
                    "events": 0,
                    "observed": 4,
                    "target_draw_qualified": False,
                    "target_draw_call_id": 0,
                    "draw_observed": 4,
                    "draw_at_caller": 2,
                    "draw_qualified": 0,
                    "draw_modes": [0, 0, 0, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                    "draw_counts": {"zero": 0, "one": 0, "two": 0,
                                    "at_least_three": 4},
                },
            )
            packet = packet_builder.build_packet(capture_dir)
            self.assertEqual(packet["status"], "draw_not_qualified")
            self.assertEqual(packet["target"]["qualified"], False)
            self.assertEqual(packet["diagnostics"]["retained_draws"], 0)
            self.assertEqual(packet["diagnostics"]["observed_draws"], 4)

    def test_retention_budget_is_unavailable_not_nonqualified(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir = _write_capture(
                Path(tmp),
                rows=[_draw_row(4, qualified=False, effective=2)],
                payload=_master(count=2),
                summary_overrides={
                    "target_draw_qualified": False,
                    "retention_budget_exhausted": True,
                },
            )
            packet = packet_builder.build_packet(capture_dir)
            self.assertEqual(packet["status"], "unavailable")
            self.assertEqual(packet["reason"], "retention_budget")

    def test_primitive_mapping_mismatch_is_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir = _write_capture(
                Path(tmp),
                rows=[_draw_row(4, primitive_enum=3)],
                payload=_master(),
            )
            packet = packet_builder.build_packet(capture_dir)
            self.assertEqual(packet["status"], "unsupported")
            self.assertEqual(packet["reason"], "primitive_table_mismatch")
            self.assertEqual(
                packet["inputs"]["primitive_semantics"]["status"], "unsupported"
            )

    def test_positive_incoming_count_overrides_master_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir = _write_capture(
                Path(tmp),
                rows=[_draw_row(4, raw_count=3, effective=3)],
                payload=_master(count=4),
            )
            packet = packet_builder.build_packet(capture_dir)
            self.assertEqual(packet["status"], "incomplete")
            self.assertEqual(packet["target"]["effective_count"], 3)
            self.assertEqual(packet["target"]["raw_count"], 3)
            self.assertEqual(packet["target"]["count_source"], "esi")

    def test_nonpositive_incoming_count_uses_master_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir = _write_capture(
                Path(tmp),
                rows=[_draw_row(4, raw_count=-1, effective=4)],
                payload=_master(count=4),
            )
            packet = packet_builder.build_packet(capture_dir)
            self.assertEqual(packet["status"], "incomplete")
            self.assertEqual(packet["target"]["effective_count"], 4)
            self.assertEqual(packet["target"]["count_source"], "master")

    def test_inconsistent_count_source_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = _draw_row(4, raw_count=3, effective=3)
            row["count_source"] = "master"
            capture_dir = _write_capture(
                Path(tmp), rows=[row], payload=_master(count=4)
            )
            packet = packet_builder.build_packet(capture_dir)
            self.assertEqual(packet["status"], "unavailable")
            self.assertEqual(packet["reason"], "draw_meta_mismatch")

    def test_qualified_record_without_summary_target_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir = _write_capture(
                Path(tmp),
                rows=[_draw_row(4)],
                payload=_master(),
                summary_overrides={
                    "target_draw_qualified": False,
                    "target_draw_call_id": 0,
                },
            )
            packet = packet_builder.build_packet(capture_dir)
            self.assertEqual(packet["status"], "unavailable")
            self.assertEqual(packet["reason"], "op_draw_qualified_without_target")
            with self.assertRaises(capture.CaptureIncomplete) as caught:
                capture.load_capture(capture_dir)
            self.assertEqual(
                caught.exception.reason, "op_draw_qualified_without_target"
            )

    def test_summary_target_call_id_mismatch_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir = _write_capture(
                Path(tmp),
                rows=[_draw_row(4)],
                payload=_master(),
                summary_overrides={"target_draw_call_id": 99},
            )
            packet = packet_builder.build_packet(capture_dir)
            self.assertEqual(packet["status"], "unavailable")
            self.assertEqual(packet["reason"], "op_draw_qualified_call_id")

    def test_meta_master_contradiction_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = _draw_row(4)
            row["effective_count"] = 9
            capture_dir = _write_capture(
                Path(tmp), rows=[row], payload=_master()
            )
            packet = packet_builder.build_packet(capture_dir)
            self.assertEqual(packet["status"], "unavailable")
            self.assertEqual(packet["reason"], "draw_meta_mismatch")

    def test_wrong_caller_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir = _write_capture(
                Path(tmp), rows=[_draw_row(4)], payload=_master()
            )
            packet = packet_builder.build_packet(capture_dir, expected_caller=0x1234)
            self.assertEqual(packet["status"], "unavailable")
            self.assertEqual(packet["reason"], "caller_mismatch")

    def test_packet_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            capture_dir = _write_capture(
                directory, rows=[_draw_row(4)], payload=_master()
            )
            output = directory / "packet"
            result = live_gfx.main(
                [
                    "packet",
                    "--capture",
                    str(capture_dir),
                    "--output",
                    str(output),
                    "--caller-rva",
                    "0xedfcd1",
                ]
            )
            self.assertEqual(result, live_gfx.EXIT_INCOMPLETE)
            packet = json.loads((output / "draw-packet.json").read_text())
            self.assertEqual(packet["status"], "incomplete")

    def _full_capture(self, directory: Path) -> Path:
        draw = _draw_row(5, payload="payloads/5-5.bin")
        draw["completion_seq"] = 3
        draw["constant_slot0_vec4"] = 4
        draw["constant_slot0_header"] = CB_HEADER
        draw["constant_slot0_match"] = True
        draw["constant_slot0_call_id"] = 4
        rows = [
            _resource_row(
                3,
                "_GfxCreateVertexBuffer",
                "0x15ebcb3",
                kind="vertex_buffer_create",
                resource_id="vb_1",
                contents_known=True,
                count=4,
                stride=24,
                usage=0,
                header_non_null=True,
                header=VB_HEADER,
                payload={"size": len(VB_BYTES), "file": "payloads/3-1.bin"},
            ),
            _resource_row(
                4,
                "_GfxSetConstantBuffer",
                "0x15ec36a",
                kind="constant_set",
                resource_id="cb_1",
                contents_known=True,
                slot=0,
                selector=16,
                header=CB_HEADER,
                data_ptr=0x60000125A5C0,
                data_size=64,
                vec4_count=4,
                payload={"size": len(CB_BYTES), "file": "payloads/4-1.bin"},
            ),
            draw,
        ]
        return _write_capture(
            directory,
            rows=rows,
            payload=_full_master(),
            payload_path="payloads/5-5.bin",
            payloads={
                "payloads/3-1.bin": VB_BYTES,
                "payloads/4-1.bin": CB_BYTES,
            },
            targets=[
                {"symbol": "_GfxDraw", "rva": DRAW_RVA, "length": 6,
                 "relocation": "safe"},
                {"symbol": "_GfxSetPrimitiveType", "rva": "0x15ea76f",
                 "length": 5, "relocation": "safe"},
                {"symbol": "_GfxCreateVertexBuffer", "rva": "0x15ebcb3",
                 "length": 6, "relocation": "safe"},
                {"symbol": "_GfxSetConstantBuffer", "rva": "0x15ec36a",
                 "length": 9, "relocation": "safe"},
            ],
            summary_overrides={
                "target_draw_call_id": 5,
                "draw_observed": len(rows),
                "draw_qualified": 1,
            },
        )

    def test_lifecycle_and_constant_inputs_are_established(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir = self._full_capture(Path(tmp))
            packet = packet_builder.build_packet(capture_dir)
            for name in ("vertex_buffer_bindings", "vertex_buffer_contents",
                         "matrix_constant"):
                self.assertEqual(packet["inputs"][name]["status"],
                                 "established", name)
            self.assertNotIn("matrix_constant", packet["missing"])
            buffer = packet["inputs"]["vertex_buffer_contents"]["value"][
                "buffers"
            ][0]
            self.assertEqual(buffer["resource_id"], "vb_1")
            self.assertEqual(buffer["count"], 4)
            self.assertEqual(buffer["stride"], 24)
            self.assertEqual(buffer["sidecar"], "payloads/vb_1.bin")
            matrix = packet["inputs"]["matrix_constant"]["value"]
            self.assertEqual(matrix["hex"], CB_BYTES.hex())
            self.assertTrue(matrix["draw_time_match"])
            self.assertEqual(matrix["last_call_id"], 4)

    def test_writer_emits_resource_and_matrix_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            capture_dir = self._full_capture(directory)
            output = directory / "packet"
            result = packet_builder.write_packet(capture_dir, output)
            self.assertEqual((output / "payloads" / "vb_1.bin").read_bytes(),
                             VB_BYTES)
            self.assertEqual((output / "payloads" / "matrix.bin").read_bytes(),
                             CB_BYTES)
            hashes = result["packet"]["provenance"]["source_hashes"]
            self.assertIn("payloads/vb_1.bin", hashes)
            self.assertIn("payloads/matrix.bin", hashes)

    def test_writer_emits_snapshot_and_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            capture_dir = _write_capture(
                directory, rows=[_draw_row(4)], payload=_master()
            )
            output = directory / "packet"
            result = packet_builder.write_packet(capture_dir, output)
            self.assertTrue((output / "draw-packet.json").is_file())
            snapshot = output / "payloads" / "4-draw.bin"
            self.assertTrue(snapshot.is_file())
            self.assertIn(
                "payloads/4-draw.bin",
                result["packet"]["provenance"]["source_hashes"],
            )
            with self.assertRaises(packet_builder.PacketError):
                packet_builder.write_packet(capture_dir, output)


class DrawCaptureStatusTests(unittest.TestCase):
    def _report(self, **operations):
        base = {
            "state": "sealed",
            "integrity_ok": True,
            "records_copied": True,
            "target_draw_qualified": False,
            "retention_budget_exhausted": False,
            "draw_observed": 0,
            "events": 0,
            "reason": "",
        }
        base.update(operations)
        return {
            "status": {"state": "installed"},
            "operations": base,
        }

    def test_qualified_status(self):
        report = self._report(target_draw_qualified=True, events=1)
        self.assertEqual(
            live_gfx._capture_status(report, until_target_draw=True),
            ("complete", ""),
        )

    def test_nonqualified_and_budget_and_integrity_are_distinct(self):
        self.assertEqual(
            live_gfx._capture_status(self._report(), until_target_draw=True),
            ("draw_not_qualified", "no_target_draw"),
        )
        self.assertEqual(
            live_gfx._capture_status(
                self._report(retention_budget_exhausted=True),
                until_target_draw=True,
            ),
            ("budget_exhausted", "retention_budget"),
        )
        self.assertEqual(
            live_gfx._capture_status(
                self._report(integrity_ok=False), until_target_draw=True
            ),
            ("unavailable", "integrity"),
        )

    def test_legacy_status_unchanged(self):
        report = self._report(events=1)
        self.assertEqual(live_gfx._capture_status(report), ("complete", ""))
        self.assertEqual(
            live_gfx._capture_status(self._report(events=0)),
            ("empty", "no matching operation was observed"),
        )


class DrawReaderTests(unittest.TestCase):
    def test_reader_accepts_zero_events_only_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir = _write_capture(
                Path(tmp),
                rows=[],
                summary_overrides={
                    "events": 0,
                    "observed": 1,
                    "target_draw_qualified": False,
                    "target_draw_call_id": 0,
                    "draw_observed": 1,
                    "draw_at_caller": 0,
                    "draw_qualified": 0,
                    "draw_modes": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                    "draw_counts": {"zero": 1, "one": 0, "two": 0,
                                    "at_least_three": 0},
                },
            )
            with self.assertRaises(capture.CaptureIncomplete) as caught:
                capture.load_capture(capture_dir)
            self.assertEqual(caught.exception.reason, "op_no_events")
            artifact = capture.load_capture(capture_dir, require_events=False)
            self.assertEqual(artifact.operations, ())
            self.assertEqual(artifact.op_summary["retention"], "state")

    def test_legacy_capture_allows_multiple_qualifying_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            second = _draw_row(5)
            second["completion_seq"] = 2
            capture_dir = _write_capture(
                Path(tmp),
                rows=[_draw_row(4), second],
                payload=_master(),
                summary_overrides={
                    "stop_policy": "quota",
                    "retention": "legacy",
                    "events": 2,
                    "observed": 2,
                    "target_draw_qualified": False,
                    "target_draw_call_id": 0,
                    "draw_observed": 2,
                    "draw_qualified": 2,
                },
            )
            artifact = capture.load_capture(capture_dir)
            self.assertEqual(len(artifact.operations), 2)
            self.assertEqual(artifact.op_summary["stop_policy"], "quota")

    def test_reader_rejects_draw_payload_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir = _write_capture(
                Path(tmp), rows=[_draw_row(4)], payload=_master()[:-1]
            )
            row = json.loads((capture_dir / "operations.jsonl").read_text())
            row["payload"]["size"] = 0x170
            (capture_dir / "operations.jsonl").write_text(json.dumps(row) + "\n")
            with self.assertRaises(capture.CaptureIncomplete) as caught:
                capture.load_capture(capture_dir)
            self.assertEqual(caught.exception.reason, "op_draw_payload")


if __name__ == "__main__":
    unittest.main()
