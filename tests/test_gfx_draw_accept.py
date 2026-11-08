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


def _master() -> bytes:
    raw = bytearray(0x171)
    struct.pack_into("<i", raw, 0x08, 5)
    struct.pack_into("<i", raw, 0x64, 4)
    struct.pack_into("<Q", raw, 0x70, 0x1234)
    struct.pack_into("<I", raw, 0xC8, 4)
    struct.pack_into("<Q", raw, 0xD0, 0x1000)
    struct.pack_into("<Q", raw, 0x130, 0x2000)
    struct.pack_into("<I", raw, 0x150, 0)
    struct.pack_into("<I", raw, 0x160, 24)
    return bytes(raw)


def _ctx(artifact_root: Path) -> dict:
    raw = _master()
    artifact = SimpleNamespace(
        session="s",
        pid=1,
        executable_sha256="b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d",
        image_base=0x100000000,
        root=artifact_root,
        operations=[],
    )
    return {
        "artifact": artifact,
        "target": SimpleNamespace(call_id=4),
        "master": trusted_packet.parse_master(raw),
        "effective_count": 4,
    }


def _packet(packet_dir: Path, capture_dir: Path, *, omit_matrix: bool = False) -> dict:
    vb = bytes(range(96))
    matrix = bytes(range(64))
    (packet_dir / "vb.bin").write_bytes(vb)
    (packet_dir / "matrix.bin").write_bytes(matrix)
    (capture_dir / "draw.bin").write_bytes(_master())
    draw_ev = {
        "type": "draw_snapshot",
        "call_id": 4,
        "payload": "draw.bin",
        "sha256": hashlib.sha256(_master()).hexdigest(),
        "size": len(_master()),
        "offset": 0,
        "length": len(_master()),
    }
    inputs = {
        "vertex_buffer_bindings": {
            "status": "established",
            "evidence": [draw_ev],
            "value": {
                "slots": [{
                    "slot": 0, "resource_id": "vb_1", "stride": 24,
                    "offset": 0, "count": 4,
                    "session_local_header": "0x2000",
                }]
            },
        },
        "vertex_buffer_contents": {
            "status": "established",
            "evidence": [draw_ev],
            "value": {
                "buffers": [{
                    "slot": 0, "resource_id": "vb_1", "stride": 24,
                    "offset": 0, "count": 4, "contents_size": 96,
                    "contents_sha256": hashlib.sha256(vb).hexdigest(),
                    "sidecar": "vb.bin", "method": "draw_time",
                    "last_call_id": 4,
                }]
            },
        },
        "matrix_constant": {
            "status": "missing" if omit_matrix else "established",
            "evidence": [] if omit_matrix else [draw_ev],
            "value": {} if omit_matrix else {
                "slot": 0, "size": 64, "hex": matrix.hex(),
                "sha256": hashlib.sha256(matrix).hexdigest(),
                "sidecar": "matrix.bin", "method": "draw_time",
                "last_call_id": 4,
            },
        },
    }
    return {
        "schema": trusted_packet.SCHEMA,
        "status": "incomplete",
        "packet_ready": False,
        "target": {
            "call_id": 4,
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


class CheckerPureTests(unittest.TestCase):
    def test_path_allowlist(self):
        patterns = ["progress.md", "benchmark/eu4_gfx_observe*.c",
                    "analysis/gfx-draw/v0/l32b/effective-state/**"]
        self.assertTrue(accept.path_allowed("progress.md", patterns))
        self.assertTrue(accept.path_allowed("benchmark/eu4_gfx_observe_draw.c", patterns))
        self.assertTrue(accept.path_allowed(
            "analysis/gfx-draw/v0/l32b/effective-state/note.md", patterns))
        self.assertFalse(accept.path_allowed("analysis/gfx-draw/v0/probe/note.md", patterns))

    def test_matrix_and_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet_dir = root / "packet"
            packet_dir.mkdir()
            ctx = _ctx(root)
            packet = _packet(packet_dir, root)
            self.assertIsNone(accept._validate_matrix(
                packet["inputs"]["matrix_constant"]["value"], ctx, packet_dir))
            self.assertIsNone(accept._validate_bindings(
                packet["inputs"]["vertex_buffer_bindings"]["value"], ctx))
            packet["inputs"]["matrix_constant"]["value"]["hex"] = "00" * 64
            self.assertIn("matrix_constant", accept._validate_matrix(
                packet["inputs"]["matrix_constant"]["value"], ctx, packet_dir))

    def test_packet_contract_pass_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet_dir = root / "packet"
            packet_dir.mkdir()
            ctx = _ctx(root)
            packet = _packet(packet_dir, root)
            config = {"required_inputs": [
                "vertex_buffer_bindings", "vertex_buffer_contents", "matrix_constant"
            ]}
            validated, error = accept.validate_packet(packet, packet_dir, ctx, config)
            self.assertIsNone(error)
            self.assertEqual(validated["missing"], [])
            missing_packet = _packet(packet_dir, root, omit_matrix=True)
            validated, error = accept.validate_packet(
                missing_packet, packet_dir, ctx, config
            )
            self.assertIsNone(error)
            self.assertEqual(validated["missing"], ["matrix_constant"])

    def test_packet_hash_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet_dir = root / "packet"
            packet_dir.mkdir()
            ctx = _ctx(root)
            packet = _packet(packet_dir, root)
            packet["inputs"]["vertex_buffer_contents"]["value"]["buffers"][0][
                "contents_sha256"
            ] = "0" * 64
            _, error = accept.validate_packet(
                packet, packet_dir, ctx,
                {"required_inputs": ["vertex_buffer_contents"]},
            )
            self.assertIn("sidecar", error or "")

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


if __name__ == "__main__":
    unittest.main()
