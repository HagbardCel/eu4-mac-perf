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

ROOT = Path(__file__).resolve().parents[1]

# The candidate workspace is first on PYTHONPATH. Do not put the trusted
# checkout ahead of it: these tests must exercise the candidate modules.
import eu4_gfx_draw_packet as packet_mod  # noqa: E402


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


def _artifact(capture_dir: Path, master: bytes):
    draw = SimpleNamespace(
        call_id=11,
        symbol="_GfxDraw",
        caller_rva=0xEDFCD1,
        thread_id=7,
        completion_seq=3,
        payload_bytes=master,
        payload_file="draw.bin",
        payload_sha256=hashlib.sha256(master).hexdigest(),
        payload_size=len(master),
        meta={"qualified": True, "raw_count": 4, "effective_count": 4},
        fields_ok=True,
        resource_id="",
        contents_known=True,
    )
    return SimpleNamespace(
        session="session",
        pid=123,
        executable_sha256=(
            "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
        ),
        image_base=0x100000000,
        root=capture_dir,
        operations=[draw],
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
            "executable_sha256": (
                "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
            ),
            "image_base": "0x100000000",
        },
        "inputs": {},
    }


def _checkpoint(tmp: Path, *, field: str = "shader_identity") -> dict:
    from automation.gfx_checkpoint import write_checkpoint

    capture = tmp / "capture"
    capture.mkdir()
    (capture / "marker").write_text("x")
    packet_dir = tmp / "packet"
    packet_dir.mkdir()
    master = _master()
    packet = _packet(master)
    packet["inputs"][field] = {
        "status": "established",
        "value": {},
        "evidence": [],
    }
    (packet_dir / "draw-packet.json").write_text(
        json.dumps(packet, indent=2), encoding="utf-8"
    )
    return write_checkpoint(
        tmp,
        campaign_id="c1",
        candidate_base_commit="a" * 40,
        trusted_contract_commit="b" * 40,
        checker_sha256="c" * 64,
        contract_version="v0",
        contract_sha256=None,
        manifest_sha256="d" * 64,
        candidate_digest="e" * 64,
        observer_library_sha256="f" * 64,
        capture={
            "session": "session",
            "pid": 123,
            "executable_sha256": (
                "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
            ),
            "library_sha256": "f" * 64,
        },
        packet=packet,
        packet_dir=packet_dir,
        capture_dir=capture,
        target={"call_id": 11, "caller_rva": "0xedfcd1", "mode": 5, "effective_count": 4},
        fields=[{"name": field, "state": "provisional"}],
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
            self.assertIsNone(error)
            self.assertEqual(validated["provisional"], ["shader_identity"])
            self.assertEqual(validated["established"], [])

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


class CheckpointTests(unittest.TestCase):
    def test_bundle_survives_temporary_packet_cleanup(self):
        from automation.gfx_checkpoint import validate_checkpoint

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            self.assertTrue(Path(checkpoint["path"]).is_file())
            errors = validate_checkpoint(
                Path(checkpoint["path"]),
                expected_campaign_id="c1",
                expected_candidate_base="a" * 40,
            )
            self.assertEqual(errors, [])
            # The original packet directory is not part of the bundle's identity.
            import shutil

            shutil.rmtree(tmp / "packet")
            errors = validate_checkpoint(Path(checkpoint["path"]))
            self.assertEqual(errors, [])


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


class CoordinatorTests(unittest.TestCase):
    def _report(self, path: Path, outcome: str) -> Path:
        from automation.review_result import write_review

        findings = []
        if outcome == "rejected":
            findings = [
                {
                    "id": "F1",
                    "severity": "P1",
                    "kind": "evidence_gap",
                }
            ]
        write_review(
            path,
            {
                "schema": "eu4-review/v1",
                "candidate_commit": "c" * 40,
                "oracle_manifest_sha256": "d" * 64,
                "outcome": outcome,
                "findings": findings,
                "summary": "test",
            },
        )
        return path

    def _report_for(self, path: Path, outcome: str, candidate: str) -> Path:
        from automation.review_result import write_review

        write_review(
            path,
            {
                "schema": "eu4-review/v1",
                "candidate_commit": candidate,
                "oracle_manifest_sha256": "d" * 64,
                "outcome": outcome,
                "findings": [],
                "summary": "test",
            },
        )
        return path

    def test_fake_path_reaches_awaiting_merge(self):
        from automation.gfx_l32b_complete import Coordinator, Hooks, default_state

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            workspace = tmp / "workspace"
            workspace.mkdir()
            report = self._report(tmp / "review.json", "qualified")
            calls = {"implementer": 0, "reviewer": 0}

            def implementer(state):
                calls["implementer"] += 1
                if calls["implementer"] == 1:
                    return {
                        "status": "paused",
                        "last_verdict": "blocked",
                        "workspace": str(workspace),
                        "checkpoint": checkpoint["path"],
                        "campaign_id": "c1",
                        "candidate_base_commit": "a" * 40,
                        "trusted_contract_commit": "b" * 40,
                    }
                state["stage"] = "final"
                return {
                    "status": "complete",
                    "last_verdict": "pass",
                    "workspace": str(workspace),
                }

            hooks = Hooks(
                run_implementer=implementer,
                snapshot=lambda workspace, state: {"commit": "c" * 40},
                run_contract=lambda checkpoint, state: {
                    "status": "complete",
                    "candidate": {"commit": "d" * 40},
                },
                run_reviewer=lambda target, state: {
                    "path": str(self._report_for(
                        tmp / f"review-{calls['reviewer']}.json",
                        "qualified",
                        (target or {}).get("commit", "c" * 40),
                    )),
                    "session_id": f"rev{calls['reviewer']}",
                },
                integrate=lambda candidate, contract, state: {
                    "ok": True, "commit": "e" * 40
                },
            )
            workflow = {
                "schema": "eu4-l32b-workflow/v1",
                "workflow_id": "wf",
                "baseline_commit": "a" * 40,
            }
            coordinator = Coordinator(
                repo=tmp,
                workflow=workflow,
                state_path=tmp / "workflow.json",
                hooks=hooks,
            )
            state = coordinator.run()
            self.assertEqual(state["state"], "awaiting_merge")
            events = [row["event"] for row in state["history"]]
            self.assertIn("checkpoint", events)
            self.assertIn("integrated", events)

    def test_rejected_review_does_not_promote(self):
        from automation.gfx_l32b_complete import Coordinator, Hooks

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            report = self._report(tmp / "review.json", "rejected")
            state_path = tmp / "workflow.json"
            hooks = Hooks(
                run_implementer=lambda state: {
                    "status": "complete", "last_verdict": "pass",
                    "workspace": str(tmp),
                },
                snapshot=lambda workspace, state: {"commit": "c" * 40},
                run_contract=lambda checkpoint, state: {"status": "complete"},
                run_reviewer=lambda target, state: {"path": str(report)},
                integrate=lambda candidate, contract, state: {
                    "ok": True, "commit": "e" * 40
                },
                run_remediation=lambda state: {
                    "status": "complete", "last_verdict": "pass",
                    "workspace": str(tmp),
                },
            )
            workflow = {
                "schema": "eu4-l32b-workflow/v1",
                "workflow_id": "wf2",
                "baseline_commit": "a" * 40,
            }
            coordinator = Coordinator(
                repo=tmp, workflow=workflow, state_path=state_path, hooks=hooks
            )
            state = coordinator.load_or_init()
            state["state"] = "reviewing"
            state["candidate"] = {"commit": "c" * 40}
            state = coordinator.step(state)
            self.assertEqual(state["state"], "remediating")
            self.assertEqual(state["review"]["outcome"], "rejected")


class ContractRunnerTests(unittest.TestCase):
    def test_weak_validator_is_rejected_by_an_adversarial_case(self):
        from automation.gfx_contract_runner import run

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            checkpoint = _checkpoint(tmp)
            workspace = tmp / "candidate"
            (workspace / "automation" / "gfx_contracts").mkdir(parents=True)
            (workspace / "automation" / "__init__.py").write_text("")
            (workspace / "automation" / "gfx_contracts" / "__init__.py").write_text("")
            (workspace / "automation" / "gfx_contracts" / "v1.py").write_text(
                "VALIDATORS = {'shader_identity': lambda value, ctx: None}\n"
            )
            cases = tmp / "cases.json"
            cases.write_text(
                json.dumps(
                    {
                        "schema": "eu4-gfx-adversarial-cases/v1",
                        "candidate_commit": "c" * 40,
                        "fields": [
                            {
                                "field": "shader_identity",
                                "mutation_class": "wrong_selected_object",
                                "structural_validity": "valid",
                                "forged_value": {},
                                "forged_evidence": [],
                                "expected_error_prefix": "shader_identity",
                            }
                        ],
                    }
                )
            )
            master = _master()
            capture = tmp / "capture"
            artifact, draw = _artifact(capture, master)
            result = run(
                workspace,
                "v1",
                Path(checkpoint["path"]),
                cases_path=cases,
                artifact_loader=lambda _path: artifact,
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["cases"][0]["ok"], False)


if __name__ == "__main__":
    unittest.main()
