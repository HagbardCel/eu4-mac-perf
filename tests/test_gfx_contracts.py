#!/usr/bin/env python3
"""Portable tests for the versioned draw-evidence contract API."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation.gfx_contracts import (  # noqa: E402
    Contract,
    ContractContext,
    ContractError,
    load_contract,
    merge_validators,
)
from automation.gfx_contracts.api import (  # noqa: E402
    require_precedes,
    require_record,
    resolve_packet_payload,
    verify_static_span,
)


class LoaderTests(unittest.TestCase):
    def test_v0_loads_and_is_empty(self):
        contract = load_contract("v0")
        self.assertEqual(contract.version, "v0")
        self.assertEqual(contract.validators, {})

    def test_invalid_version_is_rejected(self):
        with self.assertRaises(ContractError):
            load_contract("../v0")
        with self.assertRaises(ContractError):
            load_contract("v99")

    def test_duplicate_registration_is_rejected(self):
        base = {"draw_invocation": lambda value, ctx: None}
        contract = Contract("v1", {"draw_invocation": lambda value, ctx: None})
        with self.assertRaises(ContractError):
            merge_validators(base, contract)


class HelperTests(unittest.TestCase):
    def test_packet_payload_rejects_escapes(self):
        with tempfile.TemporaryDirectory() as raw:
            packet = Path(raw)
            (packet / "ok.bin").write_bytes(b"x")
            self.assertIsNotNone(resolve_packet_payload(packet, "ok.bin"))
            self.assertIsNone(resolve_packet_payload(packet, "../ok.bin"))
            self.assertIsNone(resolve_packet_payload(packet, "/tmp/ok.bin"))
            self.assertIsNone(resolve_packet_payload(packet, "missing.bin"))

    def test_require_record_and_precedes(self):
        draw = SimpleNamespace(call_id=11, symbol="_GfxDraw", fields_ok=True)
        create = SimpleNamespace(call_id=5, symbol="_GfxCreateVertexBuffer", fields_ok=True)
        ctx = SimpleNamespace(
            artifact=SimpleNamespace(operations=[create, draw]),
            target=draw,
        )
        record, error = require_record(ctx, 5, symbol="_GfxCreateVertexBuffer")
        self.assertIsNone(error)
        self.assertEqual(record.call_id, 5)
        self.assertIsNone(require_precedes(record, ctx))
        record, error = require_record(ctx, 11, symbol="_GfxCreateVertexBuffer")
        self.assertIsNotNone(error)
        self.assertIsNotNone(require_precedes(draw, ctx))

    def test_static_span_rejects_wrong_identity_and_hash(self):
        ctx = ContractContext(
            artifact=SimpleNamespace(operations=[]),
            target=SimpleNamespace(call_id=11),
            master={},
            packet_dir=Path("/tmp"),
            capture_dir=Path("/tmp"),
            config={},
            repo=ROOT,
            target_metadata={
                "sha256": "a" * 64,
                "default_executable": "/nonexistent",
            },
        )
        span = {
            "executable_sha256": "b" * 64,
            "rva": "0x10",
            "instruction_span": "90",
            "span_sha256": hashlib.sha256(b"\x90").hexdigest(),
        }
        self.assertIn("executable", verify_static_span(span, ctx) or "")
        span["executable_sha256"] = "a" * 64
        self.assertIn("not available", verify_static_span(span, ctx) or "")
        span["span_sha256"] = "c" * 64
        self.assertIn("hash", verify_static_span(span, ctx) or "")


if __name__ == "__main__":
    unittest.main()
