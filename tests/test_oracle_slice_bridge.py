"""P3 slice: budget ledger and the five-tool MCP bridge (offline, no model)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from oracle.slice import bridge as bridge_mod
from oracle.slice import compare, corpus, harness, runstate
from oracle.slice.bridge import Bridge, MCPServer, certify_candidate, write_controller_token
from oracle.slice.budget import (
    BudgetDenied,
    BudgetExceeded,
    BudgetConflict,
    RunBudgetLedger,
)

SLICE_DIR = Path(__file__).resolve().parents[1] / "oracle" / "slice"
REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "run-test-0001"


def _make_run(root: Path, limit: int = 20, candidate: Path | None = None,
              deadline_s: float = 300.0, budget_s: float = 300.0) -> tuple[Path, Path, str]:
    run_dir = root / "run"
    workspace = root / "workspace"
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    (run_dir / bridge_mod.RUN_ID_FILE).write_text(RUN_ID, encoding="utf-8")
    (workspace / "candidate.c").write_text(
        (candidate or (SLICE_DIR / "target.c")).read_text(encoding="utf-8"), encoding="utf-8"
    )
    RunBudgetLedger.create(run_dir, RUN_ID, limit).close()
    token = write_controller_token(run_dir)
    # Trusted run state: the absolute monotonic deadline the MCP server enforces.
    runstate.write_run_state(run_dir, run_id=RUN_ID,
                             deadline_monotonic=time.monotonic() + deadline_s,
                             budget_seconds=budget_s)
    return run_dir, workspace, token


def _bridge(run_dir: Path, workspace: Path, *, deadline: float | None = None) -> Bridge:
    ledger = RunBudgetLedger.attach(run_dir / "budget.sqlite3", RUN_ID)
    return Bridge(run_dir=run_dir, workspace=workspace, ledger=ledger, run_deadline=deadline)


def _call(server: MCPServer, request_id: int, method: str, params: dict | None = None) -> dict:
    response = server.handle({"jsonrpc": "2.0", "id": request_id, "method": method,
                              "params": params or {}})
    assert response is not None
    return response


def _tool_text(response: dict) -> dict:
    return json.loads(response["result"]["content"][0]["text"])


class ToolsListTests(unittest.TestCase):
    def test_exactly_five_agent_visible_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, workspace, _ = _make_run(Path(tmp))
            server = MCPServer(_bridge(run_dir, workspace))
            result = _call(server, 1, "tools/list")["result"]
            names = {tool["name"] for tool in result["tools"]}
            self.assertEqual(names, {"inspect", "fixtures", "coverage", "test", "diff"})

    def test_tools_use_bare_names_so_opencode_prefixes_once(self):
        """Raw names must be bare; OpenCode turns `inspect` into `oracle_inspect`."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, workspace, _ = _make_run(Path(tmp))
            server = MCPServer(_bridge(run_dir, workspace))
            names = {t["name"] for t in _call(server, 1, "tools/list")["result"]["tools"]}
            self.assertFalse(any(name.startswith("oracle_") for name in names))

    def test_initialize_reports_protocol_and_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, workspace, _ = _make_run(Path(tmp))
            server = MCPServer(_bridge(run_dir, workspace))
            result = _call(server, 1, "initialize", {"protocolVersion": "2024-11-05"})["result"]
            self.assertEqual(result["protocolVersion"], "2024-11-05")
            self.assertEqual(result["serverInfo"]["name"], bridge_mod.SERVER_NAME)
            self.assertIn("tools", result["capabilities"])

    def test_unknown_method_is_a_jsonrpc_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, workspace, _ = _make_run(Path(tmp))
            server = MCPServer(_bridge(run_dir, workspace))
            response = _call(server, 1, "does/not/exist")
            self.assertEqual(response["error"]["code"], -32601)


class CertificationIsNotAgentFacingTests(unittest.TestCase):
    FORGED = ("certify", "oracle_certify", "oracle_oracle_certify", "certify_candidate",
              "oracle_certify_candidate")

    def test_forged_certify_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, workspace, _ = _make_run(Path(tmp))
            server = MCPServer(_bridge(run_dir, workspace))
            for index, name in enumerate(self.FORGED, start=1):
                response = _call(server, index, "tools/call", {"name": name, "arguments": {}})
                payload = _tool_text(response)
                self.assertTrue(response["result"]["isError"], name)
                self.assertIn("unknown tool", payload["error"])

    def test_certify_never_appears_even_after_probing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, workspace, _ = _make_run(Path(tmp))
            server = MCPServer(_bridge(run_dir, workspace))
            _call(server, 1, "tools/call", {"name": "certify", "arguments": {}})
            names = {t["name"] for t in _call(server, 2, "tools/list")["result"]["tools"]}
            self.assertNotIn("certify", names)

    def test_certify_function_requires_controller_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, _, _ = _make_run(Path(tmp))
            snapshot = Path(tmp) / "snapshot.c"
            snapshot.write_text((SLICE_DIR / "reference.c").read_text(), encoding="utf-8")
            with self.assertRaises(Exception):
                certify_candidate(snapshot, run_dir=run_dir, run_id=RUN_ID, token="wrong")

    def test_certify_returns_outcome_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, _, token = _make_run(Path(tmp))
            snapshot = Path(tmp) / "snapshot.c"
            snapshot.write_text((SLICE_DIR / "reference.c").read_text(), encoding="utf-8")
            verdict = certify_candidate(snapshot, run_dir=run_dir, run_id=RUN_ID, token=token)
            self.assertEqual(verdict["status"], compare.STATUS_PASS)
            self.assertEqual(verdict["total"], len(corpus.load_corpus(corpus.HELD_OUT)))
            blob = json.dumps(verdict)
            for leaked in ("expected", "return_hex", "post_memory", "effects"):
                self.assertNotIn(leaked, blob)


class VisibleToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.run_dir, self.workspace, self.token = _make_run(self.root)
        self.bridge = _bridge(self.run_dir, self.workspace)

    def tearDown(self):
        self._tmp.cleanup()

    def test_inspect_exposes_static_evidence_without_expectations(self):
        result = self.bridge.call("inspect", {"function_id": "synth:apply"})
        blob = json.dumps(result)
        self.assertEqual(result["function_id"], "synth:apply")
        self.assertNotIn("expected", blob)
        self.assertNotIn("post_memory", blob)

    def test_fixtures_lists_visible_inputs_without_expectations(self):
        result = self.bridge.call("fixtures", {"function_id": "synth:apply", "limit": 5})
        self.assertLessEqual(len(result["fixtures"]), 5)
        blob = json.dumps(result)
        for leaked in ("expected", "return_hex", "post_memory", "effects"):
            self.assertNotIn(leaked, blob)

    def test_coverage_reports_visible_only(self):
        result = self.bridge.call("coverage", {"function_id": "synth:apply"})
        self.assertEqual(result["visible_fixtures"], len(corpus.load_corpus(corpus.VISIBLE)))
        self.assertEqual(result["held_out"], "restricted")

    def test_test_then_diff_localizes_the_defect(self):
        result = self.bridge.call("test", {"function_id": "synth:apply", "candidate_path": "candidate.c"})
        self.assertEqual(result["status"], compare.STATUS_MISMATCH)
        self.assertTrue(result["failed"])
        diff = self.bridge.call("diff", {"run_id": result["test_run_id"], "max_events": 4})
        self.assertEqual(diff["first_divergence"]["domain"], "memory")
        self.assertEqual(diff["first_divergence"]["byte_offset"], 4)

    def test_unknown_function_is_rejected(self):
        with self.assertRaises(bridge_mod.ToolError):
            self.bridge.call("fixtures", {"function_id": "nope"})

    def test_candidate_path_cannot_escape_workspace(self):
        for bad in ("../escape.c", "/etc/passwd", "sub/../../escape.c"):
            with self.assertRaises(bridge_mod.ToolError):
                self.bridge.call("test", {"function_id": "synth:apply", "candidate_path": bad})

    def test_candidate_must_be_a_c_file(self):
        (self.workspace / "notes.txt").write_text("hi", encoding="utf-8")
        with self.assertRaises(bridge_mod.ToolError):
            self.bridge.call("test", {"function_id": "synth:apply", "candidate_path": "notes.txt"})

    def test_diff_rejects_unknown_run_id(self):
        with self.assertRaises(bridge_mod.ToolError):
            self.bridge.call("diff", {"run_id": "not-a-run"})

    def test_budget_is_consumed_on_failure_too(self):
        before = self.bridge.ledger.granted()
        with self.assertRaises(bridge_mod.ToolError):
            self.bridge.call("test", {"function_id": "synth:apply", "candidate_path": "../x.c"})
        self.assertEqual(self.bridge.ledger.granted(), before + 1)

    def test_budget_exhaustion_denies_further_calls(self):
        self.bridge.ledger.close()
        RunBudgetLedger.attach(self.run_dir / "budget.sqlite3", RUN_ID).close()
        ledger = RunBudgetLedger.attach(self.run_dir / "budget.sqlite3", RUN_ID)
        # Rewrite the run with a tiny budget to force exhaustion quickly.
        ledger._conn.execute("UPDATE runs SET limit_calls=2 WHERE run_id=?", (RUN_ID,))
        ledger.close()
        bridge = _bridge(self.run_dir, self.workspace)
        bridge.call("inspect", {"function_id": "synth:apply"})
        bridge.call("inspect", {"function_id": "synth:apply"})
        with self.assertRaises(bridge_mod.ToolError) as ctx:
            bridge.call("inspect", {"function_id": "synth:apply"})
        self.assertIn("budget exhausted", str(ctx.exception))

    def test_restart_does_not_reset_budget(self):
        self.bridge.call("inspect", {"function_id": "synth:apply"})
        granted = self.bridge.ledger.granted()
        self.bridge.ledger.close()
        # Simulate an MCP server restart: a fresh ledger handle on the same file.
        restarted = _bridge(self.run_dir, self.workspace)
        self.assertEqual(restarted.ledger.granted(), granted)
        restarted.call("inspect", {"function_id": "synth:apply"})
        self.assertEqual(restarted.ledger.granted(), granted + 1)


class BudgetLedgerTests(unittest.TestCase):
    def test_missing_run_denies(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            with self.assertRaises(BudgetDenied):
                RunBudgetLedger.attach(run_dir / "budget.sqlite3", "ghost")

    def test_conflicting_limit_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            RunBudgetLedger.create(run_dir, "r", 5).close()
            with self.assertRaises(BudgetConflict):
                RunBudgetLedger.create(run_dir, "r", 9)

    def test_corrupt_ledger_denies(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "budget.sqlite3"
            RunBudgetLedger.create(Path(tmp), "r", 3).close()
            path.write_bytes(b"this is not a database")
            with self.assertRaises(BudgetDenied):
                RunBudgetLedger.attach(path, "r")

    def test_reserve_is_exact_at_the_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = RunBudgetLedger.create(Path(tmp), "r", 3)
            self.assertEqual(ledger.reserve(), 1)
            self.assertEqual(ledger.reserve(2), 3)
            with self.assertRaises(BudgetExceeded):
                ledger.reserve()
            self.assertEqual(ledger.granted(), 3)

    def test_reserve_honours_the_run_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = RunBudgetLedger.create(Path(tmp), "r", 3)
            started = time.monotonic()
            with self.assertRaises(BudgetDenied):
                ledger.reserve(deadline=time.monotonic() - 1.0)
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertEqual(ledger.granted(), 0)

    def test_concurrent_reservations_never_over_admit(self):
        with tempfile.TemporaryDirectory() as tmp:
            limit = 9
            RunBudgetLedger.create(Path(tmp), "r", limit).close()
            barrier = threading.Barrier(12)
            results: list[str] = []
            lock = threading.Lock()

            def worker() -> None:
                ledger = RunBudgetLedger.attach(Path(tmp) / "budget.sqlite3", "r")
                barrier.wait()
                try:
                    ledger.reserve()
                    outcome = "granted"
                except BudgetExceeded:
                    outcome = "denied"
                finally:
                    ledger.close()
                with lock:
                    results.append(outcome)

            threads = [threading.Thread(target=worker) for _ in range(12)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(results.count("granted"), limit)
            self.assertEqual(results.count("denied"), 12 - limit)
            self.assertEqual(RunBudgetLedger.attach(Path(tmp) / "budget.sqlite3", "r").granted(), limit)


class MCPProtocolSmokeTest(unittest.TestCase):
    """Genuine MCP handshake and tool invocation over a real stdio subprocess."""

    def test_stdio_handshake_and_structured_tool_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, workspace, _ = _make_run(Path(tmp))
            proc = _start_server(run_dir, workspace)
            try:
                init = _rpc(proc, 1, "initialize", {"protocolVersion": "2024-11-05"})
                self.assertEqual(init["result"]["serverInfo"]["name"], bridge_mod.SERVER_NAME)

                tools = _rpc(proc, 2, "tools/list")["result"]["tools"]
                self.assertEqual({t["name"] for t in tools},
                                 {"inspect", "fixtures", "coverage", "test", "diff"})

                called = _rpc(proc, 3, "tools/call",
                              {"name": "inspect", "arguments": {"function_id": "synth:apply"}})
                self.assertFalse(called["result"]["isError"])
                payload = json.loads(called["result"]["content"][0]["text"])
                self.assertEqual(payload["function_id"], "synth:apply")

                denied = _rpc(proc, 4, "tools/call", {"name": "certify", "arguments": {}})
                self.assertTrue(denied["result"]["isError"])
            finally:
                _stop_server(proc)


class DeadlineEnforcementTests(unittest.TestCase):
    """The deadline comes from trusted run state, never from the agent."""

    def test_expired_deadline_refuses_calls_before_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, workspace, _ = _make_run(Path(tmp))
            bridge = _bridge(run_dir, workspace, deadline=time.monotonic() - 1.0)
            before = bridge.ledger.granted()
            with self.assertRaises(bridge_mod.ToolError) as ctx:
                bridge.call("inspect", {"function_id": "synth:apply"})
            self.assertIn("deadline", str(ctx.exception))
            # Refusal happens before admission: no budget is consumed.
            self.assertEqual(bridge.ledger.granted(), before)

    def test_arguments_must_be_an_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, workspace, _ = _make_run(Path(tmp))
            server = MCPServer(_bridge(run_dir, workspace))
            for bad in ([1, 2], "inspect", 7):
                response = _call(server, 1, "tools/call", {"name": "inspect", "arguments": bad})
                self.assertTrue(response["result"]["isError"], repr(bad))
                self.assertIn("object", _tool_text(response)["error"])

    def test_stdio_server_refuses_calls_after_expiry_and_does_not_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, workspace, _ = _make_run(root, deadline_s=-5.0)
            # Two server processes: the deadline is persisted, so a restart still
            # enforces it rather than granting a fresh budget.
            for attempt in range(2):
                proc = _start_server(run_dir, workspace)
                try:
                    init = _rpc(proc, 1, "initialize", {"protocolVersion": "2024-11-05"})
                    self.assertEqual(init["result"]["serverInfo"]["name"], bridge_mod.SERVER_NAME)
                    refused = _rpc(proc, 2, "tools/call", {
                        "name": "test",
                        "arguments": {"function_id": "synth:apply", "candidate_path": "candidate.c",
                                      # A deadline forged by the agent is ignored.
                                      "deadline": 1e18, "timeout": 1e18},
                    })
                    self.assertTrue(refused["result"]["isError"], f"attempt {attempt}")
                    self.assertIn("deadline", _tool_text(refused)["error"])
                finally:
                    _stop_server(proc)
            builds = run_dir / bridge_mod.BUILD_DIR
            self.assertFalse(builds.exists() and any(builds.iterdir()),
                             "no compilation may begin after the deadline")

    def test_missing_run_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, workspace, _ = _make_run(root)
            (run_dir / runstate.RUN_STATE_FILE).unlink()
            proc = _start_server(run_dir, workspace)
            try:
                self.assertEqual(proc.wait(timeout=15), 2)
            finally:
                _stop_server(proc)


def _start_server(run_dir: Path, workspace: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), str(REPO_ROOT / "benchmark")])
    return subprocess.Popen(
        [sys.executable, "-m", "oracle.slice.bridge", "serve",
         "--run-dir", str(run_dir), "--workspace", str(workspace), "--run-id", RUN_ID],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=str(REPO_ROOT), env=env,
    )


def _rpc(proc: subprocess.Popen, request_id: int, method: str,
         params: dict | None = None) -> dict:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                 "method": method, "params": params or {}}) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        stderr = proc.stderr.read() if proc.stderr else ""
        raise AssertionError(f"server closed the stream: {stderr}")
    return json.loads(line)


def _stop_server(proc: subprocess.Popen) -> None:
    if proc.stdin:
        proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        proc.kill()
    for stream in (proc.stdout, proc.stderr):
        if stream:
            stream.close()


if __name__ == "__main__":
    unittest.main()
