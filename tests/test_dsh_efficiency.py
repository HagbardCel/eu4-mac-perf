"""Phase 6: bounded efficiency analysis of an existing DSH JSONL export."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import dsh_efficiency  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "dsh" / "efficiency-session.jsonl"


class EfficiencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = dsh_efficiency.read_events(FIXTURE)
        cls.value = dsh_efficiency.analyze(
            dsh_efficiency.filter_turn(cls.events, 1),
            oversized_chars=100, long_job_seconds=5.0, top=5)

    def test_calls_by_tool_and_failure_rate(self):
        calls = self.value["calls"]
        self.assertEqual(calls["total"], 5)
        self.assertEqual(calls["by_tool"]["bash"], 2)
        self.assertEqual(calls["by_tool"]["eu4_diff_run"], 1)
        self.assertEqual(calls["failed"], 1)
        self.assertEqual(calls["failure_rate"], 0.2)
        self.assertEqual(calls["failed_by_tool"], {"bash": 1})

    def test_duplicate_test_invocations_collapse_to_one_signature(self):
        tests = self.value["tests"]
        self.assertEqual(tests["test_invocations"], 2)
        self.assertEqual(tests["full_suite_invocations"], 2)
        self.assertEqual(tests["distinct_commands"], 1)
        self.assertEqual(tests["duplicates"][0]["count"], 2)
        self.assertEqual(tests["duplicates"][0]["signature"],
                         "unittest discover -s tests")

    def test_oversized_and_truncated_results_are_separated(self):
        output = self.value["output"]
        self.assertEqual(output["oversized_count"], 2)
        self.assertEqual(output["truncated_count"], 1)
        self.assertEqual(output["truncated"][0]["tool"], "eu4_session")
        self.assertTrue(all(row["chars"] > 100 for row in output["oversized"]))

    def test_long_jobs_and_wait_are_measured(self):
        self.assertEqual(self.value["long_jobs"]["count"], 1)
        job = self.value["long_jobs"]["jobs"][0]
        self.assertEqual(job["status"], "killed")
        self.assertAlmostEqual(job["seconds"], 8.5)
        wait = self.value["wait"]
        self.assertAlmostEqual(wait["tool_wait_seconds"], 10.565, places=3)
        self.assertAlmostEqual(wait["by_tool"]["eu4_diff_run"], 8.55, places=3)
        self.assertGreater(wait["share_of_elapsed"], 0.8)

    def test_tokens_distinguish_cache_reads(self):
        tokens = self.value["tokens"]
        self.assertEqual(tokens["input"], 1000)
        self.assertEqual(tokens["cache_read"], 4000)
        self.assertEqual(tokens["output"], 200)
        self.assertEqual(tokens["total"], 5200)
        self.assertEqual(tokens["effective_input"], 5000)
        self.assertAlmostEqual(tokens["cache_read_share_of_total"], 0.7692)

    def test_eu4_launch_count_is_separate_from_command_evidence(self):
        launches = self.value["eu4_launches"]
        self.assertEqual(launches["count"], 1)
        self.assertEqual(launches["process_starts_estimate"], 2)
        self.assertEqual(launches["command_evidence"][0]["kind"], "tool")

    def test_human_interventions_and_goal_outcome(self):
        human = self.value["human"]
        self.assertEqual(human["user_messages"], 1)
        self.assertEqual(human["approval_interventions"], 1)
        self.assertEqual(human["approval_policy_events"], 1)
        self.assertEqual(human["permission_commands"], 1)
        self.assertEqual(self.value["goal"]["outcome"], "completed")
        self.assertEqual(self.value["goal"]["operations"], ["create", "complete"])

    def test_turn_filter_is_a_real_window(self):
        other = dsh_efficiency.filter_turn(self.events, 2)
        value = dsh_efficiency.analyze(other)
        self.assertEqual(value["calls"]["total"], 0)
        self.assertEqual(value["session"]["id"], "session-fixture")

    def test_machine_paths_are_sanitized(self):
        events = [{
            "type": "session", "version": 4, "id": "s",
            "cwd": "/Users/someone/private/checkout",
        }, {
            "type": "job/settled", "time": 10,
            "data": {"jobId": "j", "label": "/Users/someone/private/tests.log",
                     "startedAt": 0, "finishedAt": 21000, "status": "completed"},
        }]
        value = dsh_efficiency.analyze(events)
        self.assertNotIn("someone", json.dumps(value))
        self.assertIn("<home>", value["session"]["cwd"])
        self.assertIn("<home>", value["long_jobs"]["jobs"][0]["label"])

    def test_shell_failures_and_bash_launches_are_separated(self):
        events = [
            {"type": "tool/call", "time": 1000, "data": {
                "callId": "b1", "name": "bash",
                "arguments": {"command":
                              "python3 -m automation.m6_frame capture "
                              "--output /tmp/cap"}}},
            {"type": "tool/result", "time": 2000, "data": {
                "toolCallId": "b1",
                "message": {"isError": False, "content": [
                    {"type": "text", "text": "capture_exit=1"}]}}},
            {"type": "tool/call", "time": 3000, "data": {
                "callId": "b2", "name": "bash",
                "arguments": {"command": "true"}}},
            {"type": "tool/result", "time": 4000, "data": {
                "toolCallId": "b2",
                "message": {"isError": False, "content": [
                    {"type": "text", "text": "exit=0"}]}}},
        ]
        value = dsh_efficiency.analyze(events)
        self.assertEqual(value["calls"]["failed"], 0)
        self.assertEqual(value["calls"]["shell_commands"], 2)
        self.assertEqual(value["calls"]["shell_failures"], 1)
        self.assertEqual(
            value["calls"]["shell_failure_examples"][0]["exit_code"], 1)
        self.assertEqual(value["eu4_launches"]["count"], 1)
        self.assertEqual(value["eu4_launches"]["bash_launches"], 1)
        self.assertEqual(
            value["eu4_launches"]["command_evidence"][0]["kind"], "bash")

    def test_zstd_export_is_read_transparently(self):
        if not shutil.which("zstd"):
            self.skipTest("zstd is not available")
        with tempfile.TemporaryDirectory() as temporary:
            compressed = Path(temporary) / "session.jsonl.zstd"
            subprocess.run(["zstd", "-q", "-f", str(FIXTURE), "-o",
                            str(compressed)], check=True)
            events = dsh_efficiency.read_events(compressed)
        self.assertEqual(len(events), len(self.events))

    def test_cli_emits_one_summary(self):
        completed = subprocess.run(
            [sys.executable, "-m", "automation.dsh_efficiency", "analyze",
             str(FIXTURE), "--turn", "1", "--oversized-chars", "100"],
            cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        value = json.loads(completed.stdout)
        self.assertEqual(value["schema"], dsh_efficiency.SCHEMA)
        self.assertEqual(value["calls"]["total"], 5)


if __name__ == "__main__":
    unittest.main()
