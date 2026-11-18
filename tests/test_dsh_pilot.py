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


class PilotConclusionTests(unittest.TestCase):
    def test_minimal_evidence_request_is_valid(self):
        result = dsh_pilot.validate_conclusion(conclusion())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["checks"]["outcome"],
                         "requires_additional_evidence")

    def test_cross_capture_interval_is_rejected(self):
        value = conclusion()
        value["causal_interval"] = {
            "start_event": 71,
            "end_event": 142,
            "same_capture": True,
            "basis": [
                {"event": 71, "resource": "fb:0", "status": "match",
                 "capture": dict(FRAME, session_id="another-launch")},
                {"event": 142, "resource": "fb:0", "status": "divergent",
                 "capture": dict(FRAME)},
            ],
        }
        result = dsh_pilot.validate_conclusion(value)
        self.assertFalse(result["ok"])
        self.assertTrue(any(item["code"] == "cross_capture_interval"
                            for item in result["problems"]), result)

    def test_interval_basis_must_match_the_conclusion_frame(self):
        value = conclusion()
        other = dict(FRAME, capture_frame_id=2)
        value["causal_interval"] = {
            "start_event": 71, "end_event": 142, "same_capture": True,
            "basis": [{"event": 71, "resource": "fb:0", "status": "match",
                       "capture": dict(other)}],
        }
        result = dsh_pilot.validate_conclusion(value)
        self.assertFalse(result["ok"])
        self.assertTrue(any(item["code"] == "cross_capture_interval"
                            for item in result["problems"]), result)

    def test_unresolved_pilot_cannot_claim_a_lower_bound(self):
        value = conclusion()
        value["causal_interval"] = {
            "start_event": 71, "end_event": 142, "same_capture": True,
            "lower_bound": "same_resource_match",
            "basis": [{"event": 71, "resource": "fb:0", "status": "match",
                       "capture": dict(FRAME)}],
        }
        result = dsh_pilot.validate_conclusion(value)
        self.assertFalse(result["ok"])
        self.assertTrue(any(item["code"] == "unresolved_interval_claimed"
                            for item in result["problems"]), result)

    def test_new_capture_requires_a_cross_frame_caveat(self):
        value = conclusion()
        del value["evidence_request"]["cross_frame_caveat"]
        result = dsh_pilot.validate_conclusion(value)
        self.assertFalse(result["ok"])
        self.assertTrue(any(item["code"] == "evidence_request_cross_frame"
                            for item in result["problems"]), result)

    def test_hypothesis_budget_is_bounded(self):
        value = conclusion()
        value["hypotheses"] = [dict(value["hypotheses"][0])
                               for _ in range(4)]
        result = dsh_pilot.validate_conclusion(value)
        self.assertFalse(result["ok"])
        self.assertTrue(any(item["code"] == "hypothesis_budget"
                            for item in result["problems"]), result)

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
        self.assertTrue(any(item["code"] == "correction_not_improving"
                            for item in result["problems"]), result)

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
        self.assertTrue(any(item["code"] == "correction_files"
                            for item in result["problems"]), result)

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


class PilotSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run = self.root / "run"
        self.run.mkdir()
        self.conclusion_path = self.root / "conclusion.json"
        self.conclusion_path.write_text(json.dumps(conclusion()),
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

    def write(self):
        (self.run / "session.json").write_text(
            json.dumps(self.session), encoding="utf-8")
        (self.run / "efficiency.json").write_text(
            json.dumps(self.efficiency), encoding="utf-8")

    def test_complete_run_summarizes(self):
        self.write()
        result = dsh_pilot.summarize_run(self.run, self.conclusion_path)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["efficiency"]["eu4_launches"], 0)
        self.assertEqual(result["session"]["tool_count"], 2)

    def test_launch_is_a_failure(self):
        self.efficiency["eu4_launches"]["count"] = 1
        self.write()
        result = dsh_pilot.summarize_run(self.run, self.conclusion_path)
        self.assertFalse(result["ok"])
        self.assertTrue(any(item["code"] == "eu4_launch"
                            for item in result["problems"]), result)

    def test_no_native_tool_is_a_failure(self):
        self.session["tool_calls"] = []
        self.write()
        result = dsh_pilot.summarize_run(self.run, self.conclusion_path)
        self.assertFalse(result["ok"])
        self.assertTrue(any(item["code"] == "no_native_tool"
                            for item in result["problems"]), result)


if __name__ == "__main__":
    unittest.main()
