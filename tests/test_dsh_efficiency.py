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

    def test_execution_summary_separates_native_and_shell_calls(self):
        summary = self.value["summary"]
        self.assertEqual(summary["schema"], "dsh-execution-summary/v1")
        self.assertEqual(summary["tool_calls"]["total"], 5)
        self.assertEqual(summary["tool_calls"]["native"], 2)
        self.assertEqual(summary["tool_calls"]["shell"], 2)
        self.assertEqual(summary["eu4_launches"], 1)
        self.assertEqual(summary["tests"]["executions"], 2)
        self.assertEqual(summary["tests"]["repetitions"], 1)
        self.assertEqual(summary["diagnostics"]["returned_chars"],
                         self.value["output"]["total_chars"])
        self.assertEqual(summary["execution"]["wall_seconds"],
                         self.value["window"]["elapsed_seconds"])

    def test_execution_summary_does_not_invent_unmeasured_values(self):
        summary = self.value["summary"]
        self.assertIsNone(summary["replays"]["gl"])
        self.assertIsNone(summary["replays"]["metal"])
        self.assertIsNone(summary["gpu_work"]["checkpoint_readbacks"])
        self.assertIsNone(summary["diagnostics"]["artifact_bytes"])
        self.assertIn("not in the export", summary["diagnostics"]["note"])

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

    def test_documentation_search_is_not_counted_as_a_launch(self):
        events = [
            {"type": "tool/call", "data": {
                "callId": "search", "name": "bash",
                "arguments": {"command":
                              "rg 'python3 -m automation.m6_frame capture' "
                              "analysis/m7/post206/reproduce.md"}}},
            {"type": "tool/result", "data": {
                "toolCallId": "search",
                "message": {"isError": False, "content": [
                    {"type": "text", "text": "match"}]}}},
            {"type": "tool/call", "data": {
                "callId": "launch", "name": "bash",
                "arguments": {"command":
                              "cd /tmp && python3 -m automation.m6_frame "
                              "capture --output /tmp/x"}}},
            {"type": "tool/result", "data": {
                "toolCallId": "launch",
                "message": {"isError": False, "content": [
                    {"type": "text", "text": "capture_exit=1"}]}}},
        ]
        value = dsh_efficiency.analyze(events)
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


class GoalPhaseTests(unittest.TestCase):
    def test_blocked_goal_is_not_reported_as_completed(self):
        events = [
            {"type": "session", "version": 4, "id": "s", "time": 1000,
             "createdAt": 1000},
            {"type": "turn/start", "time": 2000, "data": {"turn": 1}},
            {"type": "goal/change", "time": 2000, "data": {
                "operation": "create",
                "goal": {"id": "g", "phase": "active", "revision": 1}}},
            {"type": "goal/change", "time": 2679000, "data": {
                "operation": "block",
                "goal": {"id": "g", "phase": "blocked", "revision": 2}}},
            {"type": "turn/end", "time": 3132000, "data": {
                "turn": 1, "reason": {"kind": "completed"}}},
        ]
        value = dsh_efficiency.analyze(events)
        goal = value["goal"]
        self.assertEqual(goal["outcome"], "blocked")
        self.assertEqual(goal["last_phase"], "blocked")
        self.assertEqual(goal["last_turn_reason"], "completed")
        self.assertAlmostEqual(goal["duration_seconds"], 2677.0, places=3)
        summary = value["summary"]
        self.assertEqual(summary["outcome"], "blocked")
        self.assertEqual(summary["goal_outcome"], "blocked")
        self.assertEqual(summary["turn_outcome"], "completed")
        self.assertAlmostEqual(summary["execution"]["goal_seconds"], 2677.0,
                               places=3)
        self.assertAlmostEqual(summary["execution"]["wall_seconds"], 3131.0,
                               places=3)

    def test_sequential_goals_use_the_selected_goals_own_lifetime(self):
        events = [
            {"type": "goal/change", "time": 1000, "data": {
                "operation": "create",
                "goal": {"id": "g1", "phase": "active", "revision": 1}}},
            {"type": "goal/change", "time": 2000, "data": {
                "operation": "complete",
                "goal": {"id": "g1", "phase": "complete", "revision": 2}}},
            {"type": "goal/change", "time": 101000, "data": {
                "operation": "create",
                "goal": {"id": "g2", "phase": "active", "revision": 1}}},
            {"type": "goal/change", "time": 111000, "data": {
                "operation": "block",
                "goal": {"id": "g2", "phase": "blocked", "revision": 2}}},
            {"type": "turn/end", "time": 120000, "data": {
                "turn": 1, "reason": {"kind": "completed"}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["selected_goal_id"], "g2")
        self.assertEqual(goal["goal_ids"], ["g1", "g2"])
        self.assertEqual(goal["goal_count"], 2)
        self.assertEqual(goal["outcome"], "blocked")
        self.assertAlmostEqual(goal["duration_seconds"], 10.0, places=3)
        self.assertEqual(goal["duration_kind"], "terminal")
        self.assertTrue(goal["started_utc"].startswith("1970-01-01T00:01:41"))
        self.assertTrue(goal["ended_utc"].startswith("1970-01-01T00:01:51"))

    def test_active_goal_has_no_end_and_reports_observed_duration(self):
        events = [
            {"type": "goal/change", "time": 1000, "data": {
                "operation": "create",
                "goal": {"id": "g", "phase": "active", "revision": 1}}},
            {"type": "turn/end", "time": 61000, "data": {
                "turn": 1, "reason": {"kind": "completed"}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["outcome"], "active")
        self.assertIsNone(goal["ended_utc"])
        self.assertEqual(goal["duration_kind"], "ongoing")
        self.assertAlmostEqual(goal["duration_seconds"], 60.0, places=3)
        self.assertTrue(goal["observed_until_utc"].startswith(
            "1970-01-01T00:01:01"))
        summary = dsh_efficiency.analyze(events)["summary"]
        self.assertEqual(summary["execution"]["goal_duration_kind"], "ongoing")
        self.assertAlmostEqual(summary["execution"]["goal_seconds"], 60.0,
                               places=3)

    def test_multiple_phase_revisions_keep_one_goal_lifetime(self):
        events = [
            {"type": "goal/change", "time": 1000, "data": {
                "operation": "create",
                "goal": {"id": "g", "phase": "active", "revision": 1}}},
            {"type": "goal/change", "time": 5000, "data": {
                "operation": "edit",
                "goal": {"id": "g", "phase": "active", "revision": 2}}},
            {"type": "goal/change", "time": 11000, "data": {
                "operation": "complete",
                "goal": {"id": "g", "phase": "complete", "revision": 3}}},
            {"type": "turn/end", "time": 12000, "data": {
                "turn": 1, "reason": {"kind": "completed"}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["goal_count"], 1)
        self.assertEqual(goal["outcome"], "completed")
        self.assertAlmostEqual(goal["duration_seconds"], 10.0, places=3)
        self.assertFalse(goal["duration_invalid"])

    def test_resumed_active_goal_is_ongoing_not_terminated(self):
        events = [
            {"type": "goal/change", "time": 1000, "data": {
                "operation": "create",
                "goal": {"id": "g", "phase": "active", "revision": 1}}},
            {"type": "goal/change", "time": 2000, "data": {
                "operation": "block",
                "goal": {"id": "g", "phase": "blocked", "revision": 2}}},
            {"type": "goal/change", "time": 3000, "data": {
                "operation": "resume",
                "goal": {"id": "g", "phase": "active", "revision": 3}}},
            {"type": "turn/end", "time": 10000, "data": {
                "turn": 1, "reason": {"kind": "completed"}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["outcome"], "active")
        self.assertIsNone(goal["ended_utc"])
        self.assertEqual(goal["duration_kind"], "ongoing")
        self.assertAlmostEqual(goal["duration_seconds"], 9.0, places=3)

    def test_clear_tombstone_ends_the_selected_goal(self):
        events = [
            {"type": "goal/change", "time": 1000, "data": {
                "operation": "create",
                "goal": {"id": "g", "phase": "active", "revision": 1}}},
            {"type": "goal/change", "time": 2000, "data": {
                "operation": "clear",
                "cleared": {"id": "g", "revision": 1},
                "clearedAt": "2026-01-01T00:00:02Z"}},
            {"type": "turn/end", "time": 3000, "data": {
                "turn": 1, "reason": {"kind": "completed"}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["selected_goal_id"], "g")
        self.assertEqual(goal["outcome"], "cleared")
        self.assertFalse(goal["active"])
        self.assertTrue(goal["terminal"])
        self.assertFalse(goal["identity_missing"])
        self.assertEqual(goal["duration_kind"], "terminal")
        self.assertAlmostEqual(goal["duration_seconds"], 1.0, places=3)
        self.assertIsNotNone(goal["ended_utc"])

    def test_window_without_the_create_reports_unknown_start(self):
        events = [
            {"type": "goal/change", "time": 2000, "data": {
                "operation": "block",
                "goal": {"id": "g", "phase": "blocked", "revision": 2}}},
            {"type": "turn/end", "time": 3000, "data": {
                "turn": 2, "reason": {"kind": "completed"}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["outcome"], "blocked")
        self.assertIsNone(goal["duration_seconds"])
        self.assertEqual(goal["duration_kind"], "unknown_start")
        self.assertIn("no create event", " ".join(goal["limitations"]))

    def test_action_attempt_is_not_launch_only(self):
        events = [
            {"type": "tool/call", "time": 1000, "data": {
                "callId": "r1", "name": "eu4_replay",
                "arguments": {"operation": "verify"}}},
            {"type": "tool/result", "time": 2000, "data": {
                "toolCallId": "r1",
                "message": {"isError": False, "content": [
                    {"type": "text", "text": "ok"}]}}},
        ]
        summary = dsh_efficiency.analyze(events)["summary"]
        self.assertFalse(summary["launch_attempted"])
        self.assertEqual(summary["actions_attempted"], ["replay"])
        self.assertEqual(summary["action_attempted"], "replay")

    def test_clear_after_complete_keeps_the_completion_outcome(self):
        events = [
            {"type": "goal/change", "time": 1000, "data": {
                "operation": "create",
                "goal": {"id": "g", "phase": "active", "revision": 1}}},
            {"type": "goal/change", "time": 2000, "data": {
                "operation": "complete",
                "goal": {"id": "g", "phase": "complete", "revision": 2}}},
            {"type": "goal/change", "time": 60000, "data": {
                "operation": "clear",
                "cleared": {"id": "g", "revision": 2}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["outcome"], "completed")
        self.assertEqual(goal["substantive_phase"], "complete")
        self.assertTrue(goal["cleared"])
        self.assertTrue(goal["terminal"])
        self.assertAlmostEqual(goal["duration_seconds"], 1.0, places=3)
        self.assertTrue(goal["ended_utc"].startswith(
            "1970-01-01T00:00:02"))
        self.assertTrue(goal["cleared_utc"].startswith(
            "1970-01-01T00:01:00"))

    def test_clear_after_blocked_keeps_the_blocked_outcome(self):
        events = [
            {"type": "goal/change", "time": 1000, "data": {
                "operation": "create",
                "goal": {"id": "g", "phase": "active", "revision": 1}}},
            {"type": "goal/change", "time": 2000, "data": {
                "operation": "block",
                "goal": {"id": "g", "phase": "blocked", "revision": 2}}},
            {"type": "goal/change", "time": 60000, "data": {
                "operation": "clear",
                "cleared": {"id": "g", "revision": 2}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["outcome"], "blocked")
        self.assertTrue(goal["cleared"])
        self.assertAlmostEqual(goal["duration_seconds"], 1.0, places=3)

    def test_edit_only_terminal_window_reports_unknown_end(self):
        events = [
            {"type": "goal/change", "time": 7000, "data": {
                "operation": "edit",
                "goal": {"id": "g", "phase": "complete", "revision": 3}}},
            {"type": "turn/end", "time": 10000, "data": {
                "turn": 2, "reason": {"kind": "completed"}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["outcome"], "completed")
        self.assertEqual(goal["duration_kind"], "unknown_end")
        self.assertIsNone(goal["ended_utc"])
        self.assertIn("terminal transition is outside this window",
                      " ".join(goal["limitations"]))

    def test_edit_then_clear_terminal_window_reports_unknown_end(self):
        events = [
            {"type": "goal/change", "time": 7000, "data": {
                "operation": "edit",
                "goal": {"id": "g", "phase": "blocked", "revision": 3}}},
            {"type": "goal/change", "time": 9000, "data": {
                "operation": "clear",
                "cleared": {"id": "g", "revision": 3}}},
            {"type": "turn/end", "time": 10000, "data": {
                "turn": 2, "reason": {"kind": "completed"}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["outcome"], "blocked")
        self.assertTrue(goal["cleared"])
        self.assertTrue(goal["unknown_end"])
        self.assertEqual(goal["duration_kind"], "unknown_end")
        self.assertIsNone(goal["ended_utc"])
        self.assertIsNotNone(goal["cleared_utc"])

    def test_clear_tombstone_without_terminal_transition_is_not_completion(self):
        events = [
            {"type": "goal/change", "time": 1000, "data": {
                "operation": "create",
                "goal": {"id": "g", "phase": "active", "revision": 1}}},
            {"type": "goal/change", "time": 5000, "data": {
                "operation": "clear",
                "cleared": {"id": "g", "revision": 1}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["outcome"], "cleared")
        self.assertNotEqual(goal["outcome"], "completed")
        self.assertTrue(goal["cleared"])
        self.assertTrue(goal["terminal"])
        self.assertEqual(goal["duration_kind"], "terminal")
        self.assertAlmostEqual(goal["duration_seconds"], 4.0, places=3)
        self.assertTrue(goal["ended_utc"].startswith(
            "1970-01-01T00:00:05"))

    def test_capture_help_is_not_a_physical_launch(self):
        events = [
            {"type": "tool/call", "time": 1000, "data": {
                "callId": "help", "name": "bash",
                "arguments": {"command":
                              "python3 -m automation.m6_frame capture --help"}}},
            {"type": "tool/result", "time": 1100, "data": {
                "toolCallId": "help", "message": {"isError": False,
                                                  "content": []}}},
        ]
        value = dsh_efficiency.analyze(events)
        self.assertEqual(value["eu4_launches"]["count"], 0)

    def test_runner_invocations_are_physical_work(self):
        events = [
            {"type": "tool/call", "time": 1000, "data": {
                "callId": "r1", "name": "bash",
                "arguments": {"command":
                              "python3.14 -m automation.test_runner run "
                              "--profile offline --module tests.test_agent_tools"}}},
            {"type": "tool/result", "time": 2000, "data": {
                "toolCallId": "r1", "message": {"isError": False,
                                                "content": []}}},
        ]
        value = dsh_efficiency.analyze(events)
        self.assertEqual(value["physical_work"]["runner_invocations"], 1)
        self.assertEqual(value["physical_work"]["runner_focused_invocations"], 1)
        self.assertEqual(value["physical_work"]["game_launch_commands"], 0)
        self.assertEqual(value["physical_work"]["confirmed_game_launches"], 0)
        self.assertIsNone(value["service"]["gpu_service_seconds"])
        self.assertIsNone(value["physical_work"]["gpu_service_seconds"])

    def test_duplicate_identical_runner_invocation_counts_once(self):
        command = ("python3.14 -m automation.test_runner run --profile offline "
                   "--module tests.test_agent_tools")
        events = []
        for index in range(2):
            call_id = f"r{index}"
            events.append({"type": "tool/call", "time": 1000 + index,
                           "data": {"callId": call_id, "name": "bash",
                                    "arguments": {"command": command}}})
            events.append({"type": "tool/result", "time": 2000 + index,
                           "data": {"toolCallId": call_id,
                                    "message": {"isError": False, "content": [
                                        {"type": "text",
                                         "text": "Ran 5 tests in 0.1s\n"}]}}})
        value = dsh_efficiency.analyze(events)
        self.assertEqual(value["physical_work"]["runner_invocations"], 1)
        self.assertEqual(
            value["physical_work"]["runner_repeated_invocations"], 1)
        self.assertEqual(value["tests"]["test_invocations"], 1)
        self.assertEqual(value["tests"]["runner_tests_executed"], 5)

    def test_two_runner_commands_in_one_call_are_not_double_counted(self):
        command = (
            "python3.14 -m automation.test_runner run --profile offline "
            "--module tests.test_agent_tools; "
            "python3.14 -m automation.test_runner run --profile offline "
            "--module tests.test_gfx_case")
        events = [
            {"type": "tool/call", "time": 1000, "data": {
                "callId": "r1", "name": "bash",
                "arguments": {"command": command}}},
            {"type": "tool/result", "time": 2000, "data": {
                "toolCallId": "r1", "message": {"isError": False, "content": [
                    {"type": "text",
                     "text": "Ran 5 tests in 0.1s\nRan 7 tests in 0.2s\n"}]}}},
        ]
        value = dsh_efficiency.analyze(events)
        self.assertEqual(value["physical_work"]["runner_invocations"], 2)
        self.assertEqual(value["tests"]["runner_tests_executed"], 12)
        # Two JSON summaries cannot be attributed unambiguously without one
        # Ran-line each, so the total is explicitly unknown.
        events[1]["data"]["message"]["content"][0]["text"] = (
            '{"counts": {"executed": 4}}\n'
            '{"counts": {"executed": 6}}\n')
        value = dsh_efficiency.analyze(events)
        self.assertIsNone(value["tests"]["runner_tests_executed"])

    def test_runner_help_discovery_and_status_are_not_focused_work(self):
        events = []
        commands = [
            "python3.14 -m automation.test_runner run --help",
            "python3.14 -m automation.test_runner status --id abc",
            "python3.14 -m automation.test_runner run --profile offline",
        ]
        for index, command in enumerate(commands):
            call_id = f"c{index}"
            events.append({"type": "tool/call", "time": 1000 + index,
                           "data": {"callId": call_id, "name": "bash",
                                    "arguments": {"command": command}}})
            events.append({"type": "tool/result", "time": 2000 + index,
                           "data": {"toolCallId": call_id,
                                    "message": {"isError": False,
                                                "content": []}}})
        value = dsh_efficiency.analyze(events)
        self.assertEqual(value["physical_work"]["runner_invocations"], 0)
        self.assertEqual(value["physical_work"]["runner_full_invocations"], 1)
        self.assertEqual(value["physical_work"]["game_launch_commands"], 0)

    def test_gpu_service_time_is_explicitly_unknown(self):
        events = [
            {"type": "tool/call", "time": 1000, "data": {
                "callId": "r1", "name": "bash",
                "arguments": {"command":
                              "python3.14 -m automation.test_runner run "
                              "--module tests.test_dsh_efficiency"}}},
            {"type": "job/registered", "time": 1001, "data": {
                "jobId": "j1", "startedAt": 1001000, "label": "gate"}},
            {"type": "job/settled", "time": 9000, "data": {
                "jobId": "j1", "startedAt": 1001000, "finishedAt": 9001000,
                "status": "completed"}},
            {"type": "tool/result", "time": 9500, "data": {
                "toolCallId": "r1", "message": {"isError": False,
                                                "content": []}}},
        ]
        value = dsh_efficiency.analyze(events)
        service = value["service"]
        self.assertIsNone(service["gpu_service_seconds"])
        self.assertIn("gpu_service_seconds", service["unknown"])
        self.assertIsNone(value["summary"]["gpu_work"][
            "gpu_service_seconds"])
        self.assertAlmostEqual(service["job_service_union_seconds"], 8000.0,
                               places=3)
        self.assertAlmostEqual(service["client_wall_seconds"], 8.5, places=3)
        # Service time is never promoted to a GPU measurement.
        self.assertNotEqual(service["gpu_service_seconds"],
                            service["client_wall_seconds"])

    def test_overlapping_tool_waits_are_not_double_counted(self):
        events = [
            {"type": "tool/call", "time": 1000, "data": {
                "callId": "a", "name": "bash", "arguments": {"command": "true"}}},
            {"type": "tool/call", "time": 1000, "data": {
                "callId": "b", "name": "bash", "arguments": {"command": "true"}}},
            {"type": "tool/result", "time": 4000, "data": {
                "toolCallId": "a", "message": {"isError": False,
                                               "content": []}}},
            {"type": "tool/result", "time": 5000, "data": {
                "toolCallId": "b", "message": {"isError": False,
                                               "content": []}}},
        ]
        service = dsh_efficiency.analyze(events)["service"]
        self.assertAlmostEqual(service["tool_wait_sum_seconds"], 7.0, places=3)
        self.assertAlmostEqual(service["tool_wait_union_seconds"], 4.0,
                               places=3)
        self.assertAlmostEqual(service["overlapping_wait_seconds"], 3.0,
                               places=3)

    def test_paused_goal_has_observed_not_terminal_lifetime(self):
        events = [
            {"type": "goal/change", "time": 1000, "data": {
                "operation": "create",
                "goal": {"id": "g", "phase": "active", "revision": 1}}},
            {"type": "goal/change", "time": 2000, "data": {
                "operation": "block",
                "goal": {"id": "g", "phase": "blocked", "revision": 2}}},
            {"type": "goal/change", "time": 3000, "data": {
                "operation": "resume",
                "goal": {"id": "g", "phase": "active", "revision": 3}}},
            {"type": "goal/change", "time": 4000, "data": {
                "operation": "pause",
                "goal": {"id": "g", "phase": "paused", "revision": 4}}},
            {"type": "turn/end", "time": 10000, "data": {
                "turn": 1, "reason": {"kind": "completed"}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["outcome"], "paused")
        self.assertTrue(goal["paused"])
        self.assertFalse(goal["terminal"])
        self.assertIsNone(goal["ended_utc"])
        self.assertEqual(goal["duration_kind"], "ongoing")
        self.assertAlmostEqual(goal["duration_seconds"], 9.0, places=3)

    def test_edit_after_complete_does_not_extend_the_duration(self):
        events = [
            {"type": "goal/change", "time": 1000, "data": {
                "operation": "create",
                "goal": {"id": "g", "phase": "active", "revision": 1}}},
            {"type": "goal/change", "time": 2000, "data": {
                "operation": "complete",
                "goal": {"id": "g", "phase": "complete", "revision": 2}}},
            {"type": "goal/change", "time": 7000, "data": {
                "operation": "edit",
                "goal": {"id": "g", "phase": "complete", "revision": 3}}},
            {"type": "turn/end", "time": 10000, "data": {
                "turn": 1, "reason": {"kind": "completed"}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["outcome"], "completed")
        self.assertAlmostEqual(goal["duration_seconds"], 1.0, places=3)
        self.assertTrue(goal["ended_utc"].startswith(
            "1970-01-01T00:00:02"))

    def test_missing_goal_timestamps_do_not_emit_a_duration(self):
        events = [
            {"type": "goal/change", "data": {
                "operation": "create",
                "goal": {"id": "g", "phase": "active", "revision": 1}}},
            {"type": "goal/change", "time": 5000, "data": {
                "operation": "block",
                "goal": {"id": "g", "phase": "blocked", "revision": 2}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertEqual(goal["outcome"], "blocked")
        self.assertIsNone(goal["duration_seconds"])
        self.assertEqual(goal["duration_kind"], "unknown_start")
        self.assertIn("create event has no timestamp",
                      " ".join(goal["limitations"]))

    def test_out_of_order_goal_events_never_create_a_negative_duration(self):
        events = [
            {"type": "goal/change", "time": 5000, "data": {
                "operation": "create",
                "goal": {"id": "g", "phase": "active", "revision": 1}}},
            {"type": "goal/change", "time": 1000, "data": {
                "operation": "block",
                "goal": {"id": "g", "phase": "blocked", "revision": 2}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertIsNone(goal["duration_seconds"])
        self.assertTrue(goal["duration_invalid"])
        self.assertEqual(goal["duration_kind"], "invalid_order")
        self.assertIsNone(goal["ended_utc"])

    def test_goal_events_without_ids_are_explicitly_limited(self):
        events = [
            {"type": "goal/change", "time": 1000, "data": {
                "operation": "create", "goal": {"phase": "active"}}},
            {"type": "goal/change", "time": 2000, "data": {
                "operation": "complete", "goal": {"phase": "complete"}}},
        ]
        goal = dsh_efficiency.analyze(events)["goal"]
        self.assertTrue(goal["identity_missing"])
        self.assertIsNone(goal["selected_goal_id"])
        self.assertEqual(goal["outcome"], "completed")
        self.assertAlmostEqual(goal["duration_seconds"], 1.0, places=3)
        self.assertIn("no stable goal id", " ".join(goal["limitations"]))

    def test_absent_goal_does_not_synthesize_completion(self):
        events = [
            {"type": "turn/start", "time": 1000, "data": {"turn": 1}},
            {"type": "turn/end", "time": 2000, "data": {
                "turn": 1, "reason": {"kind": "completed"}}},
        ]
        value = dsh_efficiency.analyze(events)
        self.assertEqual(value["goal"]["outcome"], "no_goal")
        self.assertIsNone(value["goal"]["duration_seconds"])
        self.assertEqual(value["goal"]["last_turn_reason"], "completed")
        summary = value["summary"]
        self.assertEqual(summary["goal_outcome"], "no_goal")
        self.assertNotIn(summary["outcome"],
                         ("completed", "diagnosis_or_correction"))



class LaunchCommandBoundaryTests(unittest.TestCase):
    def analyze(self, *commands):
        events = []
        for index, command in enumerate(commands):
            call_id = f"c{index}"
            events.append({"type": "tool/call", "time": 1000 + index, "data": {
                "callId": call_id, "name": "bash",
                "arguments": {"command": command}}})
            events.append({"type": "tool/result", "time": 2000 + index, "data": {
                "toolCallId": call_id,
                "message": {"isError": False, "content": [
                    {"type": "text", "text": "ok"}]}}})
        return dsh_efficiency.analyze(events)

    def test_command_at_string_start(self):
        value = self.analyze(
            "python3 -m automation.m6_frame capture --output /tmp/a")
        self.assertEqual(value["eu4_launches"]["count"], 1)

    def test_command_after_and_semicolon_and_newline(self):
        value = self.analyze(
            "cd /tmp && python3 -m automation.m6_frame capture --output /tmp/a\n"
            "echo hi; python3 -m automation.m6_frame capture --output /tmp/b")
        self.assertEqual(value["eu4_launches"]["count"], 2)

    def test_multiple_real_invocations_in_one_script(self):
        value = self.analyze(
            "python3 -m automation.m6_frame capture --output /tmp/a; "
            "python3 -m automation.m6_frame capture --output /tmp/b; "
            "python3 -m automation.m6_frame capture --output /tmp/c")
        self.assertEqual(value["eu4_launches"]["count"], 3)

    def test_documentation_search_and_printed_command_are_not_launches(self):
        value = self.analyze(
            "rg 'python3 -m automation.m6_frame capture' "
            "analysis/m7/post206/reproduce.md\n"
            "echo 'python3 -m automation.m6_frame capture --output /tmp/a'")
        self.assertEqual(value["eu4_launches"]["count"], 0)

    def test_heredoc_source_text_is_not_a_launch(self):
        value = self.analyze(
            "cat > /tmp/note.txt <<'PY'\n"
            "python3 -m automation.m6_frame capture --output /tmp/a\n"
            "PY")
        self.assertEqual(value["eu4_launches"]["count"], 0)

    def test_real_command_after_heredoc_is_counted(self):
        value = self.analyze(
            "cat > /tmp/note.txt <<'PY'\n"
            "python3 -m automation.m6_frame capture --output /tmp/a\n"
            "PY\n"
            "python3 -m automation.m6_frame capture --output /tmp/b")
        self.assertEqual(value["eu4_launches"]["count"], 1)


class PhysicalLaunchJoinTests(unittest.TestCase):
    OUTPUT = ".local/m7/goal-round1/capture-a"
    STATE = ".local/m7/goal-round1/state.json"

    def analyze(self, *, claim=True, process=True, termination=True):
        events = []

        def call(call_id, moment, command, text):
            events.append({"type": "tool/call", "time": moment, "data": {
                "callId": call_id, "name": "bash",
                "arguments": {"command": command}}})
            events.append({"type": "tool/result", "time": moment + 100, "data": {
                "toolCallId": call_id,
                "message": {"isError": False, "content": [
                    {"type": "text", "text": text}]}}})

        call("capture", 1000,
             f'export EU4_CAMPAIGN_STATE="$PWD/{self.STATE}" && '
             f"python3.14 -m automation.m6_frame capture "
             f"--output {self.OUTPUT}",
             "started background job bash-1")
        if claim:
            call("guard", 2000,
                 "python3.14 -m automation.campaign_guard status "
                 f"--state {self.STATE}",
                 "counts {'corrections': 0, 'eu4_launches': 1, "
                 "'replays': 0, 'sealed_frames': 1} remaining {}")
        if process:
            call("ps", 3000, "ps -axo pid,etime,comm | grep eu4",
                 "12345       00:07 ./eu4")
        if termination:
            call("launcher", 4000,
                 f"cat {self.OUTPUT}/launcher-report.json",
                 ('termination {"pid": 12345, "terminated": true, '
                  '"killed": false, "executable": '
                  '"/Applications/EuropaUniversalisIV/eu4.app/Contents/'
                  'MacOS/eu4"}' if process else
                  "capture-a terminated True killed False"))
        return dsh_efficiency.analyze(events)

    def test_guard_claim_pid_and_termination_join_one_launch(self):
        value = self.analyze()
        self.assertEqual(value["eu4_launches"]["count"], 1)
        self.assertEqual(value["physical_work"]["confirmed_game_launches"], 1)
        confirmed = value["physical_work"]["confirmed_launch_evidence"][0]
        self.assertEqual(confirmed["claim"]["kind"], "guard_count")
        self.assertEqual(confirmed["process"]["pid"], 12345)
        self.assertEqual(confirmed["termination"]["pid"], 12345)
        self.assertEqual(
            value["physical_work"]["ambiguous_launch_attempts"], [])

    def test_claim_without_termination_is_not_confirmed(self):
        value = self.analyze(termination=False)
        self.assertEqual(value["physical_work"]["confirmed_game_launches"], 0)
        ambiguous = value["physical_work"]["ambiguous_launch_attempts"]
        self.assertEqual(len(ambiguous), 1)
        self.assertEqual(ambiguous[0]["reason"], "claim_without_termination")
        self.assertEqual(ambiguous[0]["missing"], ["termination"])

    def test_claim_without_process_identity_is_not_confirmed(self):
        value = self.analyze(process=False)
        self.assertEqual(value["physical_work"]["confirmed_game_launches"], 0)
        ambiguous = value["physical_work"]["ambiguous_launch_attempts"]
        self.assertEqual(ambiguous[0]["reason"],
                         "claim_without_process_identity")

    def test_launch_without_guard_claim_is_not_confirmed(self):
        value = self.analyze(claim=False)
        self.assertEqual(value["physical_work"]["confirmed_game_launches"], 0)
        ambiguous = value["physical_work"]["ambiguous_launch_attempts"]
        self.assertEqual(ambiguous[0]["reason"], "launch_without_guard_claim")

    def test_failed_attempt_stays_separate_from_confirmed_launches(self):
        events = []
        events.append({"type": "tool/call", "time": 1000, "data": {
            "callId": "capture", "name": "bash",
            "arguments": {"command":
                          "python3.14 -m automation.m6_frame capture "
                          "--output .local/m7/capture-a"}}})
        events.append({"type": "tool/result", "time": 1100, "data": {
            "toolCallId": "capture", "message": {"isError": False, "content": [
                {"type": "text", "text": "capture failed: refused\n"
                                         "[exit code: 4]"}]}}})
        events.append({"type": "tool/call", "time": 2000, "data": {
            "callId": "guard", "name": "bash",
            "arguments": {"command":
                          "python3.14 -m automation.campaign_guard status "
                          "--state .local/m7/state.json"}}})
        events.append({"type": "tool/result", "time": 2100, "data": {
            "toolCallId": "guard", "message": {"isError": False, "content": [
                {"type": "text", "text":
                 "counts {'eu4_launches': 1} remaining {}"}]}}})
        value = dsh_efficiency.analyze(events)
        physical = value["physical_work"]
        self.assertEqual(physical["confirmed_game_launches"], 0)
        self.assertEqual(len(physical["failed_launch_attempts"]), 1)
        self.assertEqual(physical["failed_launch_attempts"][0]["status"],
                         "failed")


if __name__ == "__main__":
    unittest.main()
