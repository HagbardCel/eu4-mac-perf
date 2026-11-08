#!/usr/bin/env python3
"""Portable tests for the trusted L3.2b draw checker."""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

import automation.gfx_draw_accept as accept  # noqa: E402
import eu4_gfx_draw_packet as trusted_packet  # noqa: E402

CALL = 11
CREATE_CALL = 5
CONSTANT_CALL = 8
VB_HEADER = 0x2000
CONSTANT_HEADER = 0x1000
VB = bytes(range(96))
MATRIX = bytes(range(64))


def _master() -> bytes:
    raw = bytearray(0x171)
    struct.pack_into("<i", raw, 0x08, 5)
    struct.pack_into("<i", raw, 0x64, 4)
    struct.pack_into("<Q", raw, 0x70, 0x1234)
    struct.pack_into("<I", raw, 0xC8, 4)
    struct.pack_into("<Q", raw, 0xD0, CONSTANT_HEADER)
    struct.pack_into("<Q", raw, 0x130, VB_HEADER)
    struct.pack_into("<I", raw, 0x150, 0)
    struct.pack_into("<I", raw, 0x160, 24)
    return bytes(raw)


def _record(call_id, symbol, resource_id, payload, meta):
    return SimpleNamespace(
        call_id=call_id,
        symbol=symbol,
        resource_id=resource_id,
        fields_ok=True,
        contents_known=True,
        payload_bytes=payload,
        payload_file=f"payloads/{resource_id}.bin",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload_size=len(payload),
        meta=meta,
    )


def _ctx(capture_dir: Path, *, extra_records=None) -> dict:
    records = [
        _record(CREATE_CALL, "_GfxCreateVertexBuffer", "vb_1", VB,
                {"header": VB_HEADER, "count": 4, "stride": 24}),
        _record(CONSTANT_CALL, "_GfxSetConstantBuffer", "cb_1", MATRIX,
                {"slot": 0, "header": CONSTANT_HEADER}),
    ]
    if extra_records:
        records.extend(extra_records)
    for record in records:
        path = capture_dir / record.payload_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(record.payload_bytes)
    (capture_dir / "draw.bin").write_bytes(_master())
    artifact = SimpleNamespace(
        session="s",
        pid=1,
        executable_sha256="b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d",
        image_base=0x100000000,
        root=capture_dir,
        operations=records,
    )
    return {
        "artifact": artifact,
        "target": SimpleNamespace(
            call_id=CALL,
            payload_bytes=_master(),
            meta={
                "constant_slot0_match": True,
                "constant_slot0_call_id": CONSTANT_CALL,
            },
        ),
        "master": trusted_packet.parse_master(_master()),
        "effective_count": 4,
    }


def _packet(packet_dir: Path, capture_dir: Path, *, omit_matrix: bool = False) -> dict:
    (packet_dir / "vb.bin").write_bytes(VB)
    (packet_dir / "matrix.bin").write_bytes(MATRIX)
    draw_ev = {
        "type": "draw_snapshot",
        "call_id": CALL,
        "payload": "draw.bin",
        "sha256": hashlib.sha256(_master()).hexdigest(),
        "size": len(_master()),
        "offset": 0,
        "length": len(_master()),
    }
    create_ev = {
        "type": "resource_observation",
        "call_id": CREATE_CALL,
        "symbol": "_GfxCreateVertexBuffer",
        "resource_id": "vb_1",
        "payload": "payloads/vb_1.bin",
        "sha256": hashlib.sha256(VB).hexdigest(),
        "size": len(VB),
    }
    constant_ev = {
        "type": "resource_observation",
        "call_id": CONSTANT_CALL,
        "symbol": "_GfxSetConstantBuffer",
        "resource_id": "cb_1",
        "payload": "payloads/cb_1.bin",
        "sha256": hashlib.sha256(MATRIX).hexdigest(),
        "size": len(MATRIX),
    }
    inputs = {
        "vertex_buffer_bindings": {
            "status": "established",
            "evidence": [draw_ev, create_ev],
            "value": {
                "slots": [{
                    "slot": 0, "resource_id": "vb_1", "resource_call_id": CREATE_CALL,
                    "stride": 24, "offset": 0, "count": 4,
                    "session_local_header": "0x2000",
                }]
            },
        },
        "vertex_buffer_contents": {
            "status": "established",
            "evidence": [create_ev],
            "value": {
                "buffers": [{
                    "slot": 0, "resource_id": "vb_1", "resource_call_id": CREATE_CALL,
                    "first_call_id": CREATE_CALL, "last_call_id": CREATE_CALL,
                    "stride": 24, "offset": 0, "count": 4,
                    "contents_size": len(VB),
                    "contents_sha256": hashlib.sha256(VB).hexdigest(),
                    "sidecar": "vb.bin", "method": "resource_lifecycle",
                }]
            },
        },
        "matrix_constant": {
            "status": "missing" if omit_matrix else "established",
            "evidence": [] if omit_matrix else [constant_ev, draw_ev],
            "value": {} if omit_matrix else {
                "slot": 0, "size": 64, "hex": MATRIX.hex(),
                "sha256": hashlib.sha256(MATRIX).hexdigest(),
                "sidecar": "matrix.bin", "method": "resource_observation",
                "last_call_id": CONSTANT_CALL,
            },
        },
    }
    return {
        "schema": trusted_packet.SCHEMA,
        "status": "incomplete",
        "packet_ready": False,
        "target": {
            "call_id": CALL,
            "caller_rva": "0xedfcd1",
            "effective_count": 4,
            "mode": 5,
        },
        "provenance": {
            "session": "s",
            "pid": 1,
            "executable_sha256": "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d",
            "image_base": "0x100000000",
        },
        "inputs": inputs,
        "missing": [],
    }


REQUIRED = {"required_inputs": [
    "vertex_buffer_bindings", "vertex_buffer_contents", "matrix_constant"
]}


class CheckerPureTests(unittest.TestCase):
    def test_path_allowlist(self):
        patterns = ["progress.md", "benchmark/eu4_gfx_observe*.c",
                    "analysis/gfx-draw/v0/l32b/effective-state/**"]
        self.assertTrue(accept.path_allowed("progress.md", patterns))
        self.assertTrue(accept.path_allowed("benchmark/eu4_gfx_observe_draw.c", patterns))
        self.assertTrue(accept.path_allowed(
            "analysis/gfx-draw/v0/l32b/effective-state/note.md", patterns))
        self.assertFalse(accept.path_allowed("analysis/gfx-draw/v0/probe/note.md", patterns))

    def _fixture(self, root: Path):
        capture = root / "capture"
        capture.mkdir()
        packet_dir = root / "packet"
        packet_dir.mkdir()
        return capture, packet_dir, _ctx(capture), _packet(packet_dir, capture)

    def test_authentic_shape_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, packet_dir, ctx, packet = self._fixture(Path(tmp))
            validated, error = accept.validate_packet(packet, packet_dir, ctx, REQUIRED)
            self.assertIsNone(error)
            self.assertEqual(validated["missing"], [])

    def test_missing_required_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture, packet_dir = Path(tmp) / "capture", Path(tmp) / "packet"
            capture.mkdir()
            packet_dir.mkdir()
            ctx = _ctx(capture)
            packet = _packet(packet_dir, capture, omit_matrix=True)
            validated, error = accept.validate_packet(packet, packet_dir, ctx, REQUIRED)
            self.assertIsNone(error)
            self.assertEqual(validated["missing"], ["matrix_constant"])

    def test_forged_matrix_with_rehashed_sidecar_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture, packet_dir, ctx, packet = self._fixture(Path(tmp))
            forged = b"\xff" * 64
            (packet_dir / "matrix.bin").write_bytes(forged)
            value = packet["inputs"]["matrix_constant"]["value"]
            value["hex"] = forged.hex()
            value["sha256"] = hashlib.sha256(forged).hexdigest()
            _, error = accept.validate_packet(packet, packet_dir, ctx, REQUIRED)
            self.assertIn("matrix_constant", error or "")

    def test_matrix_linked_to_wrong_constant_call_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture, packet_dir, ctx, packet = self._fixture(Path(tmp))
            packet["inputs"]["matrix_constant"]["value"]["last_call_id"] = 99
            _, error = accept.validate_packet(packet, packet_dir, ctx, REQUIRED)
            self.assertIn("matrix_constant", error or "")

    def test_draw_time_vertex_contents_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture, packet_dir, ctx, packet = self._fixture(Path(tmp))
            packet["inputs"]["vertex_buffer_contents"]["value"]["buffers"][0][
                "method"
            ] = "draw_time"
            _, error = accept.validate_packet(packet, packet_dir, ctx, REQUIRED)
            self.assertIn("draw_time", error or "")

    def test_unknown_binding_resource_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture, packet_dir, ctx, packet = self._fixture(Path(tmp))
            packet["inputs"]["vertex_buffer_bindings"]["value"]["slots"][0][
                "resource_id"
            ] = "vb_missing"
            _, error = accept.validate_packet(packet, packet_dir, ctx, REQUIRED)
            self.assertIn("resource_id", error or "")

    def test_contents_from_different_resource_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture, packet_dir, ctx, packet = self._fixture(Path(tmp))
            packet["inputs"]["vertex_buffer_contents"]["value"]["buffers"][0][
                "resource_id"
            ] = "vb_2"
            _, error = accept.validate_packet(packet, packet_dir, ctx, REQUIRED)
            self.assertIn("resource_id", error or "")

    def test_evidence_bytes_unrelated_to_original_record_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture, packet_dir, ctx, packet = self._fixture(Path(tmp))
            forged = b"\x11" * len(VB)
            forged_path = capture / "payloads" / "forged.bin"
            forged_path.write_bytes(forged)
            evidence = packet["inputs"]["vertex_buffer_contents"]["evidence"][0]
            evidence["payload"] = "payloads/forged.bin"
            evidence["sha256"] = hashlib.sha256(forged).hexdigest()
            evidence["size"] = len(forged)
            _, error = accept.validate_packet(packet, packet_dir, ctx, REQUIRED)
            self.assertIn("original operation", error or "")

    def test_draw_snapshot_bytes_must_match_selected_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture, packet_dir, ctx, packet = self._fixture(Path(tmp))
            altered = bytearray(_master())
            altered[0x08] = 4
            (capture / "draw.bin").write_bytes(bytes(altered))
            evidence = packet["inputs"]["vertex_buffer_bindings"]["evidence"][0]
            evidence["sha256"] = hashlib.sha256(bytes(altered)).hexdigest()
            _, error = accept.validate_packet(packet, packet_dir, ctx, REQUIRED)
            self.assertIn("selected draw record", error or "")

    def test_launch_and_capture_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            captures = run_root / "captures" / "c1"
            captures.mkdir(parents=True)
            plans = run_root / "capture-plans"
            plans.mkdir()
            (plans / "c1.json").write_text(json.dumps({
                "question": "q", "expected_observation": "e", "command": "c"
            }))
            (captures / "capture-request.json").write_text(json.dumps({
                "library_sha256": "a" * 64
            }))
            (run_root / "live-launch.json").write_text(json.dumps({
                "schema": "live-launch/v0",
                "campaign_id": run_root.name,
                "limit": 3,
                "used": 1,
                "launches": [{
                    "token": "t", "output": str(captures), "status": "complete"
                }],
            }))
            launch, error = accept.read_launch(run_root, 3)
            self.assertIsNone(error)
            self.assertEqual(launch["used"], 1)
            selected, metadata, error = accept.select_capture(
                run_root, {"capture_root": "captures", "capture_plans": "capture-plans"},
                "a" * 64,
            )
            self.assertIsNone(error)
            self.assertEqual(selected, captures.resolve())
            self.assertEqual(metadata["plan"]["question"], "q")


@unittest.skipUnless(
    sys.platform == "darwin"
    and (ROOT / ".local/automation/eu4-l32b-effective-state/workspace").is_dir(),
    "authentic L3.2b capture/worktree is not available",
)
class AuthenticRequalificationTests(unittest.TestCase):
    def test_existing_capture_passes_corrected_checker(self):
        result = accept.check(
            ROOT / ".local/automation/eu4-l32b-effective-state/workspace",
            ROOT / "campaigns/eu4-l32b-effective-state",
        )
        self.assertEqual(result["verdict"], "pass", result["diagnostics"])


if __name__ == "__main__":
    unittest.main()
