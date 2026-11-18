"""Fail-closed regressions for the M7-P1 pilot conclusion contract."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from automation import dsh_pilot

FRAME = {
    "session_id": "a3111e0d-9b76-4b23-910d-38bd48c9aa93",
    "capture_frame_id": 1,
    "event_stream_sha256": "62cfdae4" + "0" * 56,
}
OTHER_FRAME = dict(FRAME, session_id="another-launch")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def conclusion():
    return {
        "schema": "gfx-pilot-conclusion/v1",
        "case": {
            "case_id": "m7-live-frame-000001",
            "verification_ok": True,
            "sealed_frame": dict(FRAME),
        },
        "starting_diagnosis": {
            "classification": "difference_with_partial_state_coverage",
            "first_divergence": {"event": 142, "resource": "fb:0"},
            "root_cause": "not_established",
        },
        "hypotheses": [{
            "id": "H1",
            "statement": "the unsupported dither state explains the residual",
            "status": "insufficient",
            "evidence": ["event 142 checkpoint: 871620 pixels differ"],
            "test": {"method": "offline histogram of the checkpoint pair",
                     "result": "difference exceeds a 1-LSB dither pattern"},
        }],
        "outcome": "requires_additional_evidence",
        "evidence_request": {
            "reason": "no checkpoint exists between events 0 and 142",
            "frame_scope": "new_capture",
            "cross_frame_caveat": (
                "event 71 in a new frame cannot bound event 142 in the sealed "
                "frame without proven equivalent starting state"),
            "checkpoints": [{"event": 71, "resource": "fb:0",
                             "why": "midpoint bisection of the unbounded interval"}],
        },
        "remaining_uncertainty": ["the causal interval 0-142 is unresolved"],
        "next_experiment": "capture fb:0 at event 71 with both events sealed",
    }


def endpoint(event, resource, status, *, capture=FRAME, authoritative=True,
             generation=None, target=None):
    value = {
        "event": event,
        "resource": resource,
        "target": target if target is not None else (
            "0" if resource == "fb:0" else resource.split(":", 1)[1]),
        "status": status,
        "authoritative": authoritative,
        "capture": dict(capture),
    }
    if generation is not None:
        value["generation"] = generation
    return value


def interval(*, start=71, end=142, resource="fb:0", lower_bound=None,
             capture=FRAME, lower_status="match", upper_status="divergent",
             lower_authoritative=True, upper_authoritative=True,
             lower_generation=1, upper_generation=1):
    basis = []
    if start is not None:
        basis.append(endpoint(start, resource, lower_status, capture=capture,
                              authoritative=lower_authoritative,
                              generation=lower_generation))
    basis.append(endpoint(end, resource, upper_status, capture=capture,
                          authoritative=upper_authoritative,
                          generation=upper_generation))
    return {
        "start_event": start,
        "end_event": end,
        "lower_bound": lower_bound if lower_bound is not None else (
            "same_resource_match" if start is not None else "unbounded"),
        "same_capture": True,
        "basis": basis,
    }


def authoritative_evidence(*, capture=FRAME, checkpoints=None):
    return {
        "schema": "gfx-pilot-authoritative-checkpoints/v1",
        "capture": dict(capture),
        "report_sha256": "e" * 64,
        "checkpoints": checkpoints if checkpoints is not None else [
            endpoint(71, "fb:0", "match"),
            endpoint(142, "fb:0", "divergent"),
        ],
    }


def correction_conclusion(interval_value):
    value = conclusion()
    value["outcome"] = "correction_proposed"
    del value["evidence_request"]
    value["hypotheses"][0]["status"] = "supported"
    value["correction"] = {
        "files": ["benchmark/eu4_gfx_frame_replay.mm"],
        "description": "minimal correction",
        "validation": {
            "same_sealed_case": True,
            "before": {"mismatched_pixels": 100, "max_channel_error": 4},
            "after": {"mismatched_pixels": 10, "max_channel_error": 2},
            "replay_artifact": "replay-after/replay.json",
            "exact_match_regressions": [
                {"case": "canonical-title", "mismatched_pixels": 0}],
        },
    }
    value["causal_interval"] = interval_value
    return value


def problem_codes(result):
    return {item["code"] for item in result["problems"]}


class PilotConclusionTests(unittest.TestCase):
    def test_minimal_evidence_request_is_valid(self):
        result = dsh_pilot.validate_conclusion(conclusion())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["checks"]["outcome"],
                         "requires_additional_evidence")

    def test_cross_capture_interval_is_rejected(self):
        value = conclusion()
        value["causal_interval"] = interval(capture=OTHER_FRAME)
        result = dsh_pilot.validate_conclusion(value)
        self.assertFalse(result["ok"])
        self.assertIn("cross_capture_interval", problem_codes(result))

    def test_interval_basis_must_match_the_conclusion_frame(self):
        value = conclusion()
        value["causal_interval"] = interval(
            capture=dict(FRAME, capture_frame_id=2))
        result = dsh_pilot.validate_conclusion(value)
        self.assertFalse(result["ok"])
        self.assertIn("cross_capture_interval", problem_codes(result))

    def test_unresolved_pilot_cannot_claim_a_lower_bound(self):
        value = conclusion()
        value["causal_interval"] = interval()
        result = dsh_pilot.validate_conclusion(value)
        self.assertFalse(result["ok"])
        self.assertIn("unresolved_interval_claimed", problem_codes(result))

    def test_new_capture_requires_a_cross_frame_caveat(self):
        value = conclusion()
        del value["evidence_request"]["cross_frame_caveat"]
        result = dsh_pilot.validate_conclusion(value)
        self.assertFalse(result["ok"])
        self.assertIn("evidence_request_cross_frame", problem_codes(result))

    def test_hypothesis_budget_is_bounded(self):
        value = conclusion()
        value["hypotheses"] = [dict(value["hypotheses"][0])
                               for _ in range(4)]
        result = dsh_pilot.validate_conclusion(value)
        self.assertFalse(result["ok"])
        self.assertIn("hypothesis_budget", problem_codes(result))

    def test_non_improving_correction_is_rejected(self):
        value = conclusion()
        value["outcome"] = "correction_proposed"
        del value["evidence_request"]
        value["hypotheses"][0]["status"] = "supported"
        value["correction"] = {
            "files": ["benchmark/eu4_gfx_frame_replay.mm"],
            "description": "apply the captured dither pattern",
            "validation": {
                "same_sealed_case": True,
                "before": {"mismatched_pixels": 1160291,
                           "max_channel_error": 70},
                "after": {"mismatched_pixels": 1160291,
                          "max_channel_error": 70},
                "replay_artifact": "replay-after/replay.json",
                "exact_match_regressions": [
                    {"case": "canonical-title", "mismatched_pixels": 0}],
            },
        }
        result = dsh_pilot.validate_conclusion(value)
        self.assertFalse(result["ok"])
        self.assertIn("correction_not_improving", problem_codes(result))

    def test_absolute_correction_path_is_rejected(self):
        value = conclusion()
        value["outcome"] = "correction_proposed"
        del value["evidence_request"]
        value["hypotheses"][0]["status"] = "supported"
        value["correction"] = {
            "files": ["/tmp/evil.mm"],
            "description": "patch",
            "validation": {
                "same_sealed_case": True,
                "before": {"mismatched_pixels": 10, "max_channel_error": 3},
                "after": {"mismatched_pixels": 2, "max_channel_error": 1},
                "replay_artifact": "replay-after/replay.json",
                "exact_match_regressions": [
                    {"case": "canonical-title", "mismatched_pixels": 0}],
            },
        }
        result = dsh_pilot.validate_conclusion(value)
        self.assertFalse(result["ok"])
        self.assertIn("correction_files", problem_codes(result))

    def test_valid_bounded_interval_supports_a_correction(self):
        result = dsh_pilot.validate_conclusion(
            correction_conclusion(interval()),
            authoritative=authoritative_evidence())
        self.assertTrue(result["ok"], result)

    def test_texture_match_cannot_bound_a_framebuffer_divergence(self):
        value = correction_conclusion(interval())
        value["causal_interval"]["basis"][0]["resource"] = "tex:431"
        value["causal_interval"]["basis"][0]["target"] = "431"
        result = dsh_pilot.validate_conclusion(
            value, authoritative=authoritative_evidence())
        self.assertFalse(result["ok"])
        self.assertIn("mixed_resource_interval", problem_codes(result))
        self.assertIn("interval_resource_mismatch", problem_codes(result))

    def test_bounded_interval_without_authoritative_evidence_is_rejected(self):
        result = dsh_pilot.validate_conclusion(
            correction_conclusion(interval()))
        self.assertFalse(result["ok"])
        self.assertIn("interval_unverified", problem_codes(result))

    def test_fabricated_same_capture_lower_endpoint_is_rejected(self):
        # The structural basis is internally consistent, but event 71 is not
        # in the case's authoritative checkpoint evidence.
        evidence = authoritative_evidence(checkpoints=[
            endpoint(142, "fb:0", "divergent")])
        result = dsh_pilot.validate_conclusion(
            correction_conclusion(interval()), authoritative=evidence)
        self.assertFalse(result["ok"])
        self.assertIn("interval_unverified", problem_codes(result))

    def test_authoritative_status_contradiction_is_rejected(self):
        evidence = authoritative_evidence(checkpoints=[
            endpoint(71, "fb:0", "divergent"),
            endpoint(142, "fb:0", "divergent")])
        result = dsh_pilot.validate_conclusion(
            correction_conclusion(interval()), authoritative=evidence)
        self.assertFalse(result["ok"])
        self.assertIn("interval_unverified", problem_codes(result))

    def test_authoritative_evidence_from_another_capture_is_rejected(self):
        evidence = authoritative_evidence(capture=OTHER_FRAME)
        result = dsh_pilot.validate_conclusion(
            correction_conclusion(interval()), authoritative=evidence)
        self.assertFalse(result["ok"])
        self.assertIn("interval_unverified", problem_codes(result))

    def test_cli_exit_codes_follow_the_verdict(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "conclusion.json"
            path.write_text(json.dumps(conclusion()), encoding="utf-8")
            good = subprocess.run(
                [sys.executable, "-m", "automation.dsh_pilot", "validate",
                 "--conclusion", str(path)],
                capture_output=True, text=True, check=False)
            self.assertEqual(good.returncode, 0, good.stderr)
            value = conclusion()
            del value["remaining_uncertainty"]
            path.write_text(json.dumps(value), encoding="utf-8")
            bad = subprocess.run(
                [sys.executable, "-m", "automation.dsh_pilot", "validate",
                 "--conclusion", str(path)],
                capture_output=True, text=True, check=False)
            self.assertEqual(bad.returncode, 1, bad.stderr)


class CausalIntervalBasisTests(unittest.TestCase):
    def check(self, value, expected_code=None):
        problems = []
        dsh_pilot._valid_basis(
            value, problems, sealed_frame_key=dsh_pilot._capture_key(FRAME),
            expected_resource="fb:0")
        codes = {item["code"] for item in problems}
        if expected_code is None:
            self.assertEqual(problems, [], problems)
        else:
            self.assertIn(expected_code, codes, problems)
        return codes

    def test_same_resource_bounds_are_valid(self):
        self.check(interval())

    def test_unbounded_interval_needs_only_the_divergent_upper(self):
        self.check(interval(start=None, lower_bound="unbounded"))

    def test_unbounded_interval_rejects_an_invented_match(self):
        value = interval(start=None, lower_bound="unbounded")
        value["basis"].insert(0, endpoint(2, "fb:0", "match"))
        self.check(value, "interval_upper_endpoint")

    def test_unbounded_interval_rejects_a_matching_lower_bound_claim(self):
        value = interval(start=None, lower_bound="same_resource_match")
        self.check(value, "unresolved_interval_claimed")

    def test_mixed_resource_bounds_are_rejected(self):
        value = interval()
        value["basis"][0] = endpoint(71, "tex:431", "match")
        self.check(value, "mixed_resource_interval")

    def test_unrelated_target_is_rejected(self):
        value = interval()
        value["basis"][0]["target"] = "999"
        self.check(value, "interval_basis_target")

    def test_cross_capture_bounds_are_rejected(self):
        self.check(interval(capture=OTHER_FRAME), "cross_capture_interval")

    def test_missing_lower_endpoint_is_rejected(self):
        value = interval()
        value["basis"] = [value["basis"][1]]
        self.check(value, "interval_lower_endpoint")

    def test_missing_upper_endpoint_is_rejected(self):
        value = interval()
        value["basis"] = [value["basis"][0]]
        self.check(value, "interval_upper_endpoint")

    def test_wrong_endpoint_statuses_are_rejected(self):
        self.check(interval(lower_status="divergent"),
                   "interval_lower_status")
        self.check(interval(upper_status="match"), "interval_upper_status")

    def test_non_authoritative_endpoints_are_rejected(self):
        self.check(interval(lower_authoritative=False),
                   "interval_basis_not_authoritative")
        self.check(interval(upper_authoritative=False),
                   "interval_basis_not_authoritative")

    def test_duplicate_observations_are_rejected(self):
        value = interval()
        value["basis"].append(dict(value["basis"][0]))
        self.check(value, "interval_duplicate")

    def test_generation_mismatch_is_rejected(self):
        self.check(interval(lower_generation=1, upper_generation=2),
                   "interval_generation_mismatch")

    def test_generation_on_only_one_endpoint_is_incomplete(self):
        self.check(interval(lower_generation=None, upper_generation=1),
                   "interval_generation_incomplete")


def events(names, *, status="completed"):
    stream = []
    for index, name in enumerate(names):
        call_id = f"call-{index}"
        stream.append({"type": "tool_call", "callId": call_id, "tool": name,
                       "input": {}})
        stream.append({"type": "tool_result", "callId": call_id,
                       "status": status, "result": "{}"})
    return stream


class NativeGateTests(unittest.TestCase):
    def test_native_only_execution_passes(self):
        result = dsh_pilot.gate_events(
            events(["eu4_case_inspect", "eu4_replay"]))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["native_executed"],
                         ["eu4_case_inspect", "eu4_replay"])

    def test_empty_trace_fails(self):
        result = dsh_pilot.gate_events([])
        self.assertFalse(result["ok"])
        self.assertIn("no_native_tool",
                      {item["code"] for item in result["problems"]})

    def test_bash_only_trace_fails(self):
        result = dsh_pilot.gate_events(events(["bash", "read_image"]))
        self.assertFalse(result["ok"])
        self.assertIn("no_native_tool",
                      {item["code"] for item in result["problems"]})
        self.assertEqual(result["native_executed"], [])

    def test_mixed_trace_passes_for_the_native_part(self):
        result = dsh_pilot.gate_events(
            events(["bash", "eu4_case_inspect", "read_image"]))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["native_executed"], ["eu4_case_inspect"])

    def test_failed_native_call_does_not_count_as_executed(self):
        stream = [
            {"type": "tool_call", "callId": "native", "tool": "eu4_case_inspect"},
            {"type": "tool_result", "callId": "native", "status": "error"},
            {"type": "tool_call", "callId": "shell", "tool": "bash"},
            {"type": "tool_result", "callId": "shell", "status": "completed"},
        ]
        result = dsh_pilot.gate_events(stream)
        self.assertFalse(result["ok"])
        codes = {item["code"] for item in result["problems"]}
        self.assertIn("no_native_tool", codes)
        self.assertIn("failed_native_tool", codes)

    def test_required_native_tool_must_execute(self):
        result = dsh_pilot.gate_events(
            events(["eu4_case_inspect"]), required=["eu4_replay"])
        self.assertFalse(result["ok"])
        self.assertIn("missing_required_tool",
                      {item["code"] for item in result["problems"]})

    def test_unknown_required_tool_is_rejected(self):
        result = dsh_pilot.gate_events(
            events(["eu4_case_inspect"]), required=["not_a_tool"])
        self.assertFalse(result["ok"])
        self.assertIn("unknown_required_tool",
                      {item["code"] for item in result["problems"]})

    def test_native_tool_definition_is_the_existing_one(self):
        self.assertEqual(
            dsh_pilot._native_tool_names(),
            frozenset({"eu4_re_query", "eu4_session", "eu4_capture",
                       "eu4_diff_run", "eu4_diff_inspect", "eu4_replay",
                       "eu4_case_inspect"}))

    def test_gate_cli_accepts_native_and_rejects_bash_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            path.write_text("\n".join(json.dumps(item) for item in
                                      events(["eu4_case_inspect"])),
                            encoding="utf-8")
            good = subprocess.run(
                [sys.executable, "-m", "automation.dsh_pilot", "gate",
                 "--events", str(path)],
                capture_output=True, text=True, check=False)
            self.assertEqual(good.returncode, 0, good.stderr)
            path.write_text("\n".join(json.dumps(item) for item in
                                      events(["bash"])), encoding="utf-8")
            bad = subprocess.run(
                [sys.executable, "-m", "automation.dsh_pilot", "gate",
                 "--events", str(path)],
                capture_output=True, text=True, check=False)
            self.assertEqual(bad.returncode, 2, bad.stderr)


class PilotSummaryTests(unittest.TestCase):
    """Acceptance requires verified case evidence, not the conclusion's flag."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run = self.root / "run"
        self.run.mkdir()
        self.case = self.root / "case"
        (self.case / "capture").mkdir(parents=True)
        self.receipt_path = self.root / "case-receipt.json"
        self.manifest = {
            "schema": "gfx-strategy-b-case/v1",
            "case_id": "m7-live-frame-000001",
            "diagnostic_report": "diagnostic-report.json",
            "files": [],
        }
        self.seal = {
            "session_id": FRAME["session_id"],
            "capture_frame_id": FRAME["capture_frame_id"],
            "event_stream_sha256": FRAME["event_stream_sha256"],
            "manifest_sha256": "9" * 64,
            "reference_sha256": "8" * 64,
            "present_index": 617,
            "scene_index": 617,
        }
        self.report = {
            "schema": "gfx-frame-diagnosis/v1",
            "identity": {
                "status": "verified",
                "checks": [
                    {"check": "session_id",
                     "expected": FRAME["session_id"],
                     "actual": FRAME["session_id"], "ok": True},
                    {"check": "capture_frame_id", "expected": 1,
                     "actual": 1, "ok": True},
                ],
            },
            "frame": {
                "capture_frame_id": 1,
                "present_index": 617,
                "scene_index": 617,
                "manifest_sha256": "9" * 64,
                "reference_sha256": "8" * 64,
            },
            "checkpoints": [
                {"event": 11, "resource": "tex:431", "target": "431",
                 "status": "match", "authoritative": True},
                {"event": 142, "resource": "fb:0", "target": "0",
                 "status": "divergent", "authoritative": True},
            ],
        }
        self.write_case()
        self.conclusion_path = self.root / "conclusion.json"
        self.write_conclusion()
        self.session = {
            "schema": "eu4-dsh-pilot-run/v1",
            "session_id": "session-test",
            "tool_calls": ["eu4_case_inspect", "eu4_replay"],
        }
        self.efficiency = {
            "schema": "dsh-efficiency/v1",
            "session": {"cwd": "<home>/repo"},
            "window": {"turns": 1, "elapsed_seconds": 12.0, "model_calls": 3},
            "calls": {"total": 2, "by_tool": {"eu4_case_inspect": 1,
                                              "eu4_replay": 1},
                      "failed": 0},
            "repeated_calls": [],
            "output": {"total_chars": 1000, "oversized_count": 0},
            "wait": {"tool_wait_seconds": 1.5},
            "tests": {"test_invocations": 0},
            "eu4_launches": {"count": 0},
            "tokens": {"input": 10, "cache_read": 20, "output": 5,
                       "total": 35},
        }

    def tearDown(self):
        self.temporary.cleanup()

    def write_case(self):
        payload = b"\x00"
        (self.case / "payload.bin").write_bytes(payload)
        seal_bytes = json.dumps(self.seal).encode("utf-8")
        (self.case / "capture" / "frame-seal.json").write_bytes(seal_bytes)
        report_bytes = json.dumps(self.report).encode("utf-8")
        (self.case / "diagnostic-report.json").write_bytes(report_bytes)
        self.manifest["files"] = [
            {"path": "payload.bin", "bytes": len(payload),
             "sha256": _sha(payload)},
            {"path": "diagnostic-report.json", "bytes": len(report_bytes),
             "sha256": _sha(report_bytes)},
        ]
        (self.case / "case.json").write_text(
            json.dumps(self.manifest), encoding="utf-8")

    def refresh_binding(self):
        self.write_case()
        digests = dsh_pilot.case_receipt(self.case)
        value = conclusion()
        value["case"]["case_manifest_sha256"] = digests["case_manifest_sha256"]
        value["case"]["seal_sha256"] = digests["seal_sha256"]
        self.conclusion_path.write_text(json.dumps(value), encoding="utf-8")
        self.write_receipt(digests)
        return digests

    def write_receipt(self, digests, *, ok=True, files=None,
                      independent=True):
        verification = {
            "schema": "gfx-strategy-b-case-verification/v1",
            "case_id": digests["case_id"],
            "ok": ok,
            "problems": [] if ok else ["simulated problem"],
            "files": digests["files"] if files is None else files,
            "graph_status": "complete" if ok else "partial",
            "independently_reproducible": independent,
        }
        receipt = {
            "schema": "gfx-pilot-case-verification-receipt/v1",
            "case_manifest_sha256": digests["case_manifest_sha256"],
            "seal_sha256": digests["seal_sha256"],
            "payload_inventory_sha256": digests["payload_inventory_sha256"],
            "verification": verification,
        }
        self.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    def write_conclusion(self):
        self.refresh_binding()

    def write_run(self, tool_names=("eu4_case_inspect", "eu4_replay")):
        (self.run / "session.json").write_text(
            json.dumps(self.session), encoding="utf-8")
        (self.run / "efficiency.json").write_text(
            json.dumps(self.efficiency), encoding="utf-8")
        (self.run / "events.jsonl").write_text(
            "\n".join(json.dumps(item) for item in events(list(tool_names))),
            encoding="utf-8")

    def summarize(self, *, receipt=True, case=None):
        return dsh_pilot.summarize_run(
            self.run, self.conclusion_path,
            case if case is not None else self.case,
            case_receipt_path=self.receipt_path if receipt else None)

    def test_complete_run_summarizes_with_verified_case_receipt(self):
        self.write_run()
        result = self.summarize()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["case_verification"]["files"], 2)
        self.assertTrue(result["case_verification"]["independently_reproducible"])
        self.assertEqual(result["authoritative_evidence"]["checkpoints"], 2)
        self.assertEqual(result["efficiency"]["eu4_launches"], 0)
        self.assertEqual(result["session"]["native_tool_count"], 2)

    def test_bash_only_run_is_a_failure(self):
        self.write_run(tool_names=("bash", "read_image"))
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("no_native_tool", problem_codes(result))

    def test_mixed_run_passes(self):
        self.write_run(tool_names=("bash", "eu4_case_inspect"))
        result = self.summarize()
        self.assertTrue(result["ok"], result)

    def test_missing_event_records_fail(self):
        (self.run / "session.json").write_text(
            json.dumps(self.session), encoding="utf-8")
        (self.run / "efficiency.json").write_text(
            json.dumps(self.efficiency), encoding="utf-8")
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("missing_event_records", problem_codes(result))

    def test_launch_is_a_failure(self):
        self.efficiency["eu4_launches"]["count"] = 1
        self.write_run()
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("eu4_launch", problem_codes(result))

    def test_missing_launch_count_is_a_failure(self):
        del self.efficiency["eu4_launches"]
        self.write_run()
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("launch_count_invalid", problem_codes(result))

    def test_nonnumeric_launch_count_is_a_failure(self):
        self.efficiency["eu4_launches"]["count"] = "0"
        self.write_run()
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("launch_count_invalid", problem_codes(result))

    def test_missing_case_directory_fails(self):
        self.write_run()
        result = self.summarize(case=self.root / "absent")
        self.assertFalse(result["ok"])
        self.assertIn("missing_case", problem_codes(result))

    def test_self_asserted_verification_ok_without_evidence_fails(self):
        self.manifest["schema"] = "not-a-case"
        self.write_case()
        self.write_run()
        result = self.summarize(receipt=False)
        self.assertFalse(result["ok"])
        self.assertIn("case_verification_failed", problem_codes(result))
        self.assertIn("case_not_verified", problem_codes(result))

    def test_incomplete_case_payload_fails_verification(self):
        # The manifest is structurally valid but the required payload files
        # are absent; the conclusion's verification_ok flag must not rescue it.
        self.write_run()
        result = self.summarize(receipt=False)
        self.assertFalse(result["ok"])
        self.assertIn("case_not_verified", problem_codes(result))

    def test_receipt_digest_mismatch_fails(self):
        digests = dsh_pilot.case_receipt(self.case)
        receipt = {
            "schema": "gfx-pilot-case-verification-receipt/v1",
            "case_manifest_sha256": "0" * 64,
            "seal_sha256": digests["seal_sha256"],
            "payload_inventory_sha256": digests["payload_inventory_sha256"],
            "verification": {
                "case_id": digests["case_id"],
                "ok": True, "problems": [], "files": digests["files"],
                "graph_status": "complete",
                "independently_reproducible": True,
            },
        }
        self.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        self.write_run()
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("case_receipt_mismatch", problem_codes(result))

    def test_modified_payload_after_receipt_is_rejected(self):
        self.write_run()
        first = self.summarize()
        self.assertTrue(first["ok"], first)
        # Edit an inventoried payload without touching the manifest or receipt.
        report = json.loads((self.case / "diagnostic-report.json").read_text())
        report["frame"]["events"] = 1
        (self.case / "diagnostic-report.json").write_text(
            json.dumps(report), encoding="utf-8")
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("case_payload_mismatch", problem_codes(result))

    def test_deleted_payload_after_receipt_is_rejected(self):
        self.write_run()
        self.assertTrue(self.summarize()["ok"])
        (self.case / "payload.bin").unlink()
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("case_payload_missing", problem_codes(result))

    def test_fabricated_interval_via_edited_report_is_rejected(self):
        # Reproduce the reported attack: edit the inventoried diagnostic
        # report to add a matching event-71 checkpoint while leaving the
        # manifest, seal and receipt unchanged.
        self.write_run()
        self.assertTrue(self.summarize()["ok"])
        self.report["checkpoints"] = [
            {"event": 71, "resource": "fb:0", "target": "0",
             "status": "match", "authoritative": True},
            {"event": 142, "resource": "fb:0", "target": "0",
             "status": "divergent", "authoritative": True},
        ]
        (self.case / "diagnostic-report.json").write_text(
            json.dumps(self.report), encoding="utf-8")
        digests = dsh_pilot.case_receipt(self.case)
        value = correction_conclusion(interval())
        value["case"]["case_manifest_sha256"] = digests["case_manifest_sha256"]
        value["case"]["seal_sha256"] = digests["seal_sha256"]
        self.conclusion_path.write_text(json.dumps(value), encoding="utf-8")
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("case_payload_mismatch", problem_codes(result))

    def test_case_receipt_mismatch_is_a_failure(self):
        self.manifest["case_id"] = "another-case"
        self.refresh_binding()
        self.write_run()
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("case_receipt_mismatch", problem_codes(result))

    def test_capture_identity_mismatch_is_a_failure(self):
        self.seal["session_id"] = "another-launch"
        self.refresh_binding()
        self.write_run()
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("case_capture_mismatch", problem_codes(result))

    def test_case_digest_mismatch_in_conclusion_is_a_failure(self):
        self.write_run()
        value = conclusion()
        value["case"]["case_manifest_sha256"] = "0" * 64
        value["case"]["seal_sha256"] = "0" * 64
        self.conclusion_path.write_text(json.dumps(value), encoding="utf-8")
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("case_manifest_mismatch", problem_codes(result))
        self.assertIn("case_seal_mismatch", problem_codes(result))

    def test_fabricated_interval_is_rejected_by_acceptance(self):
        # Structural basis is internally valid, but the case report has no
        # event-71 match; the self-reported observation must not be promoted.
        self.report["checkpoints"] = [
            {"event": 142, "resource": "fb:0", "target": "0",
             "status": "divergent", "authoritative": True}]
        self.refresh_binding()
        digests = dsh_pilot.case_receipt(self.case)
        value = correction_conclusion(interval())
        value["case"]["case_manifest_sha256"] = digests["case_manifest_sha256"]
        value["case"]["seal_sha256"] = digests["seal_sha256"]
        self.conclusion_path.write_text(json.dumps(value), encoding="utf-8")
        self.write_run()
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("interval_unverified",
                      {item["code"]
                       for item in result["conclusion"]["validation"]["problems"]})

    def test_unverified_diagnostic_report_cannot_supply_intervals(self):
        self.report["identity"]["status"] = "unverified_session"
        self.refresh_binding()
        self.write_run()
        result = self.summarize()
        self.assertFalse(result["ok"])
        self.assertIn("case_diagnostic_unverified", problem_codes(result))

    @unittest.skipUnless(
        (Path(__file__).resolve().parents[1]
         / ".local/m7/p0-checkpoint-142/shadow/case").is_dir()
        and (Path(__file__).resolve().parents[1]
             / ".local/m7/p1/run/events.jsonl").is_file(),
        "authentic P1 case/run evidence is local-only")
    def test_authentic_p1_no_fix_conclusion_remains_valid(self):
        root = Path(__file__).resolve().parents[1]
        result = dsh_pilot.summarize_run(
            root / ".local/m7/p1/run",
            root / "analysis/m7/p1-autonomous-pilot-conclusion.json",
            root / ".local/m7/p0-checkpoint-142/shadow/case")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["conclusion"]["outcome"],
                         "requires_additional_evidence")
        self.assertFalse(result["conclusion"]["has_correction"])
        self.assertFalse(result["conclusion"]["validation"]["checks"]["has_interval"])
        self.assertEqual(result["session"]["native_tool_calls"],
                         ["eu4_case_inspect", "eu4_replay"])


if __name__ == "__main__":
    unittest.main()
