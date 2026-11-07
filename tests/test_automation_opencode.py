"""Offline HTTP tests for the verified OpenCode 2.2.5 shapes."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from automation.agents import ContractError, Observation
from automation.opencode import OpenCodeAgent, permission_rules


class App:
    def __init__(self) -> None:
        self.directory = ""
        self.force_directory = None
        self.model = {"providerID": "opencode", "id": "big-pickle"}
        self.session_status = 200
        self.sleep = False
        self.active = False
        self.wait_status = 204
        self.inbox: list[dict] = []
        self.paths: list[str] = []
        self.sequence = None
        self.sequence_at = 0
        self.malformed = False
        self.permission: list[dict] = []
        self.seen: list[str] = []
        self.pages = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def log_message(self, fmt: str, *args) -> None:
        return

    def _handle(self) -> None:
        app: App = self.server.app  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else {}
        path = self.path.split("?", 1)[0]
        app.paths.append(path)
        if app.malformed and self.command == "GET" and path.count("/") == 3:
            raw = b"{"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if app.sleep and path.startswith("/api/session/") and self.command == "GET" and path.count("/") == 3:
            time.sleep(2)
        if path == "/api/session" and self.command == "POST":
            directory = app.force_directory or body["location"]["directory"]
            self._send(200, {"data": {
                "id": "ses_test",
                "location": {"directory": directory},
                "model": app.model,
            }})
            return
        if path == "/api/session/active":
            data = {"ses_test": {"type": "running"}} if app.active else {}
            self._send(200, {"data": data})
            return
        if path.endswith("/permission"):
            self._send(200, {"data": app.permission})
            return
        if path.endswith("/inbox"):
            self._send(200, {"data": app.inbox})
            return
        if path.endswith("/message"):
            self._send(200, self._page(app))
            return
        if path.endswith("/wait"):
            self._send(app.wait_status, None)
            return
        if path.endswith("/prompt"):
            submission = body["id"]
            if submission in app.seen:
                self._send(409, {"_tag": "ConflictError", "message": "exists"})
                return
            app.seen.append(submission)
            app.inbox.append({"id": submission, "type": "user"})
            self._send(200, {"data": {"id": submission}})
            return
        if path.startswith("/api/session/"):
            if app.session_status != 200:
                self._send(app.session_status, {"_tag": "NotFoundError"})
                return
            self._send(200, {"data": {
                "id": "ses_test",
                "location": {"directory": app.directory or "/workspace"},
                "model": app.model,
            }})
            return
        self._send(500, {"_tag": "UnknownError"})

    def _page(self, app: App) -> dict:
        if app.sequence is not None:
            page = app.sequence[min(app.sequence_at, len(app.sequence) - 1)]
            app.sequence_at += 1
            return page
        if app.pages is None:
            return {"data": [], "cursor": {"next": None, "previous": None}}
        if "cursor=" in self.path:
            return app.pages[1]
        return app.pages[0]

    def _send(self, code: int, payload) -> None:
        raw = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if raw:
            self.wfile.write(raw)


class PermissionRuleTests(unittest.TestCase):
    def test_relative_reads_are_allowed_and_the_protected_tree_is_denied(self) -> None:
        protected = Path("/tmp/campaign").resolve()
        rules = permission_rules(Path("/tmp/ws"), Path("/tmp/campaign"))
        self.assertEqual(rules[0], {
            "action": "read", "resource": str(protected) + "/**", "effect": "deny"})
        self.assertIn({"action": "read", "resource": "**", "effect": "allow"}, rules)
        self.assertIn({
            "action": "skill",
            "resource": "opengl-metal-reconstruction",
            "effect": "deny",
        }, rules)
        allowed = permission_rules(Path("/tmp/ws"), Path("/tmp/campaign"), skill=True)
        self.assertIn({
            "action": "skill",
            "resource": "opengl-metal-reconstruction",
            "effect": "allow",
        }, allowed)


class OpenCodeHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = App()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def agent(self, **kwargs) -> OpenCodeAgent:
        values = {
            "timeout": 1,
            "model": "opencode/big-pickle",
        }
        values.update(kwargs)
        return OpenCodeAgent(self.url, **values)

    def test_refuses_a_non_loopback_server(self) -> None:
        with self.assertRaises(ValueError):
            OpenCodeAgent(
                "http://example.com", timeout=1, model="opencode/big-pickle")

    def test_rejects_a_different_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self.app.force_directory = "/not/the/workspace"
            with self.assertRaises(ContractError) as caught:
                self.agent().create(workspace, "hello")
            self.assertEqual(caught.exception.reason, "workspace_mismatch")

    def test_malformed_json_is_unknown(self) -> None:
        self.app.malformed = True
        self.assertEqual(self.agent().inspect("ses_test", "msg_a").phase, "unknown")

    def test_timeout_is_unknown(self) -> None:
        self.app.sleep = True
        agent = self.agent(timeout=0.2)
        self.assertEqual(agent.inspect("ses_test", "msg_a").phase, "unknown")

    def test_queued_admission_is_not_settled(self) -> None:
        self.app.active = True
        self.app.inbox = [{"id": "msg_a", "type": "user"}]
        observation = self.agent().inspect("ses_test", "msg_a")
        self.assertEqual(observation, Observation("queued", "present"))

    def test_delivered_without_idle_is_not_settled(self) -> None:
        self.app.pages = [
            {"data": [{"id": "msg_a", "type": "user", "text": "go"}],
             "cursor": {"next": "more", "previous": None}},
            {"data": [{"id": "msg_b", "type": "assistant", "content": []}],
             "cursor": {"next": None, "previous": "more"}},
        ]
        observation = self.agent().inspect("ses_test", "msg_a")
        self.assertNotEqual(observation.phase, "settled")
        history = self.agent().history("ses_test")
        self.assertEqual([item["id"] for item in history.raw], ["msg_a", "msg_b"])

    def test_idle_after_the_user_message_is_settled(self) -> None:
        self.app.pages = [
            {"data": [{"id": "msg_a", "type": "user", "text": "go"}],
             "cursor": {"next": None, "previous": None}},
            {"data": [], "cursor": {"next": None}},
        ]
        self.app.pages[0]["data"].append(
            {"id": "msg_idle", "type": "idle", "outcome": "succeeded"})
        self.assertEqual(self.agent().inspect("ses_test", "msg_a").phase, "settled")

    def test_missing_server_error_and_active_session(self) -> None:
        agent = self.agent()
        self.app.session_status = 404
        self.assertEqual(agent.inspect("ses_test", "msg_a").phase, "missing")
        self.app.session_status = 500
        self.assertEqual(agent.inspect("ses_test", "msg_a").phase, "unknown")
        self.app.session_status = 200
        self.app.active = True
        self.app.paths.clear()
        self.assertEqual(agent.inspect("ses_test", "msg_a").phase, "executing")
        self.assertFalse(any(path.endswith("/wait") for path in self.app.paths))

    def test_repeated_end_cursor_is_complete(self) -> None:
        self.app.sequence = [
            {"data": [
                {"id": "msg_a", "type": "user", "text": "go"},
                {"id": "msg_idle", "type": "idle", "outcome": "succeeded"},
            ], "cursor": {"next": "end"}},
            {"data": [
                {"id": "msg_idle", "type": "idle", "outcome": "succeeded"},
            ], "cursor": {"next": "end"}},
        ]
        self.assertEqual(self.agent().inspect("ses_test", "msg_a").phase, "settled")

    def test_changed_cursor_without_new_ids_is_incomplete(self) -> None:
        self.app.sequence = [
            {"data": [{"id": "msg_a", "type": "user", "text": "go"}],
             "cursor": {"next": "c1"}},
            {"data": [{"id": "msg_a", "type": "user", "text": "go"}],
             "cursor": {"next": "c2"}},
        ]
        with self.assertRaises(ContractError) as caught:
            self.agent().history("ses_test")
        self.assertEqual(caught.exception.reason, "history_incomplete")

    def test_page_cap_with_a_live_cursor_is_incomplete(self) -> None:
        self.app.sequence = [
            {"data": [{"id": f"m{index}", "type": "user", "text": "x"}],
             "cursor": {"next": f"c{index}"}}
            for index in range(100)
        ]
        with self.assertRaises(ContractError) as caught:
            self.agent().history("ses_test")
        self.assertEqual(caught.exception.reason, "history_incomplete")

    def test_conflict_reuses_the_same_submission_id(self) -> None:
        agent = self.agent()
        self.app.inbox = [{"id": "msg_a", "type": "user"}]
        self.app.seen.append("msg_a")
        first = agent.submit("ses_test", "again", "msg_a")
        self.assertEqual(first.phase, "queued")
        self.assertEqual(self.app.seen, ["msg_a"])


if __name__ == "__main__":
    unittest.main()
