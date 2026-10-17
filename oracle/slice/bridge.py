"""Trusted Oracle bridge for the P3 slice: MCP server plus controller-only certify.

The bridge is the only Oracle surface the reconstruction agent may touch. Over
MCP it exposes exactly five read/diagnose tools on the **visible** corpus:

    inspect / fixtures / coverage / test / diff

OpenCode prefixes a tool with its server name, so a server named ``oracle``
yields the agent-visible ``oracle_inspect`` ... ``oracle_diff``.

Certification is deliberately **not** in the MCP catalog. ``certify_candidate``
is an internal function, reachable only through a privileged CLI guarded by the
controller's run token. It reads the held-out corpus, which the MCP tools never
see. The controller invokes it once, after freezing the candidate.

Each accepted MCP call reserves budget in the run ledger *before* executing, so
restarting the server cannot reset the counter.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import uuid
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable

from oracle.slice import compare, corpus, harness
from oracle.slice.budget import BudgetDenied, BudgetExceeded, RunBudgetLedger
from oracle.slice.corpus import Fixture

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "eu4-oracle-slice"
SERVER_VERSION = "0.3.0"
MAX_TOOL_CHARS = 64000

RUN_ID_FILE = "run-id.txt"
TOKEN_FILE = "controller.token"
TEST_DIR = "tests"
BUILD_DIR = "builds"

_FUNCTION_IDS = ("synth:apply",)

_STATIC_EVIDENCE: dict[str, dict[str, Any]] = {
    "synth:apply": {
        "function_id": "synth:apply",
        "signature": "int synth_apply(struct synth_state *s, int mode, int arg)",
        "abi": {
            "target": "x86-64 (portable C11, pinned semantics)",
            "note": "decompiler types are hypotheses; raw recorded bytes are authoritative",
        },
        "state_layout": {
            "struct": "synth_state",
            "bytes": harness.STATE_BYTES,
            "fields": [
                {"name": "dirty", "offset": 0, "width": 4, "type": "int"},
                {"name": "value", "offset": 4, "width": 4, "type": "int"},
                {"name": "counter", "offset": 8, "width": 4, "type": "int"},
            ],
        },
        "observable_domains": ["return", "memory", "effects"],
        "effect_sink": "synth_emit(field, value) records an ordered write event",
        "fields_enum": {"SYNTH_FIELD_DIRTY": 1, "SYNTH_FIELD_VALUE": 2, "SYNTH_FIELD_COUNTER": 3},
        "note": "Behaviour is evidenced by the visible fixtures; no expectations are supplied here.",
    }
}

_TOOL_SCHEMA_ID = {"type": "string", "minLength": 1, "maxLength": 128,
                   "pattern": r"^[A-Za-z0-9:_@.\-]+$"}

TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "inspect",
        "description": "Read verified static evidence and ABI/state layout for a function",
        "inputSchema": {"type": "object", "additionalProperties": False,
                        "properties": {"function_id": _TOOL_SCHEMA_ID}, "required": ["function_id"]},
    },
    {
        "name": "fixtures",
        "description": "List visible fixtures with input and capture-completeness metadata",
        "inputSchema": {"type": "object", "additionalProperties": False,
                        "properties": {"function_id": _TOOL_SCHEMA_ID,
                                       "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
                        "required": ["function_id"]},
    },
    {
        "name": "coverage",
        "description": "Read exact visible-fixture counts and observable domains",
        "inputSchema": {"type": "object", "additionalProperties": False,
                        "properties": {"function_id": _TOOL_SCHEMA_ID}, "required": ["function_id"]},
    },
    {
        "name": "test",
        "description": "Compile the candidate and run the visible fixtures; returns a test run id",
        "inputSchema": {"type": "object", "additionalProperties": False,
                        "properties": {"function_id": _TOOL_SCHEMA_ID,
                                       "candidate_path": {"type": "string", "minLength": 1, "maxLength": 256}},
                        "required": ["function_id", "candidate_path"]},
    },
    {
        "name": "diff",
        "description": "Read the first observable divergence from a visible test run",
        "inputSchema": {"type": "object", "additionalProperties": False,
                        "properties": {"run_id": {"type": "string", "minLength": 1, "maxLength": 128},
                                       "max_events": {"type": "integer", "minimum": 1, "maximum": 32}},
                        "required": ["run_id"]},
    },
)


class ToolError(RuntimeError):
    """A tool call that the agent may see rejected."""


# --- argument validation ------------------------------------------------------


def _function_id(args: dict[str, Any]) -> str:
    value = args.get("function_id")
    if value not in _FUNCTION_IDS:
        raise ToolError(f"unknown function_id: {value!r}")
    return value


def _arg_int(args: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"{key} must be an integer")
    if value < low or value > high:
        raise ToolError(f"{key} must be between {low} and {high}")
    return value


def _safe_token(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ToolError(f"{what} is required")
    if len(value) > 128 or not all(c.isalnum() or c in ":_-." for c in value):
        raise ToolError(f"{what} is malformed")
    return value


class Bridge:
    """Holds the run's trusted state and services the five agent-visible tools."""

    def __init__(self, *, run_dir: Path, workspace: Path, ledger: RunBudgetLedger,
                 visible_root: Path | None = None, timeout_s: float = harness.DEFAULT_TIMEOUT_S,
                 cc: str = "cc"):
        self.run_dir = Path(run_dir)
        self.workspace = Path(workspace).resolve()
        self.ledger = ledger
        self.run_id = ledger.run_id
        self.visible_root = Path(visible_root) if visible_root else corpus.FIXTURES_DIR
        self.timeout_s = timeout_s
        self.cc = cc
        self._visible_cache: list[Fixture] | None = None
        (self.run_dir / TEST_DIR).mkdir(parents=True, exist_ok=True)
        (self.run_dir / BUILD_DIR).mkdir(parents=True, exist_ok=True)

    # -- corpus ---------------------------------------------------------------
    def _visible(self) -> list[Fixture]:
        if self._visible_cache is None:
            self._visible_cache = corpus.load_corpus(corpus.VISIBLE, root=self.visible_root)
        return self._visible_cache

    def _for_function(self, function_id: str) -> list[Fixture]:
        return [f for f in self._visible() if f.function_id == function_id]

    # -- dispatch -------------------------------------------------------------
    def call(self, method: str, args: dict[str, Any] | None) -> dict[str, Any]:
        handler = _HANDLERS.get(method)
        if handler is None:
            raise ToolError(f"unknown tool: {method!r}")
        # Admission happens before execution, and the reservation stands even if
        # the tool then fails.
        try:
            self.ledger.reserve(1)
        except BudgetExceeded as exc:
            raise ToolError(f"tool-call budget exhausted ({exc.granted}/{exc.limit})") from exc
        except BudgetDenied as exc:
            raise ToolError(f"tool calls are denied: {exc}") from exc
        result = handler(self, args or {})
        if len(json.dumps(result)) > MAX_TOOL_CHARS:
            raise ToolError("tool response exceeded the model-facing size limit")
        return result

    # -- candidate path -------------------------------------------------------
    def _resolve_candidate(self, raw: Any) -> Path:
        if not isinstance(raw, str) or not raw:
            raise ToolError("candidate_path is required")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ToolError(f"candidate not found: {raw}") from exc
        if not resolved.is_relative_to(self.workspace):
            raise ToolError("candidate_path escapes the candidate workspace")
        if resolved.suffix != ".c":
            raise ToolError("candidate_path must name a .c file")
        return resolved

    # -- test records ---------------------------------------------------------
    def _test_path(self, test_run_id: str) -> Path:
        return self.run_dir / TEST_DIR / f"{test_run_id}.json"

    def _store_test(self, test_run_id: str, record: dict[str, Any]) -> None:
        self._test_path(test_run_id).write_text(json.dumps(record), encoding="utf-8")

    def _load_test(self, test_run_id: str) -> dict[str, Any]:
        path = self._test_path(test_run_id)
        if not path.is_file():
            raise ToolError(f"unknown run_id: {test_run_id!r}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive
            raise ToolError(f"cannot read test run: {exc}") from exc


# --- agent-visible tool implementations ---------------------------------------


def _tool_inspect(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(_STATIC_EVIDENCE[_function_id(args)]))


def _tool_fixtures(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
    function_id = _function_id(args)
    limit = _arg_int(args, "limit", 10, 1, 50)
    fixtures = bridge._for_function(function_id)
    if not fixtures:
        raise ToolError(f"no visible fixtures for {function_id!r}")
    shown = fixtures[:limit]
    return {
        "function_id": function_id,
        "total": len(fixtures),
        "returned": len(shown),
        "truncated": len(shown) < len(fixtures),
        "fixtures": [fixture.summary() for fixture in shown],
    }


def _tool_coverage(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
    function_id = _function_id(args)
    fixtures = bridge._for_function(function_id)
    by_mode = Counter(str(f.input["mode"]) for f in fixtures)
    return {
        "function_id": function_id,
        "visible_fixtures": len(fixtures),
        "modes": dict(sorted(by_mode.items())),
        "observable_domains": _STATIC_EVIDENCE[function_id]["observable_domains"],
        "held_out": "restricted",
    }


def _tool_test(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
    function_id = _function_id(args)
    fixtures = bridge._for_function(function_id)
    candidate = bridge._resolve_candidate(args.get("candidate_path"))
    test_run_id = f"{bridge.run_id}-{uuid.uuid4().hex[:12]}"
    source_hash = sha256(candidate.read_bytes()).hexdigest()
    try:
        build = harness.build(candidate, bridge.run_dir / BUILD_DIR / test_run_id, cc=bridge.cc)
    except harness.BuildError as exc:
        raise ToolError("candidate failed to compile: " + (exc.stderr or "")[-800:]) from exc
    diagnostics = [harness.evaluate(build, fixture, run_id=test_run_id, timeout_s=bridge.timeout_s)
                   for fixture in fixtures]
    summary = compare.verdict(diagnostics)
    bridge._store_test(test_run_id, {
        "test_run_id": test_run_id,
        "function_id": function_id,
        "candidate_sha256": source_hash,
        "summary": summary,
        "diagnostics": diagnostics,
        "toolchain": {"cc": build.cc, "flags": list(build.flags), "compiler_version": build.compiler_version},
    })
    return {
        "test_run_id": test_run_id,
        "status": summary["status"],
        "total": summary["total"],
        "passed": summary["passed"],
        "failed": summary["failures"],
    }


def _tool_diff(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
    test_run_id = _safe_token(args.get("run_id"), "run_id")
    max_events = _arg_int(args, "max_events", 8, 1, 32)
    record = bridge._load_test(test_run_id)
    failing = [d for d in record["diagnostics"] if d["status"] != compare.STATUS_PASS]
    if not failing:
        return {"test_run_id": test_run_id, "status": compare.STATUS_PASS, "first_divergence": None}
    first = failing[0]
    return {
        "test_run_id": test_run_id,
        "fixture_id": first["fixture_id"],
        "status": first["status"],
        "first_divergence": first["first_divergence"],
        "capture": first["capture"],
        "failing_fixtures": [d["fixture_id"] for d in failing[:max_events]],
        "note": first.get("note"),
    }


_HANDLERS: dict[str, Callable[[Bridge, dict[str, Any]], dict[str, Any]]] = {
    "inspect": _tool_inspect,
    "fixtures": _tool_fixtures,
    "coverage": _tool_coverage,
    "test": _tool_test,
    "diff": _tool_diff,
}


# --- controller-only certification --------------------------------------------


def token_path(run_dir: Path) -> Path:
    return Path(run_dir) / TOKEN_FILE


def write_controller_token(run_dir: Path) -> str:
    """Create the run token. Controller-only; mode 0600."""
    token = secrets.token_hex(32)
    path = token_path(run_dir)
    path.write_text(token, encoding="utf-8")
    os.chmod(path, 0o600)
    return token


def require_token(run_dir: Path, token: str | None) -> None:
    path = token_path(run_dir)
    if not path.is_file():
        raise ToolError("controller token is missing; certification is forbidden")
    expected = path.read_text(encoding="utf-8").strip()
    if not token or not secrets.compare_digest(token, expected):
        raise ToolError("invalid controller token; certification is forbidden")


def certify_candidate(candidate: Path, *, run_dir: Path, run_id: str, token: str | None,
                      timeout_s: float = harness.DEFAULT_TIMEOUT_S, cc: str = "cc",
                      fixtures_root: Path | None = None) -> dict[str, Any]:
    """Run the held-out corpus against a frozen candidate. Controller-only.

    Returns an outcome-only verdict: never expected bytes, never per-fixture
    expected values.
    """
    require_token(run_dir, token)
    candidate = Path(candidate)
    if not candidate.is_file() or candidate.is_symlink():
        raise ToolError(f"candidate snapshot is not a regular file: {candidate}")
    root = Path(fixtures_root) if fixtures_root else corpus.FIXTURES_DIR
    fixtures = corpus.load_corpus(corpus.HELD_OUT, root=root)
    snapshot_hash = sha256(candidate.read_bytes()).hexdigest()
    started = time.monotonic()
    build = harness.build(candidate, Path(run_dir) / "certify" / run_id, cc=cc)
    diagnostics = [harness.evaluate(build, fixture, run_id=run_id, timeout_s=timeout_s)
                   for fixture in fixtures]
    summary = compare.verdict(diagnostics)
    return {
        "schema_version": compare.DIAGNOSTIC_SCHEMA_VERSION,
        "run_id": run_id,
        "status": summary["status"],
        "total": summary["total"],
        "passed": summary["passed"],
        "failures": summary["failures"],
        "candidate_sha256": snapshot_hash,
        "corpus": corpus.manifest(fixtures, set_name=corpus.HELD_OUT),
        "toolchain": {"cc": build.cc, "flags": list(build.flags), "compiler_version": build.compiler_version},
        "duration_s": round(time.monotonic() - started, 6),
    }


# --- MCP protocol -------------------------------------------------------------


class MCPServer:
    """Minimal newline-delimited JSON-RPC 2.0 server implementing MCP tools."""

    def __init__(self, bridge: Bridge):
        self.bridge = bridge
        self.initialized = False

    def _tools_result(self) -> dict[str, Any]:
        return {"tools": [dict(definition) for definition in TOOL_DEFINITIONS]}

    def handle(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return _error(None, -32600, "invalid request")
        request_id = message.get("id")
        method = message.get("method")
        if request_id is None:
            return None  # notification
        if method == "initialize":
            params = message.get("params") or {}
            requested = params.get("protocolVersion") if isinstance(params, dict) else None
            self.initialized = True
            return _result(request_id, {
                "protocolVersion": requested or MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, self._tools_result())
        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name") if isinstance(params, dict) else None
            arguments = params.get("arguments") if isinstance(params, dict) else None
            try:
                result = self.bridge.call(name, arguments or {})
            except ToolError as exc:
                return _result(request_id, _tool_error(str(exc)))
            return _result(request_id, _tool_ok(result))
        return _error(request_id, -32601, f"method not found: {method!r}")


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_ok(result: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(result)}], "isError": False}


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps({"error": message})}], "isError": True}


def serve(bridge: Bridge, *, stdin: Iterable[str] | None = None, stdout: Any = None) -> int:
    server = MCPServer(bridge)
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response: dict[str, Any] | None = _error(None, -32700, "parse error")
        else:
            response = server.handle(message)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0


# --- CLI ----------------------------------------------------------------------


def _load_run_id(run_dir: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    path = Path(run_dir) / RUN_ID_FILE
    if not path.is_file():
        raise SystemExit(f"no run id given and {path} is missing")
    return path.read_text(encoding="utf-8").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oracle.slice.bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_parser = sub.add_parser("serve", help="run the agent-facing MCP stdio server")
    serve_parser.add_argument("--run-dir", required=True)
    serve_parser.add_argument("--workspace", required=True)
    serve_parser.add_argument("--run-id")
    serve_parser.add_argument("--timeout", type=float, default=harness.DEFAULT_TIMEOUT_S)
    serve_parser.add_argument("--cc", default="cc")

    certify_parser = sub.add_parser("certify", help="controller-only held-out certification")
    certify_parser.add_argument("--run-dir", required=True)
    certify_parser.add_argument("--candidate", required=True)
    certify_parser.add_argument("--run-id")
    certify_parser.add_argument("--token", default=os.environ.get("EU4_ORACLE_CONTROLLER_TOKEN"))
    certify_parser.add_argument("--timeout", type=float, default=harness.DEFAULT_TIMEOUT_S)
    certify_parser.add_argument("--cc", default="cc")

    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)
    run_id = _load_run_id(run_dir, args.run_id)

    if args.command == "serve":
        ledger = RunBudgetLedger.attach(run_dir / "budget.sqlite3", run_id)
        bridge = Bridge(run_dir=run_dir, workspace=Path(args.workspace), ledger=ledger,
                        timeout_s=args.timeout, cc=args.cc)
        return serve(bridge)

    verdict = certify_candidate(Path(args.candidate), run_dir=run_dir, run_id=run_id,
                                token=args.token, timeout_s=args.timeout, cc=args.cc)
    print(json.dumps(verdict))
    return 0 if verdict["status"] == compare.STATUS_PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
