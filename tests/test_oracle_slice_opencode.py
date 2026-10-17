"""P3-B: the pinned OpenCode binding, exercised offline.

No model, no network and no real ``opencode`` binary are involved. The adapter is
driven against a *stub* executable that speaks the pinned wire formats (a
``--version`` line and a line-delimited JSON event stream on ``run``) and that
repairs the candidate the way a model would. That makes the binding, the evidence
chain and the seal/verify path testable without a run.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from oracle.core import canonical_bytes
from oracle.slice import controller, corpus, harness, opencode, receipts
from oracle.slice.controller import (
    STATUS_ABORTED,
    STATUS_CERTIFIED,
    RunConfig,
)
from oracle.slice.opencode import OpenCodeAdapter, OpenCodeRun, materialize

SLICE_DIR = Path(__file__).resolve().parents[1] / "oracle" / "slice"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _tool_event(tool, status, arguments, error=None):
    state = {"status": status, "input": arguments}
    if error is not None:
        state["error"] = error
    return {"type": "tool_use", "part": {"tool": tool, "state": state}}


#: A minimal, real MCP stdio client. The double uses it to talk to the *actual*
#: trusted bridge over the pinned newline-delimited JSON-RPC transport, so its
#: oracle calls produce genuine ledger reservations and genuine trusted records.
_MCP_CLIENT_SOURCE = '''
import json
import subprocess


class MCPClient:
    def __init__(self, command, environment):
        self._proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, env=environment, bufsize=1)
        self._next_id = 0

    def _request(self, method, params=None):
        self._next_id += 1
        request_id = self._next_id
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._proc.stdin.write(json.dumps(message) + "\\n")
        self._proc.stdin.flush()
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError("the trusted bridge closed its stream")
            reply = json.loads(line)
            if isinstance(reply, dict) and reply.get("id") == request_id:
                if "error" in reply:
                    raise RuntimeError(f"{method} failed: {reply['error']}")
                return reply.get("result")

    def initialize(self):
        return self._request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "opencode-stub", "version": "1"}})

    def close(self):
        try:
            self._proc.stdin.close()
            self._proc.wait(timeout=30)
        except Exception:
            self._proc.kill()

    def tool(self, name, arguments):
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            return {"status": "error", "error": "malformed tool result"}
        if result.get("isError"):
            text = "".join(block.get("text", "") for block in (result.get("content") or [])
                           if isinstance(block, dict) and block.get("type") == "text")
            return {"status": "error", "error": text}
        return {"status": "completed"}
'''

_STUB_SOURCE = '''
import json
import os
import shutil
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
if "--version" in sys.argv:
    print("opencode v9.9.9-stub")
    sys.exit(0)

sys.path.insert(0, str(here))
import mcp_stub_client  # noqa: E402

workspace = Path.cwd()
config = json.loads(Path(os.environ["OPENCODE_CONFIG"]).read_text(encoding="utf-8"))
server = config["mcp"]["servers"]["oracle"]
skeleton = [event for event in json.loads((here / "events.json").read_text(encoding="utf-8"))
            if not (isinstance(event, dict) and event.get("type") == "tool_use"
                    and isinstance(event.get("part"), dict)
                    and str(event["part"].get("tool", "")).startswith("oracle_"))]
mcp_calls = json.loads((here / "mcp_calls.json").read_text(encoding="utf-8"))
write_candidate = json.loads((here / "write_candidate.json").read_text(encoding="utf-8"))


def emit(event):
    sys.stdout.write(json.dumps(event) + "\\n")
    sys.stdout.flush()


if write_candidate:
    shutil.copyfile(here / "repair.c", workspace / "candidate.c")

emit({"type": "step_start", "part": {}})
emit({"type": "text", "part": {"text": "Inspect the declared evidence, then test the repair."}})

client = mcp_stub_client.MCPClient(server["command"], {**os.environ, **server["environment"]})
client.initialize()
try:
    for call in mcp_calls:
        outcome = client.tool(call["tool"], call["arguments"])
        state = {"status": outcome["status"], "input": call["arguments"]}
        if outcome["status"] == "error":
            state["error"] = outcome["error"]
        emit({"type": "tool_use", "part": {"tool": "oracle_" + call["tool"], "state": state}})
finally:
    client.close()

for event in skeleton:
    emit(event)
'''


def _completed_stream(deny_shell=True, leak=None):
    events = [
        {"type": "step_start", "part": {}},
        {"type": "text", "part": {"text": "Inspect then test."}},
        _tool_event("oracle_inspect", "completed", {"function_id": "synth:apply"}),
        _tool_event("oracle_test", "completed", {"function_id": "synth:apply"}),
    ]
    if deny_shell:
        events.append(_tool_event("shell", "error", {"command": "echo SHELLRUN"},
                                  error="Permission denied: shell"))
    if leak is not None:
        events.append({"type": "text", "part": {"text": leak}})
    return events


def _write_stub(root: Path, events, *, write_candidate=True, repair="reference.c",
                mcp_calls=None) -> Path:
    """A stand-in for the pinned binary: same argv surface, *real* MCP calls.

    The double does not fabricate trusted state. It launches exactly the MCP
    server the materialized configuration declares and performs a genuine
    ``tools/call`` for every oracle tool it reports, so the events it emits
    describe calls that really happened: real reservations in the ledger and real
    records on the trusted side. Only the pinned process's *other* behaviour
    (a denied direct tool, model text) is emulated from ``events``.
    """
    if mcp_calls is None:
        mcp_calls = [
            {"tool": "inspect", "arguments": {"function_id": "synth:apply"}},
            {"tool": "test", "arguments": {"function_id": "synth:apply",
                                           "candidate_path": "candidate.c"}},
        ]
    (root / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (root / "mcp_calls.json").write_text(json.dumps(mcp_calls), encoding="utf-8")
    (root / "write_candidate.json").write_text(json.dumps(write_candidate), encoding="utf-8")
    (root / "repair.c").write_text((SLICE_DIR / repair).read_text(), encoding="utf-8")
    (root / "mcp_stub_client.py").write_text(_MCP_CLIENT_SOURCE, encoding="utf-8")
    stub = root / "opencode-stub"
    stub.write_text(f"#!{sys.executable}\n" + _STUB_SOURCE, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _adapter(tmp: Path, stub: Path, **overrides) -> OpenCodeAdapter:
    base = tmp / "staging"
    params = dict(
        run_id="run-e2e",
        run_dir=base / "run-e2e",
        workspace=base / "run-e2e" / "workspace",
        repo_root=REPO_ROOT,
        python=Path(sys.executable),
        binary=stub,
        provider="opencode",
        model_id="deepseek-v4.1-flash-free",
        variant="high",
    )
    params.update(overrides)
    return OpenCodeAdapter(OpenCodeRun(**params))


def _run(tmp: Path, adapter, **overrides) -> controller.RunResult:
    params = dict(
        run_id="run-e2e",
        candidate_src=SLICE_DIR / "target.c",
        base_dir=tmp / "staging",
        budget_calls=60,
        max_attempts=1,
        wall_seconds=120.0,
        model={"provider": "opencode", "id": "deepseek-v4.1-flash-free", "variant": "high"},
    )
    params.update(overrides)
    return controller.run(RunConfig(**params), adapter)


class MaterializeTests(unittest.TestCase):
    def test_substitutes_every_token(self):
        self.assertEqual(materialize("@A@/@B@", {"@A@": "x", "@B@": "y"}), "x/y")

    def test_unused_token_is_refused(self):
        with self.assertRaises(opencode.BindingError):
            materialize("no tokens", {"@A@": "x"})

    def test_surviving_placeholder_is_refused(self):
        with self.assertRaises(opencode.BindingError):
            materialize("@A@ @LEFTOVER@", {"@A@": "x"})

    def test_tracked_template_renders_valid_json_with_pinned_restrictions(self):
        template = (SLICE_DIR / "opencode_config" / opencode.CONFIG_TEMPLATE).read_text()
        rendered = materialize(template, {
            "@MODEL@": "opencode/deepseek-v4.1-flash-free#high",
            "@PYTHON@": "/usr/bin/python3",
            "@REPO_ROOT@": "/repo",
            "@RUN_DIR@": "/staging/run",
            "@WORKSPACE@": "/staging/run/workspace",
        })
        config = json.loads(rendered)
        self.assertFalse(config["codemode"])
        self.assertFalse(config["autoupdate"])
        server = config["mcp"]["servers"]["oracle"]
        self.assertEqual(server["type"], "local")
        self.assertFalse(server["codemode"])
        self.assertEqual(server["command"][1:4], ["-m", "oracle.slice.bridge", "serve"])
        self.assertEqual(server["environment"]["PYTHONPATH"], "/repo")
        for action in ("shell", "subagent", "webfetch", "websearch", "browser", "question"):
            self.assertEqual(config["permission"][action], "deny")
        self.assertEqual(config["permission"]["external_directory"], {"*": "deny"})


class ChildEnvironmentTests(unittest.TestCase):
    """The child must not inherit the host repository as its project root.

    ``PWD`` decides OpenCode's project directory, and ``subprocess`` does not
    update it. An inherited ``PWD`` pointing at the host repository makes the
    repository's own files - including the held-out fixtures - *internal* to the
    agent's file tools, where ``external_directory`` cannot deny them. The child
    environment therefore pins ``PWD`` to the workspace.
    """

    def test_pwd_is_pinned_to_the_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream())
            adapter = _adapter(root, stub)
            with mock.patch.dict(os.environ, {"PWD": "/some/other/repository"}):
                env = adapter._child_env()
            self.assertEqual(env["PWD"], str(adapter.run.workspace))
            self.assertNotEqual(env["PWD"], "/some/other/repository")

    def test_isolation_roots_and_config_are_outside_the_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream())
            adapter = _adapter(root, stub)
            env = adapter._child_env()
            workspace = str(adapter.run.workspace)
            for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
                         "OPENCODE_CONFIG"):
                self.assertFalse(env[name].startswith(workspace), name)
                self.assertTrue(env[name].startswith(str(adapter.run.run_dir)), name)


class MaterializedServerTests(unittest.TestCase):
    """The materialized config's MCP server block must actually work.

    Reading ``opencode.json`` and launching exactly the command and environment it
    declares is the only way to know the agent will have working tools: the
    interpreter, the ``PYTHONPATH`` and the workspace flag all have to line up.
    Model-free and deterministic - the same call an agent would make.
    """

    def _session(self, server, workspace, requests, want):
        proc = subprocess.Popen(server["command"], cwd=str(workspace),
                                env={**os.environ, **server["environment"]},
                                text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        try:
            for request in requests:
                proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
            got = {}
            deadline = time.time() + 240
            while time.time() < deadline and len(got) < want:
                line = proc.stdout.readline()
                if not line:
                    break
                message = json.loads(line)
                if isinstance(message, dict) and "id" in message:
                    got[message["id"]] = message
            return got
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                proc.kill()
                proc.wait(timeout=10)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    stream.close()
                except OSError:  # pragma: no cover - defensive
                    pass

    def test_declared_server_lists_the_five_tools_and_tests_the_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream())
            adapter = _adapter(root, stub)
            _run(root, adapter, wall_seconds=600.0)

            config = json.loads((adapter.run.run_dir / "opencode.json").read_text())
            server = config["mcp"]["servers"]["oracle"]
            self.assertEqual(server["command"][0], sys.executable)
            self.assertIn("oracle.slice.bridge", server["command"])
            self.assertEqual(server["environment"]["PYTHONPATH"], str(REPO_ROOT))

            got = self._session(server, adapter.run.workspace, [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2024-11-05", "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "test", "arguments": {"function_id": "synth:apply",
                                                          "candidate_path": "candidate.c"}}},
            ], want=3)

            tools = got[2]["result"]["tools"]
            self.assertEqual([t["name"] for t in tools],
                             ["inspect", "fixtures", "coverage", "test", "diff"])
            test_schema = tools[[t["name"] for t in tools].index("test")]["inputSchema"]
            self.assertEqual(test_schema["required"], ["function_id", "candidate_path"])

            result = got[3]["result"]
            self.assertFalse(result.get("isError"))
            payload = json.loads(result["content"][0]["text"])
            # The stub repaired the candidate, so the visible suite must pass.
            self.assertEqual(payload["status"], "PASS")
            self.assertEqual(payload["total"], 12)
            self.assertEqual(payload["passed"], 12)


class EventStreamTests(unittest.TestCase):
    def test_malformed_line_fails_closed(self):
        with self.assertRaises(opencode.BindingError):
            opencode.parse_events('{"type": "text"}\nnot json\n')

    def test_empty_and_non_object_streams_are_refused(self):
        with self.assertRaises(opencode.BindingError):
            opencode.parse_events("\n\n")
        with self.assertRaises(opencode.BindingError):
            opencode.parse_events("[1, 2, 3]\n")

    def test_summary_records_calls_and_normalizes_denials(self):
        events = opencode.parse_events("\n".join(json.dumps(e) for e in _completed_stream()))
        summary = opencode.summarize(events, run_id="run-e2e", stream_sha256="a" * 64,
                                     stream_bytes=10)
        names = {c["tool"] for c in summary["oracle_tool_calls"]}
        self.assertEqual(names, {"oracle_inspect", "oracle_test"})
        self.assertEqual(summary["denials"], [{
            "action": "shell", "resource": "echo SHELLRUN", "decision": "deny",
            "reason": "Permission denied: shell",
        }])

    def test_a_refusal_is_not_a_denial(self):
        events = [{"type": "text", "part": {"text": "I will not call shell."}},
                  _tool_event("read", "error", {"filePath": "/etc/hosts"},
                              error="File not found")]
        summary = opencode.summarize(events, run_id="r", stream_sha256="a" * 64, stream_bytes=1)
        self.assertEqual(summary["denials"], [])

    def test_an_absent_tool_produces_no_denial(self):
        summary = opencode.summarize([{"type": "text", "part": {"text": "no shell tool"}}],
                                     run_id="r", stream_sha256="a" * 64, stream_bytes=1)
        self.assertEqual(summary["denials"], [])
        self.assertEqual(summary["oracle_tool_calls"], [])


class LeakScanTests(unittest.TestCase):
    def test_fingerprints_include_the_prefix_and_every_held_out_fixture(self):
        fingerprints = opencode.held_out_fingerprints()
        self.assertIn(opencode.HELD_OUT_PREFIX, fingerprints)
        for fixture in corpus.load_corpus(corpus.HELD_OUT):
            self.assertIn(fixture.fixture_id, fingerprints)

    def test_a_held_out_id_in_the_stream_is_conclusive(self):
        leaks = opencode.scan_for_leaks('{"text": "see hid-003"}', ["hid-", "hid-003"])
        self.assertEqual(leaks, ["hid-", "hid-003"])


class OpenCodeEndToEndTests(unittest.TestCase):
    def test_a_real_repair_through_the_adapter_is_certified_and_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream())
            adapter = _adapter(root, stub)
            result = _run(root, adapter)

            self.assertEqual(result.status, STATUS_CERTIFIED)
            payload = result.receipt["payload"]
            binding = payload["toolchain"]["opencode"]
            self.assertEqual(binding["binary"]["version"], "opencode v9.9.9-stub")
            self.assertEqual(binding["binary"]["name"], "opencode-stub")
            self.assertEqual(len(binding["binary"]["sha256"]), 64)
            self.assertEqual(binding["model"],
                             {"provider": "opencode", "id": "deepseek-v4.1-flash-free",
                              "variant": "high"})
            # The receipt is self-consistent with the summary it publishes.
            summary = adapter.sanitized_summary()
            self.assertEqual(binding["events"]["summary_sha256"],
                             opencode._sha256(canonical_bytes(summary)))
            self.assertEqual(binding["events"]["raw_sha256"], summary["stream"]["sha256"])
            self.assertEqual(binding["events"]["raw_bytes"], summary["stream"]["bytes"])
            self.assertGreater(binding["events"]["raw_bytes"], 0)
            self.assertEqual(binding["events"]["tool_calls"], 2)
            self.assertEqual(binding["events"]["denials"][0]["action"], "shell")
            # The model identity the controller recorded agrees with the binding.
            self.assertEqual(payload["agent"]["model"], binding["model"])
            receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])

            published = root / "agent.events.json"
            adapter.write_sanitized_summary(published)
            self.assertEqual(json.loads(published.read_text()), summary)

    def test_held_out_material_in_the_stream_prevents_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream(leak="expected hid-000 2147483647"))
            result = _run(root, _adapter(root, stub))
            payload = result.receipt["payload"]
            # The candidate itself verified; only acceptance is withheld.
            self.assertTrue(payload["verdict"]["candidate_verified"])
            self.assertEqual(result.status, STATUS_ABORTED)
            self.assertEqual(payload["toolchain"].get("opencode"), None)
            self.assertIn("held-out material", payload["execution"]["finalization"]["error"])
            receipts.verify(result.receipt)

    def test_a_production_capped_stream_prevents_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream())
            adapter = _adapter(root, stub)
            result = _run(root, adapter)
            stream = adapter._streams[0]
            # Simulate an event stream that filled the OS-enforced production cap:
            # the capture can no longer be trusted to be complete.
            stream.write_bytes(b"x" * harness.MAX_OUTPUT_BYTES)
            with self.assertRaises(opencode.BindingError):
                adapter.receipt_metadata()

    def test_probe_mode_is_non_accepting_and_spends_no_holdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream(), write_candidate=False)
            adapter = _adapter(root, stub, mode="probe",
                               prompt_path=SLICE_DIR / "opencode_config" / "pilot.prompt.txt")
            result = _run(root, adapter)
            payload = result.receipt["payload"]
            self.assertNotEqual(result.status, STATUS_CERTIFIED)
            self.assertEqual(payload["verdict"]["held_out"], None)
            self.assertEqual(payload["execution"]["abort_reason"], "gave_up")
            self.assertEqual(payload["verdict"]["certification"],
                             "skipped: agent aborted: gave_up")

    def test_a_mismatched_pinned_binary_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream())
            result = _run(root, _adapter(root, stub, expected_binary_sha256="0" * 64))
            payload = result.receipt["payload"]
            self.assertEqual(result.status, STATUS_ABORTED)
            # A binding fault is a trusted-host configuration failure, not an
            # agent crash; the attempt note names the cause and the run cannot be
            # accepted while the adapter was never prepared.
            self.assertIn("binary digest does not match", payload["execution"]["attempts"][0]["note"])
            self.assertIn("never prepared",
                          payload["execution"]["finalization"]["error"])
            self.assertNotIn("opencode", payload["toolchain"])
            receipts.verify(result.receipt)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
