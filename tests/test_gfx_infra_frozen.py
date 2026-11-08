#!/usr/bin/env python3
"""Frozen acceptance tests for the L3.2b infrastructure bootstrap.

These tests are run by ``automation.gfx_infra_accept`` from the trusted
checkout against the candidate workspace. They fail on the #178 baseline
because the v2 checker, checkpoint, ledger and coordinator do not exist there.
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

# The candidate workspace is first on PYTHONPATH. Do not put the trusted
# checkout ahead of it: these tests must exercise the candidate modules.
import eu4_gfx_draw_packet as packet_mod  # noqa: E402

EXE_SHA = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"


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


def _record(call_id, symbol, resource_id, payload, *, caller=0, thread=7, seq=0):
    return SimpleNamespace(
        call_id=call_id,
        symbol=symbol,
        caller_rva=caller,
        thread_id=thread,
        completion_seq=seq,
        payload_bytes=payload,
        payload_file=f"payloads/{call_id}.bin",
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload_size=len(payload),
        meta={},
        fields_ok=True,
        resource_id=resource_id,
        contents_known=True,
    )


def _artifact(capture_dir: Path, master: bytes):
    draw = _record(11, "_GfxDraw", "", master, caller=0xEDFCD1, seq=3)
    draw.meta = {"qualified": True, "raw_count": 4, "effective_count": 4}
    shader = _record(9, "_GfxSetShader", "shader_1", b"shader-object", seq=2)
    return SimpleNamespace(
        session="session",
        pid=123,
        executable_sha256=EXE_SHA,
        image_base=0x100000000,
        root=capture_dir,
        operations=[shader, draw],
    ), draw


def _packet(master: bytes) -> dict:
    return {
        "schema": packet_mod.SCHEMA,
        "status": "incomplete",
        "packet_ready": False,
        "target": {
            "call_id": 11,
            "caller_rva": "0xedfcd1",
            "effective_count": 4,
            "mode": 5,
        },
        "provenance": {
            "session": "session",
            "pid": 123,
            "executable_sha256": EXE_SHA,
            "image_base": "0x100000000",
        },
        "inputs": {},
    }


def _shader_evidence(packet_dir: Path, master: bytes) -> list[dict]:
    (packet_dir / "draw.bin").write_bytes(master)
    (packet_dir / "shader.bin").write_bytes(b"shader-object")
    return [
        {
            "type": "draw_snapshot",
            "call_id": 11,
            "payload": "draw.bin",
            "sha256": hashlib.sha256(master).hexdigest(),
            "size": len(master),
            "offset": 0,
            "length": len(master),
        },
        {
            "type": "resource_observation",
            "call_id": 9,
            "symbol": "_GfxSetShader",
            "resource_id": "shader_1",
            "payload": "shader.bin",
            "sha256": hashlib.sha256(b"shader-object").hexdigest(),
            "size": len(b"shader-object"),
        },
    ]


def _checkpoint(
    tmp: Path,
    *,
    field: str = "draw_invocation",
    evidence: list | None = None,
    basis: str = "generic substantive evidence",
) -> dict:
    from automation.gfx_checkpoint import write_checkpoint

    capture = tmp / "capture"
    capture.mkdir()
    (capture / "marker").write_text("x")
    packet_dir = tmp / "packet"
    packet_dir.mkdir()
    master = _master()
    packet = _packet(master)
    if evidence is None:
        evidence = [{"type": "draw_record", "call_id": 11}]
    packet["inputs"][field] = {
        "status": "established",
        "value": {},
        "evidence": evidence,
    }
    (packet_dir / "draw-packet.json").write_text(
        json.dumps(packet, indent=2), encoding="utf-8"
    )
    return write_checkpoint(
        tmp,
        campaign_id="c1",
        candidate_base_commit="a" * 40,
        trusted_contract_commit="t1",
        checker_sha256="c" * 64,
        contract_version="v0",
        contract_sha256=None,
        manifest_sha256="d" * 64,
        candidate_digest="e" * 64,
        observer_library_sha256="f" * 64,
        capture={
            "session": "session",
            "pid": 123,
            "executable_sha256": EXE_SHA,
            "library_sha256": "f" * 64,
        },
        packet=packet,
        packet_dir=packet_dir,
        capture_dir=capture,
        target={"call_id": 11, "caller_rva": "0xedfcd1", "mode": 5, "effective_count": 4},
        fields=[
            {
                "name": field,
                "status": "established",
                "state": "provisional",
                "evidence_relevant": True,
                "evidence_basis": basis,
            }
        ],
        contract_gaps=[{"name": field}],
        checkpoint_kind="partial",
        blocked_reason="unsupported_evidence_contract",
    )


class FailClosedTests(unittest.TestCase):
    def test_unknown_validator_is_provisional_not_pass(self):
        import automation.gfx_draw_accept_v2 as accept

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            capture = tmp / "capture"
            capture.mkdir()
            master = _master()
            artifact, draw = _artifact(capture, master)
            packet = _packet(master)
            packet["inputs"]["shader_identity"] = {
                "status": "established",
                "value": {},
                "evidence": _shader_evidence(tmp, master),
            }
            ctx = {
                "artifact": artifact,
                "target": draw,
                "master": packet_mod.parse_master(master),
                "effective_count": 4,
            }
            validated, error = accept.validate_packet(
                packet, tmp, ctx, {"required_inputs": ["shader_identity"]}
            )
            self.assertIsNone(error)
            self.assertEqual(validated["provisional"], ["shader_identity"])
            self.assertEqual(validated["established"], [])

    def test_empty_evidence_is_not_provisional(self):
        import automation.gfx_draw_accept_v2 as accept

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            capture = tmp / "capture"
            capture.mkdir()
            master = _master()
            artifact, draw = _artifact(capture, master)
            packet = _packet(master)
            packet["inputs"]["shader_identity"] = {
                "status": "established", "value": {}, "evidence": []
            }
            ctx = {
                "artifact": artifact,
                "target": draw,
                "master": packet_mod.parse_master(master),
                "effective_count": 4,
            }
            validated, error = accept.validate_packet(
                packet, tmp, ctx, {"required_inputs": ["shader_identity"]}
            )
            self.assertIsNone(validated)
            self.assertIn("field-relevant evidence", error or "")

    def test_unresolved_only_evidence_is_not_provisional(self):
        import automation.gfx_draw_accept_v2 as accept

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            capture = tmp / "capture"
            capture.mkdir()
            master = _master()
            artifact, draw = _artifact(capture, master)
            packet = _packet(master)
            packet["inputs"]["shader_identity"] = {
                "status": "established",
                "value": {},
                "evidence": [{"type": "unresolved", "reason_code": "x"}],
            }
            ctx = {
                "artifact": artifact,
                "target": draw,
                "master": packet_mod.parse_master(master),
                "effective_count": 4,
            }
            validated, error = accept.validate_packet(
                packet, tmp, ctx, {"required_inputs": ["shader_identity"]}
            )
            self.assertIsNone(validated)
            self.assertIn("field-relevant evidence", error or "")

    def test_shader_source_backed_only_by_draw_record_is_rejected(self):
        import automation.gfx_draw_accept_v2 as accept

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            capture = tmp / "capture"
            capture.mkdir()
            master = _master()
            artifact, draw = _artifact(capture, master)
            packet = _packet(master)
            packet["inputs"]["shader_stage_sources"] = {
                "status": "established",
                "value": {},
                "evidence": [{"type": "draw_record", "call_id": 11}],
            }
            ctx = {
                "artifact": artifact,
                "target": draw,
                "master": packet_mod.parse_master(master),
                "effective_count": 4,
            }
            validated, error = accept.validate_packet(
                packet, tmp, ctx, {"required_inputs": ["shader_stage_sources"]}
            )
            self.assertIsNone(validated)
            self.assertIn("shader source", error or "")

    def test_base_draw_invocation_validator_accepts_authentic_shape(self):
        import automation.gfx_draw_accept_v2 as accept

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            capture = tmp / "capture"
            capture.mkdir()
            master = _master()
            artifact, draw = _artifact(capture, master)
            packet = _packet(master)
            packet["inputs"]["draw_invocation"] = {
                "status": "established",
                "value": {
                    "call_id": 11,
                    "caller_rva": "0xedfcd1",
                    "mode": 5,
                    "effective_count": 4,
                    "master_sha256": hashlib.sha256(master).hexdigest(),
                    "thread_id": 7,
                    "completion_seq": 3,
                },
                "evidence": [
                    {
                        "type": "draw_record",
                        "call_id": 11,
                        "caller_rva": 0xEDFCD1,
                        "thread_id": 7,
                        "completion_seq": 3,
                    }
                ],
            }
            ctx = {
                "artifact": artifact,
                "target": draw,
                "master": packet_mod.parse_master(master),
                "effective_count": 4,
            }
            validated, error = accept.validate_packet(
                packet, tmp, ctx, {"required_inputs": ["draw_invocation"]}
            )
            self.assertIsNone(error)
            self.assertEqual(validated["established"], ["draw_invocation"])

    def test_validator_sees_resolved_evidence_paths(self):
        import automation.gfx_draw_accept_v2 as accept
        from automation.gfx_contracts import Contract

        def validator(value, ctx):
            path = ctx.field_evidence_paths.get("draw.bin")
            if path is None or not path.is_file():
                return "shader_identity: resolved evidence path is missing"
            if path.read_bytes() != _master():
                return "shader_identity: resolved evidence bytes differ"
            return None

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            capture = tmp / "capture"
            capture.mkdir()
            master = _master()
            artifact, draw = _artifact(capture, master)
            packet = _packet(master)
            packet["inputs"]["shader_identity"] = {
                "status": "established",
                "value": {},
                "evidence": _shader_evidence(tmp, master),
            }
            ctx = {
                "artifact": artifact,
                "target": draw,
                "master": packet_mod.parse_master(master),
                "effective_count": 4,
                "contract": Contract("v1", {"shader_identity": validator}),
            }
            validated, error = accept.validate_packet(
                packet, tmp, ctx, {"required_inputs": ["shader_identity"]}
            )
            self.assertIsNone(error)
            self.assertEqual(validated["established"], ["shader_identity"])


class CheckpointTests(unittest.TestCase):
    def test_bundle_survives_temporary_packet_cleanup(self):
        from automation.gfx_checkpoint import validate_checkpoint

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            capture = tmp / "capture"
            artifact, _draw = _artifact(capture, _master())
            self.assertTrue(Path(checkpoint["path"]).is_file())
            errors = validate_checkpoint(
                Path(checkpoint["path"]),
                expected_campaign_id="c1",
                expected_candidate_base="a" * 40,
                artifact_loader=lambda _path: artifact,
            )
            self.assertEqual(errors, [])
            import shutil

            shutil.rmtree(tmp / "packet")
            errors = validate_checkpoint(
                Path(checkpoint["path"]),
                artifact_loader=lambda _path: artifact,
            )
            self.assertEqual(errors, [])

    def test_unrelated_evidence_is_rejected_by_checkpoint(self):
        from automation.gfx_checkpoint import validate_checkpoint

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(
                tmp,
                field="shader_stage_sources",
                evidence=[{"type": "draw_record", "call_id": 11}],
                basis="captured shader source bytes",
            )
            capture = tmp / "capture"
            artifact, _draw = _artifact(capture, _master())
            errors = validate_checkpoint(
                Path(checkpoint["path"]),
                artifact_loader=lambda _path: artifact,
            )
            self.assertTrue(any("field-relevant evidence" in error for error in errors))


class CheckpointPolicyTests(unittest.TestCase):
    def test_unrelated_draw_snapshot_is_rejected_for_render_state(self):
        from automation.gfx_checkpoint import validate_checkpoint

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(
                tmp,
                field="render_state_blend",
                evidence=[
                    {
                        "type": "draw_snapshot",
                        "call_id": 999,
                        "offset": 4,
                        "length": 8,
                    }
                ],
                basis="draw-master state bytes",
            )
            capture = tmp / "capture"
            artifact, _draw = _artifact(capture, _master())
            errors = validate_checkpoint(
                Path(checkpoint["path"]),
                artifact_loader=lambda _path: artifact,
            )
            self.assertTrue(any("field-relevant evidence" in error for error in errors))


class CheckpointPolicyTests2(unittest.TestCase):
    def test_unrelated_draw_snapshot_is_rejected_for_sampler(self):
        from automation.gfx_checkpoint import validate_checkpoint

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(
                tmp,
                field="sampler_state",
                evidence=[
                    {"type": "draw_snapshot", "call_id": 999, "sampler": {"filter": "linear"}}
                ],
                basis="draw-time sampler state",
            )
            capture = tmp / "capture"
            artifact, _draw = _artifact(capture, _master())
            errors = validate_checkpoint(
                Path(checkpoint["path"]),
                artifact_loader=lambda _path: artifact,
            )
            self.assertTrue(any("field-relevant evidence" in error for error in errors))


class LedgerTests(unittest.TestCase):
    def test_global_ledger_cannot_be_reset(self):
        from automation import launch_ledger

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            ledger = tmp / "ledger.json"
            launch_ledger.create_ledger(
                ledger, workflow_id="wf", limit=6, deadline_seconds=120
            )
            for index in range(6):
                launch_ledger.claim(
                    ledger,
                    campaign_id=f"campaign-{index}",
                    run_root=tmp / f"campaign-{index}",
                    output=tmp / f"out-{index}",
                    targets=["_GfxDraw"],
                    requested_timeout=120,
                )
            with self.assertRaises(launch_ledger.LedgerError):
                launch_ledger.claim(
                    ledger,
                    campaign_id="campaign-7",
                    run_root=tmp / "campaign-7",
                    output=tmp / "out-7",
                    targets=["_GfxDraw"],
                    requested_timeout=120,
                )
            value = launch_ledger.load_ledger(ledger)
            self.assertEqual(value["used"], 6)
            self.assertEqual(len(value["launches"]), 6)
            launch_ledger.reconcile(ledger)
            self.assertEqual(launch_ledger.load_ledger(ledger)["used"], 6)


class LedgerSelectionTests(unittest.TestCase):
    def test_completed_capture_is_found_without_the_campaign_mirror(self):
        from automation import launch_ledger
        import automation.gfx_draw_accept_v2 as accept

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_root = tmp / "campaign"
            capture = run_root / "captures" / "c1"
            capture.mkdir(parents=True)
            (run_root / "capture-plans").mkdir(parents=True)
            (run_root / "capture-plans" / "c1.json").write_text(
                json.dumps({"question": "q", "expected_observation": "e"})
            )
            (capture / "capture-request.json").write_text(
                json.dumps({"library_sha256": "a" * 64})
            )
            ledger = tmp / "ledger.json"
            launch_ledger.create_ledger(
                ledger, workflow_id="wf", limit=6, deadline_seconds=120
            )
            token = launch_ledger.claim(
                ledger,
                campaign_id="campaign",
                run_root=run_root,
                output=capture,
                targets=["_GfxDraw"],
                requested_timeout=120,
            )
            launch_ledger.start(ledger, token, 123)
            launch_ledger.finish(ledger, token, pid=123, status="completed")
            launch_ledger.write_pointer(
                run_root, workflow_id="wf", ledger_path=ledger, limit=6
            )
            config = {
                "capture_root": "captures",
                "capture_plans": "capture-plans",
            }
            launch, error = accept.read_launch(run_root, 3, config)
            self.assertIsNone(error)
            self.assertEqual(launch["used"], 1)
            selected, metadata, error = accept.select_capture(
                run_root, config, "a" * 64, launch
            )
            self.assertIsNone(error)
            self.assertEqual(selected, capture.resolve())
            self.assertEqual(metadata["plan"]["question"], "q")


class ContractRunnerTests(unittest.TestCase):
    def test_weak_validator_is_rejected_by_an_adversarial_case(self):
        from automation.gfx_contract_runner import run

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp, field="draw_invocation")
            workspace = tmp / "candidate"
            (workspace / "automation" / "gfx_contracts").mkdir(parents=True)
            (workspace / "automation" / "__init__.py").write_text("")
            (workspace / "automation" / "gfx_contracts" / "__init__.py").write_text("")
            (workspace / "automation" / "gfx_contracts" / "v1.py").write_text(
                "VALIDATORS = {'draw_invocation': lambda value, ctx: None}\n"
            )
            cases = tmp / "cases.json"
            cases.write_text(
                json.dumps(
                    {
                        "schema": "eu4-gfx-adversarial-cases/v1",
                        "candidate_commit": "c" * 40,
                        "contract_version": "v1",
                        "checkpoint_sha256": hashlib.sha256(
                            Path(checkpoint["path"]).read_bytes()
                        ).hexdigest(),
                        "fields": [
                            {
                                "field": "draw_invocation",
                                "mutation_class": "wrong_selected_object",
                                "structural_validity": "valid",
                                "forged_value": {},
                                "forged_evidence": [
                                    {"type": "draw_record", "call_id": 11}
                                ],
                                "why_semantically_wrong": "not the selected draw",
                                "expected_error_prefix": "draw_invocation",
                            }
                        ],
                    }
                )
            )
            capture = tmp / "capture"
            artifact, _draw = _artifact(capture, _master())
            result = run(
                workspace,
                "v1",
                Path(checkpoint["path"]),
                cases_path=cases,
                artifact_loader=lambda _path: artifact,
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["cases"][0]["outcome"], "false_pass")

    def test_nonexistent_forged_resource_is_an_invalid_test(self):
        from automation.gfx_contract_runner import run

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp, field="draw_invocation")
            workspace = tmp / "candidate"
            (workspace / "automation" / "gfx_contracts").mkdir(parents=True)
            (workspace / "automation" / "__init__.py").write_text("")
            (workspace / "automation" / "gfx_contracts" / "__init__.py").write_text("")
            (workspace / "automation" / "gfx_contracts" / "v1.py").write_text(
                "VALIDATORS = {'draw_invocation': lambda value, ctx: None}\n"
            )
            cases = tmp / "cases.json"
            cases.write_text(
                json.dumps(
                    {
                        "schema": "eu4-gfx-adversarial-cases/v1",
                        "candidate_commit": "c" * 40,
                        "contract_version": "v1",
                        "checkpoint_sha256": hashlib.sha256(
                            Path(checkpoint["path"]).read_bytes()
                        ).hexdigest(),
                        "fields": [
                            {
                                "field": "draw_invocation",
                                "mutation_class": "wrong_selected_object",
                                "structural_validity": "valid",
                                "forged_value": {},
                                "forged_evidence": [
                                    {"type": "draw_record", "call_id": 999}
                                ],
                                "why_semantically_wrong": "nonexistent record",
                                "expected_error_prefix": "draw_invocation",
                            }
                        ],
                    }
                )
            )
            capture = tmp / "capture"
            artifact, _draw = _artifact(capture, _master())
            result = run(
                workspace,
                "v1",
                Path(checkpoint["path"]),
                cases_path=cases,
                artifact_loader=lambda _path: artifact,
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["cases"][0]["outcome"], "invalid_test")

    def test_evidence_only_association_mutation_is_rejected_by_real_runner(self):
        from automation.gfx_checkpoint import write_checkpoint
        from automation.gfx_contract_runner import run

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            capture = tmp / "capture"
            capture.mkdir()
            master = _master()
            artifact, draw = _artifact(capture, master)
            payload = b"same source bytes"
            shader_a = _record(7, "_GfxAddVertexShader", "shader_a", payload)
            shader_b = _record(8, "_GfxAddVertexShader", "shader_b", payload)
            artifact.operations = [shader_a, shader_b, draw]
            packet_dir = tmp / "packet"
            (packet_dir / "payloads").mkdir(parents=True)
            (packet_dir / "payloads" / "7.bin").write_bytes(payload)
            (packet_dir / "payloads" / "8.bin").write_bytes(payload)
            packet = _packet(master)
            packet["inputs"]["shader_stage_sources"] = {
                "status": "established",
                "value": {"resource_id": "shader_a", "sha256": hashlib.sha256(payload).hexdigest()},
                "evidence": [
                    {
                        "type": "resource_observation",
                        "call_id": 7,
                        "symbol": "_GfxAddVertexShader",
                        "resource_id": "shader_a",
                        "payload": "payloads/7.bin",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "size": len(payload),
                    }
                ],
            }
            (packet_dir / "draw-packet.json").write_text(json.dumps(packet), encoding="utf-8")
            checkpoint = write_checkpoint(
                tmp,
                campaign_id="c1",
                candidate_base_commit="a" * 40,
                trusted_contract_commit="b" * 40,
                checker_sha256="c" * 64,
                contract_version="v1",
                contract_sha256=None,
                manifest_sha256="d" * 64,
                candidate_digest="e" * 64,
                observer_library_sha256="f" * 64,
                capture={
                    "session": "session",
                    "pid": 123,
                    "executable_sha256": EXE_SHA,
                    "library_sha256": "f" * 64,
                },
                packet=packet,
                packet_dir=packet_dir,
                capture_dir=capture,
                target={"call_id": 11, "caller_rva": "0xedfcd1", "mode": 5, "effective_count": 4},
                fields=[
                    {
                        "name": "shader_stage_sources",
                        "status": "established",
                        "state": "provisional",
                        "evidence_relevant": True,
                        "evidence_basis": "captured shader source bytes",
                    }
                ],
                contract_gaps=[{"name": "shader_stage_sources"}],
                checkpoint_kind="partial",
                blocked_reason="unsupported_evidence_contract",
            )
            workspace = tmp / "candidate"
            (workspace / "automation" / "gfx_contracts").mkdir(parents=True)
            (workspace / "automation" / "__init__.py").write_text("")
            (workspace / "automation" / "gfx_contracts" / "__init__.py").write_text("")
            (workspace / "automation" / "gfx_contracts" / "v1.py").write_text(
                "def validate(value, ctx):\n"
                "    evidence = ctx.field_evidence or []\n"
                "    if not evidence:\n"
                "        return 'shader_stage_sources: no evidence'\n"
                "    if evidence[0].get('resource_id') != value.get('resource_id'):\n"
                "        return 'shader_stage_sources: association differs'\n"
                "    return None\n"
                "VALIDATORS = {'shader_stage_sources': validate}\n"
            )
            cases = tmp / "cases.json"
            cases.write_text(
                json.dumps(
                    {
                        "schema": "eu4-gfx-adversarial-cases/v1",
                        "candidate_commit": "c" * 40,
                        "contract_version": "v1",
                        "checkpoint_sha256": hashlib.sha256(
                            Path(checkpoint["path"]).read_bytes()
                        ).hexdigest(),
                        "fields": [
                            {
                                "field": "shader_stage_sources",
                                "mutation_class": "cross_resource",
                                "structural_validity": "valid",
                                "forged_value": {
                                    "resource_id": "shader_a",
                                    "sha256": hashlib.sha256(payload).hexdigest(),
                                },
                                "forged_evidence": [
                                    {
                                        "type": "resource_observation",
                                        "call_id": 8,
                                        "symbol": "_GfxAddVertexShader",
                                        "resource_id": "shader_b",
                                        "payload": "payloads/8.bin",
                                        "sha256": hashlib.sha256(payload).hexdigest(),
                                        "size": len(payload),
                                    }
                                ],
                                "why_semantically_wrong": "source bytes belong to another shader object",
                                "expected_error_prefix": "shader_stage_sources",
                            }
                        ],
                    }
                )
            )
            result = run(
                workspace,
                "v1",
                Path(checkpoint["path"]),
                cases_path=cases,
                artifact_loader=lambda _path: artifact,
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["cases"][0]["outcome"], "semantic_rejection")


class CoordinatorTests(unittest.TestCase):
    def _review(self, path: Path, outcome: str, candidate: str) -> Path:
        from automation.review_result import write_review

        findings = []
        if outcome == "rejected":
            findings = [{"id": "F1", "severity": "P1", "kind": "evidence_gap"}]
        write_review(
            path,
            {
                "schema": "eu4-review/v1",
                "candidate_commit": candidate,
                "oracle_manifest_sha256": "d" * 64,
                "outcome": outcome,
                "findings": findings,
                "summary": "test",
            },
        )
        return path

    def _cases(self, path: Path, candidate: str, checkpoint: Path, field: str = "draw_invocation"):
        path.write_text(
            json.dumps(
                {
                    "schema": "eu4-gfx-adversarial-cases/v1",
                    "candidate_commit": candidate,
                    "contract_version": "v1",
                    "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                    "fields": [
                        {
                            "field": field,
                            "mutation_class": "wrong_selected_object",
                            "structural_validity": "valid",
                            "forged_value": {},
                            "forged_evidence": [{"type": "draw_record", "call_id": 11}],
                            "why_semantically_wrong": "not the selected draw",
                            "expected_error_prefix": field,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def _hooks(self, tmp: Path, checkpoint: dict, *, contract_reject_once: bool = False):
        from automation.gfx_l32b_complete import Hooks

        report_calls = {"n": 0}
        contract_calls = {"n": 0}
        snapshots = {"n": 0}
        impl_calls = {"n": 0}

        def implementer(state):
            impl_calls["n"] += 1
            if impl_calls["n"] > 1:
                state["stage"] = "final"
                receipt = tmp / "qualification.json"
                trusted = state.get("trusted_contract") or {}
                receipt.write_text(
                    json.dumps(
                        {
                            "schema": "eu4-gfx-qualification-receipt/v1",
                            "trusted_contract_commit": trusted.get("commit"),
                            "checker_sha256": "1" * 64,
                            "candidate_digest": "9" * 64,
                            "packet": {"packet_ready": True},
                            "fields": [{"name": "draw_invocation", "state": "validated"}],
                        }
                    )
                )
                return {
                    "status": "complete",
                    "last_verdict": "pass",
                    "workspace": str(tmp),
                    "qualification_receipt": str(receipt),
                }
            return {
                "status": "paused",
                "last_verdict": "blocked",
                "workspace": str(tmp),
                "checkpoint": checkpoint["path"],
                "campaign_id": "c1",
                "candidate_base_commit": "a" * 40,
                "trusted_contract_commit": "t1",
            }

        def snapshot(workspace, state):
            snapshots["n"] += 1
            return {"candidate_commit": "c" * 40, "candidate_digest": "9" * 64}

        def snapshot_contract(workspace, state):
            contract_calls["n"] += 1
            commit = "d" * 40 if contract_calls["n"] == 1 else "e" * 40
            return {"commit": commit, "contract_sha256": "1" * 64}

        def run_contract(checkpoint_path, state):
            return {
                "status": "complete",
                "last_verdict": "pass",
                "workspace": str(tmp),
                "contract_version": "v1",
                "contract_sha256": "1" * 64,
                "checkpoint": checkpoint_path,
            }

        def reviewer(target, state):
            report_calls["n"] += 1
            target_commit = (target or {}).get("commit", "c" * 40)
            path = tmp / f"review-{report_calls['n']}.json"
            outcome = "qualified"
            if contract_reject_once and report_calls["n"] == 1:
                outcome = "rejected"
            self._review(path, outcome, target_commit)
            if outcome == "qualified" and target_commit in ("d" * 40, "e" * 40):
                self._cases(path.with_name("review-adversarial.json"), target_commit,
                            Path(checkpoint["path"]))
            return {"path": str(path), "session_id": f"rev{report_calls['n']}"}

        def adversarial(checkpoint_path, contract, cases_path, state):
            return {"ok": True, "positive_ok": True, "negative_ok": True, "cases": []}

        def integrate(candidate, contract, state, kind=None):
            return {"ok": True, "commit": "e" * 40, "kind": kind}

        def remediation(state):
            return {
                "status": "complete",
                "last_verdict": "pass",
                "workspace": str(tmp),
            }

        return Hooks(
            run_implementer=implementer,
            snapshot=snapshot,
            run_contract=run_contract,
            run_reviewer=reviewer,
            integrate=integrate,
            snapshot_contract=snapshot_contract,
            run_adversarial_cases=adversarial,
            run_remediation=remediation,
        )

    def _workflow(self):
        return {
            "schema": "eu4-l32b-workflow/v1",
            "workflow_id": "wf",
            "baseline_commit": "a" * 40,
            "oracle_manifest_sha256": "d" * 64,
            "trusted_contract": {"commit": "t1", "version": "v0", "sha256": "2" * 64},
        }

    def test_fake_path_reaches_awaiting_merge(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            with mock.patch(
                "automation.gfx_l32b_complete.validate_checkpoint", return_value=[]
            ), mock.patch(
                "automation.gfx_l32b_complete._git_blob_sha", return_value="1" * 64
            ):
                state = coordinator.run()
            self.assertEqual(state["state"], "awaiting_merge")
            kinds = [row.get("kind") for row in state["history"] if row["event"] == "integrated"]
            self.assertEqual(kinds, ["contract", "final"])

    def test_contract_rejection_remediates_contract_not_candidate(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint, contract_reject_once=True)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            with mock.patch(
                "automation.gfx_l32b_complete.validate_checkpoint", return_value=[]
            ), mock.patch(
                "automation.gfx_l32b_complete._git_blob_sha", return_value="1" * 64
            ):
                state = coordinator.run()
            self.assertEqual(state["state"], "awaiting_merge")
            self.assertEqual(state["contract"]["commit"], "e" * 40)
            kinds = [row.get("kind") for row in state["history"] if row["event"] == "integrated"]
            self.assertEqual(kinds, ["contract", "final"])

    def test_implementation_continuation_does_not_require_contract(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            report = self._review(tmp / "review.json", "qualified", "c" * 40)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            state["state"] = "reviewing"
            state["review_target"] = "candidate"
            state["candidate"] = {"commit": "c" * 40}
            state["contract"] = {"commit": "u1"}
            state["trusted_contract"] = {"commit": "t1", "version": "v0", "sha256": "2" * 64}
            seen = []

            def integrate(candidate, contract, state, kind=None):
                seen.append((kind, (contract or {}).get("commit")))
                return {"ok": True, "commit": "e" * 40, "kind": kind}

            hooks.integrate = integrate
            with mock.patch.object(
                hooks, "run_reviewer", lambda target, state: {"path": str(report)}
            ):
                state = coordinator.step(state)
            self.assertEqual(state["promotion_kind"], "implementation")
            state = coordinator.step(state)
            self.assertEqual(state["state"], "continuation")
            self.assertEqual(seen, [("implementation", "t1")])

    def test_qualified_contract_without_cases_blocks(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            report = self._review(tmp / "review.json", "qualified", "d" * 40)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            state["contract"] = {"commit": "d" * 40}
            state["contract_version"] = "v1"
            state["contract_sha256"] = "1" * 64
            state["contract_checkpoint"] = checkpoint["path"]
            state["state"] = "qualifying_review"
            state["review_target"] = "contract"
            with mock.patch.object(
                hooks, "run_reviewer", lambda target, state: {"path": str(report)}
            ):
                state = coordinator.step(state)
            self.assertEqual(state["state"], "blocked")
            self.assertEqual(state["blocker"]["reason"], "adversarial_qualification_failed")

    def test_cases_bound_to_wrong_contract_commit_block(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            state["contract"] = {"commit": "d" * 40}
            state["contract_version"] = "v1"
            state["contract_sha256"] = "1" * 64
            state["contract_checkpoint"] = checkpoint["path"]
            report = self._review(tmp / "review.json", "qualified", "d" * 40)
            self._cases(
                tmp / "review-adversarial.json", "c" * 40, Path(checkpoint["path"])
            )
            errors = coordinator._qualify_contract(state, report)
            self.assertTrue(
                any("not bound to the reviewed contract commit" in error for error in errors)
            )

    def test_changed_report_or_cases_block_promotion(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            report = self._review(tmp / "review.json", "qualified", "d" * 40)
            cases = self._cases(
                tmp / "review-adversarial.json", "d" * 40, Path(checkpoint["path"])
            )
            hooks = self._hooks(tmp, checkpoint)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            state["contract"] = {"commit": "d" * 40}
            state["contract_version"] = "v1"
            state["contract_sha256"] = "1" * 64
            state["contract_checkpoint"] = checkpoint["path"]
            state["review"] = {"path": str(report)}
            state["adversarial_cases"] = str(cases)
            state["adversarial_report"] = {"ok": True}
            state["contract_review"] = {
                "contract_commit": "d" * 40,
                "contract_version": "v1",
                "contract_sha256": "1" * 64,
                "checkpoint_sha256": hashlib.sha256(
                    Path(checkpoint["path"]).read_bytes()
                ).hexdigest(),
                "reviewer_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                "adversarial_cases_sha256": hashlib.sha256(cases.read_bytes()).hexdigest(),
                "positive_ok": True,
                "negative_ok": True,
                "runner_report_sha256": hashlib.sha256(
                    json.dumps({"ok": True}, sort_keys=True).encode()
                ).hexdigest(),
            }
            with mock.patch(
                "automation.gfx_l32b_complete._git_blob_sha", return_value="1" * 64
            ):
                self.assertEqual(coordinator._validate_promotion(state, "contract"), [])
                report.write_text(report.read_text() + "\n", encoding="utf-8")
                errors = coordinator._validate_promotion(state, "contract")
            self.assertTrue(any("reviewer report changed" in error for error in errors))

    def test_final_review_must_name_the_candidate_commit(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            report = self._review(tmp / "review.json", "qualified", "f" * 40)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            state["state"] = "final_review"
            state["review_target"] = "final"
            state["candidate"] = {"commit": "c" * 40, "candidate_digest": "9" * 64}
            with mock.patch.object(
                hooks, "run_reviewer", lambda target, state: {"path": str(report)}
            ):
                state = coordinator.step(state)
            self.assertEqual(state["state"], "blocked")
            self.assertEqual(state["blocker"]["reason"], "invalid_review")

    def test_final_receipt_must_bind_candidate_and_checker(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            receipt = tmp / "receipt.json"
            receipt.write_text("{}")
            state["last_implementer"] = {
                "last_verdict": "pass",
                "qualification_receipt": str(receipt),
            }
            state["trusted_contract"] = {"commit": "t1", "version": "v0", "sha256": "2" * 64}
            state["candidate"] = {"commit": "c" * 40, "candidate_digest": "9" * 64}
            errors = coordinator._final_receipt(state, tmp / "review.json")
            self.assertTrue(any("schema" in error for error in errors))

    def test_implementation_promotion_requires_trusted_contract(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            state["candidate"] = {"commit": "c" * 40}
            state["trusted_contract"] = None
            state["candidate_review"] = {
                "candidate_commit": "c" * 40,
                "trusted_contract_commit": None,
                "trusted_contract_sha256": None,
            }
            errors = coordinator._validate_promotion(state, "implementation")
            self.assertTrue(any("trusted contract identity is missing" in error for error in errors))

    def test_oracle_mismatch_blocks_review(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            report = self._review(tmp / "review.json", "qualified", "c" * 40)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            state["state"] = "reviewing"
            state["review_target"] = "candidate"
            state["candidate"] = {"commit": "c" * 40}
            state["oracle_manifest_sha256"] = "a" * 64
            with mock.patch.object(
                hooks, "run_reviewer", lambda target, state: {"path": str(report)}
            ):
                state = coordinator.step(state)
            self.assertEqual(state["state"], "blocked")
            self.assertEqual(state["blocker"]["reason"], "invalid_review")

    def test_run_no_progress_blocks(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )

            class Stuck(Coordinator):
                def step(self, state):
                    return state

            stuck = Stuck(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "stuck.json",
                hooks=hooks,
            )
            state = stuck.run()
            self.assertEqual(state["state"], "blocked")
            self.assertEqual(state["blocker"]["reason"], "no_progress")

    def test_main_run_requires_hooks(self):
        from automation.gfx_l32b_complete import main

        code = main(
            [
                "run",
                "--workflow",
                str(ROOT / "campaigns/eu4-l32b-complete/workflow.json"),
            ]
        )
        self.assertEqual(code, 1)

    def test_review_uses_snapshot_candidate_commit_shape(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            report = self._review(tmp / "review.json", "qualified", "f" * 40)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            state["state"] = "reviewing"
            state["review_target"] = "candidate"
            state["candidate"] = {"candidate_commit": "c" * 40}
            with mock.patch.object(
                hooks, "run_reviewer", lambda target, state: {"path": str(report)}
            ):
                state = coordinator.step(state)
            self.assertEqual(state["state"], "blocked")
            self.assertEqual(state["blocker"]["reason"], "invalid_review")

    def test_checkpoint_trusted_contract_mismatch_blocks(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            state["state"] = "investigating"
            state["trusted_contract"] = {"commit": "t1", "version": "v0", "sha256": "2" * 64}

            def implementer(state):
                return {
                    "status": "paused",
                    "last_verdict": "blocked",
                    "workspace": str(tmp),
                    "checkpoint": checkpoint["path"],
                    "campaign_id": "c1",
                    "candidate_base_commit": "a" * 40,
                    "trusted_contract_commit": "other",
                }

            with mock.patch.object(hooks, "run_implementer", implementer):
                state = coordinator.step(state)
            self.assertEqual(state["state"], "blocked")
            self.assertEqual(
                state["blocker"]["reason"], "checkpoint_trusted_contract_mismatch"
            )

    def test_implementer_requires_trusted_contract(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            state["state"] = "investigating"
            state["trusted_contract"] = None
            state = coordinator.step(state)
            self.assertEqual(state["state"], "blocked")
            self.assertEqual(state["blocker"]["reason"], "trusted_contract_missing")

    def test_final_promotion_requires_trusted_contract(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            state["candidate"] = {"commit": "c" * 40}
            state["final_review"] = {"candidate_commit": "c" * 40}
            state["trusted_contract"] = None
            errors = coordinator._validate_promotion(state, "final")
            self.assertTrue(
                any("final trusted contract identity is missing" in error for error in errors)
            )

    def test_run_terminal_on_last_step_is_preserved(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            workflow = dict(self._workflow())
            workflow["max_steps"] = 1

            class Done(Coordinator):
                def step(self, state):
                    state["state"] = "awaiting_merge"
                    return state

            coordinator = Done(
                repo=tmp,
                workflow=workflow,
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.run()
            self.assertEqual(state["state"], "awaiting_merge")

    def test_preflight_populates_oracle_digest(self):
        from automation.gfx_l32b_complete import Coordinator, default_state

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            manifest = tmp / "manifest.json"
            manifest.write_text("{}")
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            workflow = dict(self._workflow())
            workflow.pop("oracle_manifest_sha256", None)
            workflow["trusted_contract"] = {
                "commit": "t1",
                "version": "v0",
                "sha256": "2" * 64,
                "manifest": str(manifest),
            }
            coordinator = Coordinator(
                repo=tmp,
                workflow=workflow,
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = default_state(workflow)
            state = coordinator._preflight(state)
            self.assertEqual(
                state["oracle_manifest_sha256"],
                hashlib.sha256(manifest.read_bytes()).hexdigest(),
            )

    def test_snapshot_candidate_commit_success_path(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            report = self._review(tmp / "review.json", "qualified", "c" * 40)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            state["state"] = "reviewing"
            state["review_target"] = "candidate"
            state["candidate"] = {"candidate_commit": "c" * 40}
            with mock.patch.object(
                hooks, "run_reviewer", lambda target, state: {"path": str(report)}
            ):
                state = coordinator.step(state)
            self.assertEqual(state["promotion_kind"], "implementation")
            self.assertEqual(state["candidate_review"]["candidate_commit"], "c" * 40)

    def test_shipped_workflow_resolves_trusted_pin_and_oracle(self):
        from automation.gfx_l32b_complete import Coordinator, default_state

        workflow_path = ROOT / "campaigns/eu4-l32b-complete/workflow.json"
        workflow = json.loads(workflow_path.read_text())
        checkpoint = _checkpoint(Path(tempfile.mkdtemp()))
        hooks = self._hooks(Path(tempfile.mkdtemp()), checkpoint)
        coordinator = Coordinator(
            repo=ROOT,
            workflow=workflow,
            state_path=Path(tempfile.mkdtemp()) / "workflow.json",
            hooks=hooks,
        )
        state = default_state(workflow)
        state = coordinator._preflight(state)
        self.assertNotEqual(state["trusted_contract"]["commit"], "PIN_AT_RUNTIME")
        self.assertEqual(len(state["oracle_manifest_sha256"] or ""), 64)

    def test_rejected_review_does_not_promote(self):
        from automation.gfx_l32b_complete import Coordinator

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            hooks = self._hooks(tmp, checkpoint)
            report = self._review(tmp / "review.json", "rejected", "c" * 40)
            coordinator = Coordinator(
                repo=tmp,
                workflow=self._workflow(),
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.load_or_init()
            state["state"] = "reviewing"
            state["review_target"] = "candidate"
            state["candidate"] = {"commit": "c" * 40}
            with mock.patch.object(
                hooks, "run_reviewer", lambda target, state: {"path": str(report)}
            ):
                state = coordinator.step(state)
            self.assertEqual(state["state"], "remediating")
            self.assertEqual(state["review"]["outcome"], "rejected")


@unittest.skipUnless(
    (ROOT / ".local/automation/eu4-l32b-effective-state/workspace").is_dir(),
    "authentic L3.2b-I capture/worktree is not available",
)
class LegacyRequalificationTests(unittest.TestCase):
    def test_v2_accepts_the_existing_l3_2b_i_capture(self):
        import automation.gfx_draw_accept_v2 as accept

        result = accept.check(
            ROOT / ".local/automation/eu4-l32b-effective-state/workspace",
            ROOT / "campaigns/eu4-l32b-effective-state",
        )
        self.assertEqual(result["verdict"], "pass", result["diagnostics"])


if __name__ == "__main__":
    unittest.main()
