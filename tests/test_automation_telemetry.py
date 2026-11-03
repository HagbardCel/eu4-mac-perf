"""Normalized traces keep tool transitions and drop replayed ids."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation.opencode import normalize
from automation.telemetry import append, redact, report


def _record(event_id: str, kind: str, **extra) -> dict:
    record = {
        "schema": 1,
        "ts": "2026-11-03T00:00:00+00:00",
        "campaign_id": "c",
        "runtime": "opencode",
        "session_id": "ses",
        "event_id": event_id,
        "type": kind,
    }
    record.update(extra)
    return record


class TelemetryTests(unittest.TestCase):
    def test_running_tool_and_later_completion_both_survive_a_reread(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            started = _record("m/p/tool.started", "tool.started", tool="bash", status="running")
            completed = _record(
                "m/p/tool.completed", "tool.completed", tool="bash", status="completed")
            append(path, [started])
            append(path, [completed])
            append(path, [started, completed])
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(
                [row["event_id"] for row in rows],
                ["m/p/tool.started", "m/p/tool.completed"],
            )

    def test_duration_comes_only_from_timestamps(self) -> None:
        running = {
            "id": "msg_a",
            "type": "assistant",
            "content": [{
                "type": "tool",
                "id": "call_1",
                "name": "bash",
                "state": {"status": "running", "input": {"path": "marker.txt"}},
                "time": {"created": 10},
            }],
        }
        finished = {
            "id": "msg_a",
            "type": "assistant",
            "content": [{
                "type": "tool",
                "id": "call_1",
                "name": "bash",
                "state": {
                    "status": "completed",
                    "input": {"path": "marker.txt"},
                    "content": [{"text": "ok"}],
                },
                "time": {"created": 10, "ran": 10, "completed": 40},
            }],
            "tokens": {"input": 3, "output": 1},
            "cost": 0,
        }
        first = {event["event_id"]: event for event in normalize([running])}
        second = {event["event_id"]: event for event in normalize([finished])}
        self.assertNotIn("duration_ms", first["msg_a/call_1/tool.started"])
        self.assertEqual(second["msg_a/call_1/tool.completed"]["duration_ms"], 30)
        self.assertNotIn("msg_a/call_1/tool.started", second)
        denied = normalize([{
            "id": "msg_b",
            "type": "assistant",
            "content": [{
                "type": "tool",
                "id": "call_2",
                "name": "read",
                "state": {"status": "error", "input": {}, "error": {"message": "Permission denied: read"}},
                "time": {"ran": 1, "completed": 2},
            }],
        }])
        self.assertEqual(denied[0]["text"], "Permission denied: read")
        command = "echo hi"
        recorded = normalize([{
            "id": "msg_c",
            "type": "assistant",
            "content": [{
                "type": "tool",
                "id": "call_9",
                "name": "bash",
                "state": {
                    "status": "completed",
                    "input": {"command": command, "note": "x" * 250},
                },
                "time": {},
            }],
        }])
        self.assertEqual(recorded[0]["args"]["command"], command)
        self.assertEqual(len(recorded[0]["args"]["note"]), 200)

    def test_redacts_secrets_and_spills_large_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            append(path, [_record(
                "m/message", "message", text="x" * 5000,
                note="OPENCODE_SERVER_PASSWORD=sekret",
            )], secrets=["sekret"])
            row = json.loads(path.read_text())
            self.assertNotIn("sekret", path.read_text())
            hidden = redact(
                {"token": "secret123", "tokens": {"input": 4}, "note": "PASSWORD=sekret"},
                [])
            self.assertEqual(hidden["token"], "[redacted]")
            self.assertEqual(hidden["tokens"], {"input": 4})
            self.assertNotIn("sekret", hidden["note"])
            self.assertIn("[redacted]", row["note"])
            self.assertNotIn("text", row)
            self.assertEqual(row["blob"]["size"], 5000)
            summary = report(path)
            self.assertEqual(summary["tool_calls"], 0)

    def test_report_counts_validation_and_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            append(path, [
                _record("a", "tool.started", tool="bash"),
                _record("b", "tool.completed", tool="bash", status="error", path="marker.txt"),
                _record("c", "validation", verdict="fail", rounds=1, elapsed_seconds=1.5),
                _record("d", "validation", verdict="pass", rounds=2, elapsed_seconds=2.5),
            ])
            summary = report(path)
            self.assertEqual(summary["tool_calls"], 2)
            self.assertEqual(summary["tool_failures"], 1)
            self.assertEqual(summary["validations"], 2)
            self.assertEqual(summary["validation_passes"], 1)
            self.assertEqual(summary["files"], ["marker.txt"])
            self.assertEqual(summary["rounds"], 2)
            self.assertEqual(summary["elapsed_seconds"], 2.5)

    def test_tool_calls_count_terminal_events_and_not_both_states(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            append(path, [
                _record("m/p/tool.started", "tool.started", tool="bash", status="running"),
                _record("m/p/tool.completed", "tool.completed", tool="bash", status="completed"),
                _record("m/q/tool.completed", "tool.completed", tool="bash",
                        status="completed", args={"command": "echo hi"}),
            ])
            path.write_text(
                path.read_text(encoding="utf-8")
                + json.dumps(_record(
                    "m/q/tool.completed", "tool.completed", tool="bash",
                    status="completed", session_id="ses_other",
                    args={"command": "echo hi"}))
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(report(path)["tool_calls"], 3)


if __name__ == "__main__":
    unittest.main()
