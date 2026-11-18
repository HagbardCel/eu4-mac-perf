"""Fail-closed regressions for the M7-P1 pilot conclusion contract."""

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
        result = dsh_pilot.validate_conclusion(correction_conclusion(interval()))
        self.assertTrue(result["ok"], result)

    def test_texture_match_cannot_bound_a_framebuffer_divergence(self):
        value = correction_conclusion(interval())
        value["causal_interval"]["basis"][0]["resource"] = "tex:431"
        value["causal_interval"]["basis"][0]["target"] = "431"
        result = dsh_pilot.validate_conclusion(value)
        self.assertFalse(result["ok"])
        self.assertIn("mixed_resource_interval", problem_codes(result))
        self.assertIn("interval_resource_mismatch", problem_codes(result))

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
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run = self.root / "run"
        self.run.mkdir()
        self.conclusion_path = self.root / "conclusion.json"
        self.conclusion_path.write_text(json.dumps(conclusion()),
                                        encoding="utf-8")
        self.case = self.root / "case"
        (self.case / "capture").mkdir(parents=True)
        (self.case / "case.json").write_text(
            json.dumps({"case_id": "m7-live-frame-000001"}), encoding="utf-8")
        (self.case / "capture" / "frame-seal.json").write_text(
            json.dumps({"session_id": FRAME["session_id"],
                        "capture_frame_id": FRAME["capture_frame_id"],
                        "event_stream_sha256": FRAME["event_stream_sha256"],
                        "present_index": 617, "scene_index": 617}),
            encoding="utf-8")
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

    def write(self, tool_names=("eu4_case_inspect", "eu4_replay")):
        (self.run / "session.json").write_text(
            json.dumps(self.session), encoding="utf-8")
        (self.run / "efficiency.json").write_text(
            json.dumps(self.efficiency), encoding="utf-8")
        (self.run / "events.jsonl").write_text(
            "\n".join(json.dumps(item) for item in events(list(tool_names))),
            encoding="utf-8")

    def test_complete_run_summarizes_with_case_receipt(self):
        self.write()
        result = dsh_pilot.summarize_run(
            self.run, self.conclusion_path, case_dir=self.case)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["efficiency"]["eu4_launches"], 0)
        self.assertEqual(result["session"]["native_tool_count"], 2)
        self.assertEqual(result["case_receipt"]["case_id"],
                         "m7-live-frame-000001")

    def test_bash_only_run_is_a_failure(self):
        self.write(tool_names=("bash", "read_image"))
        result = dsh_pilot.summarize_run(
            self.run, self.conclusion_path, case_dir=self.case)
        self.assertFalse(result["ok"])
        self.assertIn("no_native_tool", problem_codes(result))

    def test_mixed_run_passes(self):
        self.write(tool_names=("bash", "eu4_case_inspect"))
        result = dsh_pilot.summarize_run(
            self.run, self.conclusion_path, case_dir=self.case)
        self.assertTrue(result["ok"], result)

    def test_missing_event_records_fail(self):
        (self.run / "session.json").write_text(
            json.dumps(self.session), encoding="utf-8")
        (self.run / "efficiency.json").write_text(
            json.dumps(self.efficiency), encoding="utf-8")
        result = dsh_pilot.summarize_run(
            self.run, self.conclusion_path, case_dir=self.case)
        self.assertFalse(result["ok"])
        self.assertIn("missing_event_records", problem_codes(result))

    def test_launch_is_a_failure(self):
        self.efficiency["eu4_launches"]["count"] = 1
        self.write()
        result = dsh_pilot.summarize_run(
            self.run, self.conclusion_path, case_dir=self.case)
        self.assertFalse(result["ok"])
        self.assertIn("eu4_launch", problem_codes(result))

    def test_missing_launch_count_is_a_failure(self):
        del self.efficiency["eu4_launches"]
        self.write()
        result = dsh_pilot.summarize_run(
            self.run, self.conclusion_path, case_dir=self.case)
        self.assertFalse(result["ok"])
        self.assertIn("launch_count_invalid", problem_codes(result))

    def test_nonnumeric_launch_count_is_a_failure(self):
        self.efficiency["eu4_launches"]["count"] = "0"
        self.write()
        result = dsh_pilot.summarize_run(
            self.run, self.conclusion_path, case_dir=self.case)
        self.assertFalse(result["ok"])
        self.assertIn("launch_count_invalid", problem_codes(result))

    def test_case_receipt_mismatch_is_a_failure(self):
        (self.case / "case.json").write_text(
            json.dumps({"case_id": "another-case"}), encoding="utf-8")
        self.write()
        result = dsh_pilot.summarize_run(
            self.run, self.conclusion_path, case_dir=self.case)
        self.assertFalse(result["ok"])
        self.assertIn("case_receipt_mismatch", problem_codes(result))

    def test_capture_identity_mismatch_is_a_failure(self):
        (self.case / "capture" / "frame-seal.json").write_text(
            json.dumps({"session_id": "another-launch",
                        "capture_frame_id": 1,
                        "event_stream_sha256": FRAME["event_stream_sha256"]}),
            encoding="utf-8")
        self.write()
        result = dsh_pilot.summarize_run(
            self.run, self.conclusion_path, case_dir=self.case)
        self.assertFalse(result["ok"])
        self.assertIn("case_capture_mismatch", problem_codes(result))


if __name__ == "__main__":
    unittest.main()
