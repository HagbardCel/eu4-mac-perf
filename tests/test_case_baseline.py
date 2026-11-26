"""Identity-bound baseline reuse (M7-P2 B2)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from automation import (case_baseline, gfx_independent_replay,
                        replay_host_dependencies)


def _write_case(root: Path, *, legacy: bool = False) -> Path:
    case = root / "case"
    (case / "capture").mkdir(parents=True)
    (case / "replay").mkdir()
    (case / "shaders").mkdir()
    files = []

    def add(relative: str, payload: bytes) -> None:
        path = case / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        files.append({"path": relative, "bytes": len(payload),
                      "sha256": hashlib.sha256(payload).hexdigest()})

    add("capture/frame-seal.json", json.dumps({
        "session_id": "session-a", "capture_frame_id": 1,
        "event_stream_sha256": "a" * 64, "present_index": 5,
        "scene_index": 5,
    }).encode())
    add("capture/frame-trace.json", json.dumps({"complete": True}).encode())
    add("capture/frame-events.jsonl", b"")
    add("shaders/shaders.json", b"{}")
    add("shaders/program.metal", b"kernel")
    replay = {
        "status": "ok", "stage": "complete", "readback": True,
        "partial": False, "timed_out": False, "failed": 0,
        "command_status": 4, "events": 3, "encoded": 3,
        "checkpoints": [{"event": 0, "target": "0", "file": "event0.bgra"}]
    }
    if not legacy:
        replay["shader_compile_options"] = {"default_fast_math": True, "fast_math": True}
    add("replay/replay.json", json.dumps(replay).encode())
    add("replay/metal-frame.bgra", b"\0" * 16)
    add("replay/event0.bgra", b"checkpoint")
    replay_bytes = (case / "replay" / "replay.json").read_bytes()
    metal_bytes = (case / "replay" / "metal-frame.bgra").read_bytes()
    add("replay/producer-receipt.json", json.dumps({
        "schema": "gfx-replay-producer-receipt/v1",
        **({} if legacy else {"recipe": gfx_independent_replay.replay_recipe(env={})}),
        "producer": {
            "executable": "/tmp/gfx_frame_replay",
            "executable_sha256": "c" * 64,
            "build_fingerprint": "b" * 64,
            "build_stamp_sha256": "s" * 64,
        },
        "capture": {
            "session_id": "session-a", "capture_frame_id": 1,
            "event_stream_sha256": "a" * 64,
        },
        "result": {
            "replay_json_sha256": hashlib.sha256(replay_bytes).hexdigest(),
            "metal_frame_sha256": hashlib.sha256(metal_bytes).hexdigest(),
            "status": "ok", "stage": "complete", "readback": True,
            "partial": False, "timed_out": False, "failed": 0,
            "command_status": 4, "events": 3, "encoded": 3,
            "checkpoint_files": [{"file": "event0.bgra",
                                  "sha256": hashlib.sha256(b"checkpoint").hexdigest()}],
        },
    }).encode())
    add("diagnostic-report.json", json.dumps({
        "schema": "gfx-frame-diagnosis/v1",
        "classification": "difference_with_partial_state_coverage",
        "qualification": {"qualified": False, "diagnostic_qualified": True},
        "pixels": {"final": {"statistics": {"mismatched_pixels": 4,
                                            "max_channel_error": 2}}},
        "first_divergence": {"event": 2, "resource": "fb:0", "target": "0",
                             "producer_event": 1, "confidence": "confirmed"},
    }).encode())
    add("dependency-graph.json", json.dumps({"status": "complete"}).encode())
    add("graph.json", json.dumps({"status": "complete"}).encode())
    add("case.json", json.dumps({
        "schema": "gfx-strategy-b-case/v1", "case_id": "identity-case",
        "diagnostic_report": "diagnostic-report.json", "graph": "graph.json",
        "files": files,
    }).encode())
    return case


class BaselineIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.case = _write_case(self.root)
        self.baseline = self.root / "baseline.json"
        self._producer = mock.patch.object(
            case_baseline, "_current_producer",
            return_value={"executable": "/tmp/gfx_frame_replay",
                          "executable_sha256": "c" * 64,
                          "build_fingerprint": "b" * 64})
        self._producer.start()

    def tearDown(self):
        self._producer.stop()
        self.temporary.cleanup()

    def _rewrite_replay(self, **changes):
        import hashlib
        import json as _json
        replay_path = self.case / "replay" / "replay.json"
        replay = _json.loads(replay_path.read_bytes())
        replay.update(changes)
        replay_path.write_bytes(_json.dumps(replay).encode())
        receipt_path = self.case / "replay" / "producer-receipt.json"
        receipt = _json.loads(receipt_path.read_bytes())
        for key in ("status", "stage", "readback", "events", "encoded",
                    "failed", "command_status"):
            receipt["result"][key] = replay.get(key)
        receipt["result"]["replay_json_sha256"] = hashlib.sha256(
            replay_path.read_bytes()).hexdigest()
        receipt_path.write_bytes(_json.dumps(receipt).encode())
        manifest_path = self.case / "case.json"
        manifest = _json.loads(manifest_path.read_bytes())
        for item in manifest["files"]:
            target = self.case / item["path"]
            if item["path"] in ("replay/replay.json",
                                "replay/producer-receipt.json"):
                item["bytes"] = target.stat().st_size
                item["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest_path.write_bytes(_json.dumps(manifest).encode())

    def test_receipt_execution_fields_must_match_replay(self):
        import hashlib
        import json as _json
        from automation import gfx_replay_case

        receipt_path = self.case / "replay" / "producer-receipt.json"
        receipt = _json.loads(receipt_path.read_bytes())
        receipt["result"]["encoded"] = 0
        receipt_path.write_bytes(_json.dumps(receipt).encode())
        manifest_path = self.case / "case.json"
        manifest = _json.loads(manifest_path.read_bytes())
        for item in manifest["files"]:
            if item["path"] == "replay/producer-receipt.json":
                payload = receipt_path.read_bytes()
                item["bytes"] = len(payload)
                item["sha256"] = hashlib.sha256(payload).hexdigest()
        manifest_path.write_bytes(_json.dumps(manifest).encode())
        verification = gfx_replay_case.verify(self.case)
        self.assertEqual(verification["producer_identity"]["status"], "invalid")
        with self.assertRaises(case_baseline.BaselineError):
            case_baseline.record(self.case, self.baseline)

    def test_partial_full_frame_is_not_reusable(self):
        self._rewrite_replay(partial=True)
        with self.assertRaises(case_baseline.BaselineError) as caught:
            case_baseline.record(self.case, self.baseline)
        self.assertEqual(caught.exception.kind, "replay_incomplete")

    def test_short_encoded_count_is_not_reusable(self):
        replay = json.loads((self.case / "replay" / "replay.json").read_text())
        self._rewrite_replay(encoded=replay["events"] - 1)
        with self.assertRaises(case_baseline.BaselineError) as caught:
            case_baseline.record(self.case, self.baseline)
        self.assertEqual(caught.exception.kind, "replay_incomplete")

    def test_nonzero_failed_count_is_not_reusable(self):
        self._rewrite_replay(failed=1)
        with self.assertRaises(case_baseline.BaselineError) as caught:
            case_baseline.record(self.case, self.baseline)
        self.assertEqual(caught.exception.kind, "replay_incomplete")

    def test_timeout_is_not_reusable(self):
        self._rewrite_replay(timed_out=True)
        with self.assertRaises(case_baseline.BaselineError) as caught:
            case_baseline.record(self.case, self.baseline)
        self.assertEqual(caught.exception.kind, "replay_incomplete")

    def test_unsuccessful_command_buffer_is_not_reusable(self):
        self._rewrite_replay(command_status=5)
        with self.assertRaises(case_baseline.BaselineError) as caught:
            case_baseline.record(self.case, self.baseline)
        self.assertEqual(caught.exception.kind, "replay_incomplete")

    def test_record_and_check_reuses_the_same_identity(self):
        recorded = case_baseline.record(self.case, self.baseline)
        checked = case_baseline.load_check(self.case, self.baseline)
        self.assertTrue(checked["ok"], checked)
        self.assertTrue(checked["reusable"])
        self.assertEqual(checked["current_fingerprint"],
                         recorded["identity"]["fingerprint"])
        self.assertEqual(recorded["result"]["first_divergence"]["event"], 2)
        self.assertEqual(recorded["identity"]["validity"]["payload_validation"],
                         "ok")
        self.assertEqual(recorded["identity"]["validity"]["replay_status"],
                         "ok")

    def test_legacy_replay_remains_inspectable_but_cannot_be_reused(self):
        legacy = _write_case(self.root / "legacy", legacy=True)
        verified = case_baseline.gfx_replay_case.verify(legacy)
        self.assertTrue(verified["ok"], verified)
        self.assertEqual(verified["producer_identity"]["status"], "verified")
        case_baseline.record(self.case, self.baseline)
        with self.assertRaisesRegex(case_baseline.BaselineError,
                                    "invalid_shader_compile_options"):
            case_baseline.load_check(legacy, self.baseline)

    def test_baseline_reuse_requires_same_effective_math(self):
        case_baseline.record(self.case, self.baseline)
        with mock.patch.dict(os.environ, {"EU4_FRAME_REPLAY_FAST_MATH": "1"}):
            self.assertTrue(case_baseline.load_check(
                self.case, self.baseline)["reusable"])
        with mock.patch.dict(os.environ, {"EU4_FRAME_REPLAY_FAST_MATH": "0"}):
            with self.assertRaisesRegex(case_baseline.BaselineError,
                                        "shader_compile_mode_mismatch"):
                case_baseline.load_check(self.case, self.baseline)

    def test_portable_identity_uses_quoted_include_fallback(self):
        with mock.patch.object(replay_host_dependencies.sys, "platform",
                               "linux"):
            identity = replay_host_dependencies.build_identity()
            recorded = case_baseline.record(self.case, self.baseline)
            checked = case_baseline.load_check(self.case, self.baseline)
        self.assertEqual(identity["dependency_authority"], "quoted-include")
        self.assertEqual(identity["platform"], "linux")
        self.assertTrue(checked["ok"], checked)
        self.assertEqual(checked["current_fingerprint"],
                         recorded["identity"]["fingerprint"])

    def test_identity_runs_one_authoritative_payload_pass(self):
        original = case_baseline.gfx_replay_case.verify
        with mock.patch.object(case_baseline.gfx_replay_case, "verify",
                               wraps=original) as verify:
            case_baseline.identity(self.case)
        verify.assert_called_once()

    def test_edited_shader_payload_without_manifest_change_is_rejected(self):
        case_baseline.record(self.case, self.baseline)
        (self.case / "shaders" / "program.metal").write_bytes(b"changed")
        with self.assertRaises(case_baseline.BaselineError) as caught:
            case_baseline.load_check(self.case, self.baseline)
        self.assertEqual(caught.exception.kind, "payload_validation")
        self.assertIn("program.metal", caught.exception.detail)

    def test_deleted_declared_payload_is_rejected(self):
        case_baseline.record(self.case, self.baseline)
        (self.case / "shaders" / "program.metal").unlink()
        with self.assertRaises(case_baseline.BaselineError):
            case_baseline.load_check(self.case, self.baseline)

    def test_corrupt_replay_readback_is_rejected(self):
        case_baseline.record(self.case, self.baseline)
        (self.case / "replay" / "metal-frame.bgra").write_bytes(b"\1" * 16)
        with self.assertRaises(case_baseline.BaselineError):
            case_baseline.load_check(self.case, self.baseline)

    def test_missing_replay_stage_is_incomplete(self):
        case_baseline.record(self.case, self.baseline)
        result = json.loads((self.case / "replay" / "replay.json").read_text())
        result.pop("stage", None)
        (self.case / "replay" / "replay.json").write_bytes(
            json.dumps(result).encode())
        manifest = json.loads((self.case / "case.json").read_text())
        for item in manifest["files"]:
            if item["path"] == "replay/replay.json":
                payload = (self.case / "replay" / "replay.json").read_bytes()
                item["bytes"] = len(payload)
                item["sha256"] = hashlib.sha256(payload).hexdigest()
        (self.case / "case.json").write_text(json.dumps(manifest))
        with self.assertRaises(case_baseline.BaselineError) as caught:
            case_baseline.load_check(self.case, self.baseline)
        self.assertIn(caught.exception.kind,
                      ("replay_incomplete", "case_verification"))

    def test_changed_replay_status_invalidates_the_baseline(self):
        case_baseline.record(self.case, self.baseline)
        result = json.loads((self.case / "replay" / "replay.json").read_text())
        result["status"] = "partial"
        (self.case / "replay" / "replay.json").write_bytes(
            json.dumps(result).encode())
        manifest = json.loads((self.case / "case.json").read_text())
        for item in manifest["files"]:
            if item["path"] == "replay/replay.json":
                payload = (self.case / "replay" / "replay.json").read_bytes()
                item["bytes"] = len(payload)
                item["sha256"] = hashlib.sha256(payload).hexdigest()
        (self.case / "case.json").write_text(json.dumps(manifest))
        with self.assertRaises(case_baseline.BaselineError) as caught:
            case_baseline.load_check(self.case, self.baseline)
        self.assertIn(caught.exception.kind,
                      ("replay_incomplete", "case_verification"))

    def test_modifying_renderer_semantics_invalidates_reuse(self):
        case_baseline.record(self.case, self.baseline)
        baseline = json.loads(self.baseline.read_text())
        original = case_baseline.identity(self.case)
        changed = dict(original)
        sources = dict(original["renderer_sources"])
        self.assertIn("renderer/engine/eu4_gfx_replay_semantics.h", sources)
        sources["renderer/engine/eu4_gfx_replay_semantics.h"] = "f" * 64
        changed["renderer_sources"] = sources
        changed["fingerprint"] = hashlib.sha256(json.dumps(
            {key: value for key, value in changed.items()
             if key != "fingerprint"},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        checked = case_baseline.evaluate(changed, baseline)
        self.assertFalse(checked["reusable"])
        self.assertTrue(any(problem["code"] == "baseline_mismatch"
                            for problem in checked["problems"]))

    def test_renderer_closure_tracks_a_semantics_header_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            bench = root / "benchmark"
            bench.mkdir()
            semantics = bench / "eu4_gfx_replay_semantics.h"
            semantics.write_text("first semantics")
            (bench / "root.mm").write_text('#include "eu4_gfx_replay_semantics.h"\n')
            with mock.patch.object(case_baseline, "ROOT", root):
                first = case_baseline._renderer_closure(
                    ("benchmark/root.mm",))
                semantics.write_text("changed semantics")
                second = case_baseline._renderer_closure(
                    ("benchmark/root.mm",))
        self.assertIn("benchmark/eu4_gfx_replay_semantics.h", first)
        self.assertNotEqual(first, second)

    def test_changed_checkpoint_is_invalid(self):
        from automation import gfx_replay_case
        (self.case / "replay" / "event0.bgra").write_bytes(b"changed")
        manifest = json.loads((self.case / "case.json").read_text())
        result = gfx_replay_case.verify_producer_receipt(self.case, manifest)
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(any("digest mismatch" in problem
                            for problem in result["problems"]), result)

    def test_missing_replay_json_is_invalid_not_an_exception(self):
        from automation import gfx_replay_case

        (self.case / "replay" / "replay.json").unlink()
        manifest = json.loads((self.case / "case.json").read_text())
        result = gfx_replay_case.verify_producer_receipt(self.case, manifest)
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(any("no replay.json" in problem
                            for problem in result["problems"]), result)

    def test_external_replay_override_cannot_be_reused(self):
        case_baseline.record(self.case, self.baseline)
        external = self.root / "external-replay"
        import shutil
        shutil.copytree(self.case / "replay", external)
        with self.assertRaises(case_baseline.BaselineError) as caught:
            case_baseline.load_check(self.case, self.baseline,
                                     replay_dir=external)
        self.assertEqual(caught.exception.kind, "external_replay_override")

    def test_producer_receipt_must_bind_the_replay_output(self):
        receipt_path = self.case / "replay" / "producer-receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["result"]["replay_json_sha256"] = "0" * 64
        receipt_path.write_text(json.dumps(receipt))
        with self.assertRaises(case_baseline.BaselineError) as caught:
            case_baseline.record(self.case, self.baseline)
        self.assertIn(caught.exception.kind,
                      ("case_verification", "producer_identity_invalid"))

    def test_producer_binary_change_rejects_reuse(self):
        case_baseline.record(self.case, self.baseline)
        with mock.patch.object(
                case_baseline, "_current_producer",
                return_value={"executable": "/tmp/gfx_frame_replay",
                              "executable_sha256": "d" * 64,
                              "build_fingerprint": "b" * 64}):
            with self.assertRaises(case_baseline.BaselineError) as caught:
                case_baseline.load_check(self.case, self.baseline)
        self.assertEqual(caught.exception.kind, "producer_mismatch")

    def test_legacy_case_without_producer_receipt_cannot_be_reused(self):
        receipt = self.case / "replay" / "producer-receipt.json"
        receipt.unlink()
        manifest = json.loads((self.case / "case.json").read_text())
        manifest["files"] = [item for item in manifest["files"]
                             if item["path"] != "replay/producer-receipt.json"]
        (self.case / "case.json").write_text(json.dumps(manifest))
        with self.assertRaises(case_baseline.BaselineError) as caught:
            case_baseline.record(self.case, self.baseline)
        self.assertEqual(caught.exception.kind, "producer_identity_missing")

    def test_changing_required_build_dependency_invalidates_reuse(self):
        case_baseline.record(self.case, self.baseline)
        baseline = json.loads(self.baseline.read_text())
        current = case_baseline.identity(self.case)
        sources = dict(current["implementation_sources"])
        self.assertIn("automation/gfx_independent_replay.py", sources)
        sources["automation/gfx_independent_replay.py"] = "e" * 64
        current["implementation_sources"] = sources
        current["fingerprint"] = hashlib.sha256(json.dumps(
            {key: value for key, value in current.items()
             if key != "fingerprint"},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        checked = case_baseline.evaluate(current, baseline)
        self.assertFalse(checked["reusable"])

    def test_changing_policy_invalidates_reuse(self):
        case_baseline.record(self.case, self.baseline)
        other_policy = self.root / "other-policy.json"
        other_policy.write_text('{"policy": "different"}')
        checked = case_baseline.load_check(
            self.case, self.baseline, policy=other_policy)
        self.assertFalse(checked["reusable"])
        self.assertTrue(any(problem["code"] == "baseline_mismatch"
                            for problem in checked["problems"]))

    def test_a_different_backend_never_reuses(self):
        case_baseline.record(self.case, self.baseline)
        checked = case_baseline.load_check(self.case, self.baseline,
                                           backend="opengl")
        self.assertFalse(checked["reusable"])
        self.assertTrue(any(problem["code"] == "baseline_backend"
                            for problem in checked["problems"]))

    def test_invalid_case_cannot_be_recorded(self):
        (self.case / "graph.json").unlink()
        with self.assertRaises(case_baseline.BaselineError):
            case_baseline.record(self.case, self.baseline)
        self.assertFalse(self.baseline.exists())

    def test_fingerprint_match_is_not_enough_without_validity(self):
        case_baseline.record(self.case, self.baseline)
        baseline = json.loads(self.baseline.read_text())
        current = case_baseline.identity(self.case)
        current["validity"] = dict(current["validity"], verified=False)
        checked = case_baseline.evaluate(current, baseline)
        self.assertFalse(checked["reusable"])
        self.assertTrue(any(problem["code"] == "current_not_verified"
                            for problem in checked["problems"]))

    def test_stored_baseline_without_validity_is_not_reusable(self):
        case_baseline.record(self.case, self.baseline)
        baseline = json.loads(self.baseline.read_text())
        baseline["identity"]["validity"]["verified"] = False
        current = case_baseline.identity(self.case)
        checked = case_baseline.evaluate(current, baseline)
        self.assertFalse(checked["reusable"])
        self.assertTrue(any(problem["code"] == "baseline_not_verified"
                            for problem in checked["problems"]))

    def test_missing_baseline_is_refused(self):
        with self.assertRaises(case_baseline.BaselineError):
            case_baseline.load_check(self.case, self.root / "absent.json")

    def test_baseline_is_not_claimed_as_a_qualified_match(self):
        recorded = case_baseline.record(self.case, self.baseline)
        self.assertFalse(recorded["result"]["qualified"])
        self.assertTrue(recorded["result"]["diagnostic_qualified"])


if __name__ == "__main__":
    unittest.main()
