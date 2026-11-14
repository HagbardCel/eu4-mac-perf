"""Stable JSON tool core for agents (CLI, DSH plugin, MCP, OpenCode).

Seven logical tools share one implementation:

``eu4_re_query``       reverse-engineering knowledge
``eu4_session``        owned session inspection and safe termination
``eu4_capture``        frame/health inspection and targeted capture requests
``eu4_diff_run``       run a dual-process differential campaign
``eu4_diff_inspect``   inspect the first discrepancy of a campaign
``eu4_replay``         run a stored local reproduction
``eu4_case_inspect``   summarize a diagnostic case package

Every invocation prints exactly one JSON object with a stable envelope.  Thin
agent adapters must call this module rather than reimplementing any backend
logic.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_case, gfx_diagnostics, gfx_dual_run, gfx_scenario  # noqa: E402
from automation import gfx_session, launch_lease, launch_ledger  # noqa: E402

SCHEMA = "eu4-agent-tool/v1"
TOOLS = (
    "eu4_re_query",
    "eu4_session",
    "eu4_capture",
    "eu4_diff_run",
    "eu4_diff_inspect",
    "eu4_replay",
    "eu4_case_inspect",
)


def _envelope(tool: str, status: str, result=None, *, reason: str = "",
              exit_code: int | None = None) -> dict:
    value = {
        "schema": SCHEMA,
        "tool": tool,
        "ok": status in ("ok", "pass", "completed", "stopped", "partial"),
        "status": status,
        "reason": reason,
        "result": result if result is not None else {},
    }
    if exit_code is not None:
        value["exit_code"] = exit_code
    return value


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: not an object")
    return value


def _run_module(module: str, args: list[str], *, timeout: float = 600.0) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=timeout,
    )
    text = completed.stdout.strip()
    if not text:
        raise RuntimeError(completed.stderr.strip() or "tool produced no output")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"tool output is not JSON: {exc}") from exc
    if isinstance(value, dict):
        return value
    return {"result": value}


# ---------------------------------------------------------------------------
# Tool implementations


def eu4_re_query(args: argparse.Namespace) -> dict:
    from reverse_engineering import re_query

    if args.re_op == "provenance":
        value = re_query.query_provenance(index=args.index)
    elif args.re_op == "struct":
        value = re_query.query_struct(args.target, limit=args.limit)
    elif args.re_op == "resolve":
        value = re_query.query_resolve(args.target, index=args.index, limit=args.limit)
    elif args.re_op == "function":
        value = re_query.query_function(args.target, index=args.index,
                                        max_bytes=args.max_bytes)
    elif args.re_op in ("callers", "callees"):
        value = re_query.query_edges(args.target, direction=args.re_op, index=args.index)
    elif args.re_op == "xrefs":
        value = re_query.query_xrefs(args.target, index=args.index)
    elif args.re_op == "abi":
        value = re_query.query_abi(args.target, index=args.index)
    else:
        raise ValueError(f"unknown re operation: {args.re_op}")
    status = value.get("status", "unavailable")
    return _envelope("eu4_re_query", status, value.get("result"),
                     reason=value.get("reason", ""))


def eu4_session(args: argparse.Namespace) -> dict:
    if args.session_op == "list":
        rows = gfx_session.Session.active_sessions()
        return _envelope("eu4_session", "ok", {"sessions": rows})
    if args.session_op == "lease":
        executable = Path(args.executable).expanduser() if args.executable else None
        if executable is None:
            from reverse_engineering import paths
            executable = paths.executable_path()
        return _envelope("eu4_session", "ok",
                         launch_lease.inspect(executable))
    run = Path(args.run).expanduser().resolve()
    if args.session_op == "inspect":
        return _envelope("eu4_session", "ok", gfx_session.Session.load(run))
    if args.session_op == "terminate":
        state = _read_json(run / "session.json")
        processes = state.get("processes") or []
        results = []
        for row in processes:
            identity = gfx_session.ProcessIdentity.from_dict(row)
            results.append(gfx_session.terminate_owned(
                identity, grace_seconds=args.grace).as_dict())
        terminal = run / "terminal-status.json"
        return _envelope("eu4_session", "ok", {
            "session_id": state.get("session_id"),
            "terminations": results,
        })
    raise ValueError(f"unknown session operation: {args.session_op}")


def eu4_capture(args: argparse.Namespace) -> dict:
    run = Path(args.run).expanduser().resolve()
    if args.capture_op == "health":
        summary = gfx_diagnostics.scan_run(run)
        gate = gfx_diagnostics.health_gate(
            summary, result_requires_pixels=bool(args.require_pixels))
        return _envelope("eu4_capture", "ok",
                         {"health": summary, "gate": gate})
    if args.capture_op == "frames":
        rows = []
        for backend in ("reference", "candidate"):
            path = run / backend / "observe" / "frame-observe.jsonl"
            if not path.is_file():
                path = run / "observe" / "frame-observe.jsonl"
            if not path.is_file():
                continue
            with path.open("r", encoding="utf-8") as source:
                for number, line in enumerate(source):
                    if number >= args.limit:
                        break
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(value, dict):
                        rows.append(value)
        return _envelope("eu4_capture", "ok", {"frames": rows, "count": len(rows)})
    if args.capture_op == "request":
        role_dir = run / args.role
        observe = role_dir / "observe"
        if not observe.is_dir():
            return _envelope("eu4_capture", "unavailable",
                             reason=f"no observation directory: {observe}")
        request = observe / "capture-request.json"
        payload = {"schema": "gfx-frame-request/v1",
                   "frames": [int(value) for value in args.frame]}
        request.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return _envelope("eu4_capture", "ok",
                         {"request": str(request), "frames": payload["frames"]})
    if args.capture_op == "probe":
        argv = ["automation.live_gfx", "probe", "--backend", "metal",
                "--checkpoint", args.checkpoint, "--occurrence", str(args.occurrence),
                "--output", args.output, "--campaign-run-root", args.campaign_run_root]
        if args.caller_rva:
            argv += ["--caller-rva", args.caller_rva]
        if args.shader_cache:
            argv += ["--shader-cache", args.shader_cache]
        if args.timeout:
            argv += ["--timeout", str(args.timeout)]
        value = _run_module(*argv, timeout=(args.timeout or 300) + 120)
        return _envelope("eu4_capture", "ok", value)
    raise ValueError(f"unknown capture operation: {args.capture_op}")


def eu4_diff_run(args: argparse.Namespace) -> dict:
    scenario = gfx_scenario.load(Path(args.scenario))
    result = gfx_dual_run.run_campaign(
        scenario,
        run_root=Path(args.run_root),
        ledger_path=Path(args.ledger),
        shader_cache=Path(args.shader_cache) if args.shader_cache else None,
        qualified_sources=(Path(args.qualified_sources) if args.qualified_sources
                           else ROOT / "analysis" / "gfx-shader" / "v0" / "qualified-sources.json"),
        mode=args.mode,
        max_frames=args.max_frames,
        profile=args.profile,
        staggered=bool(getattr(args, "staggered", False)),
        stagger_wait_seconds=float(getattr(args, "stagger_wait", 600.0)),
    )
    status = result.get("result", "incomplete")
    return _envelope("eu4_diff_run",
                     "completed" if status == "completed" else status,
                     {"run_dir": result.get("run_dir"),
                      "error": result.get("error"),
                      "stop": result.get("stop"),
                      "frames": (result.get("frames") or {}).get("paired"),
                      "comparison": (result.get("frames") or {}).get("comparison"),
                      "health": (result.get("health") or {}).get("verdict")})


def eu4_diff_inspect(args: argparse.Namespace) -> dict:
    run = Path(args.run).expanduser().resolve()
    result = _read_json(run / "result.json")
    frames = result.get("frames") or {}
    comparison = frames.get("comparison") or {}
    pair = comparison.get("first_failure") or frames.get("last_pair")
    package = None
    if args.build_case:
        package = gfx_case.build_case(run)
    return _envelope("eu4_diff_inspect", "ok", {
        "run_dir": str(run),
        "stop": result.get("stop"),
        "health": result.get("health"),
        "alignment": result.get("alignment"),
        "comparison": comparison,
        "first_pair": pair,
        "case": package,
        "next": (
            "inspect the case analysis and the first differing region; request a "
            "per-draw prefix before starting another campaign"
            if pair else "no discrepancy was recorded"
        ),
    })


def eu4_replay(args: argparse.Namespace) -> dict:
    if args.replay_op == "case":
        result = gfx_case.compare_case(Path(args.case))
        return _envelope("eu4_replay", result.get("status", "unavailable"), result)
    if args.replay_op == "frame":
        argv = ["automation.m6_frame", "replay", "--frame", args.frame,
                "--shaders", args.shaders, "--out", args.out]
        if args.only_event is not None:
            argv += ["--only-event", str(args.only_event)]
            argv += ["--capture-pre", "--capture-post"]
        value = _run_module(*argv, timeout=600)
        return _envelope("eu4_replay", value.get("status", "ok"), value)
    raise ValueError(f"unknown replay operation: {args.replay_op}")


def eu4_case_inspect(args: argparse.Namespace) -> dict:
    value = gfx_case.inspect_case(Path(args.case))
    return _envelope("eu4_case_inspect", "ok", value)


# ---------------------------------------------------------------------------
# CLI


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None,
                        help="also write the JSON envelope to this file")
    sub = parser.add_subparsers(dest="tool", required=True)

    re_query = sub.add_parser("eu4_re_query")
    re_query.add_argument("re_op", choices=(
        "resolve", "function", "callers", "callees", "decompile", "xrefs",
        "abi", "struct", "provenance"))
    re_query.add_argument("target", nargs="?", default=None)
    re_query.add_argument("--index", type=Path, default=None)
    re_query.add_argument("--limit", type=int, default=10)
    re_query.add_argument("--max-bytes", type=int, default=24000)

    session = sub.add_parser("eu4_session")
    session.add_argument("session_op", choices=("list", "inspect", "terminate", "lease"))
    session.add_argument("--executable", default=None)
    session.add_argument("--run", type=Path, default=None)
    session.add_argument("--grace", type=float, default=6.0)

    capture = sub.add_parser("eu4_capture")
    capture.add_argument("capture_op", choices=("health", "frames", "request", "probe"))
    capture.add_argument("--run", type=Path, default=None)
    capture.add_argument("--role", choices=("reference", "candidate"), default=None)
    capture.add_argument("--frame", action="append", type=int, default=[])
    capture.add_argument("--limit", type=int, default=100)
    capture.add_argument("--require-pixels", action="store_true")
    capture.add_argument("--checkpoint", default="after-present")
    capture.add_argument("--occurrence", type=int, default=1)
    capture.add_argument("--output", default=None)
    capture.add_argument("--campaign-run-root", default=None)
    capture.add_argument("--caller-rva", default=None)
    capture.add_argument("--shader-cache", default=None)
    capture.add_argument("--timeout", type=float, default=None)

    diff_run = sub.add_parser("eu4_diff_run")
    diff_run.add_argument("--scenario", required=True)
    diff_run.add_argument("--run-root", required=True)
    diff_run.add_argument("--ledger", required=True)
    diff_run.add_argument("--shader-cache", default=None)
    diff_run.add_argument("--qualified-sources", default=None)
    diff_run.add_argument("--mode", choices=("streaming", "lockstep"), default=None)
    diff_run.add_argument("--max-frames", type=int, default=None)
    diff_run.add_argument("--profile", choices=("early", "targeted", "qualification"),
                          default=None)
    diff_run.add_argument("--staggered", action="store_true")
    diff_run.add_argument("--stagger-wait", type=float, default=600.0)

    inspect = sub.add_parser("eu4_diff_inspect")
    inspect.add_argument("--run", required=True, type=Path)
    inspect.add_argument("--build-case", action="store_true")

    replay = sub.add_parser("eu4_replay")
    replay.add_argument("replay_op", choices=("case", "frame"))
    replay.add_argument("--case", type=Path, default=None)
    replay.add_argument("--frame", default=None)
    replay.add_argument("--shaders", default=None)
    replay.add_argument("--out", default=None)
    replay.add_argument("--only-event", type=int, default=None)

    case = sub.add_parser("eu4_case_inspect")
    case.add_argument("--case", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if getattr(args, "out", None) is None and argv:
        # ``argparse`` subparsers reset parent defaults in this layout; honour
        # ``--out`` in either position without duplicating it on every parser.
        for index, token in enumerate(argv):
            if token == "--out" and index + 1 < len(argv):
                args.out = Path(argv[index + 1])
                break
    implementations = {
        "eu4_re_query": eu4_re_query,
        "eu4_session": eu4_session,
        "eu4_capture": eu4_capture,
        "eu4_diff_run": eu4_diff_run,
        "eu4_diff_inspect": eu4_diff_inspect,
        "eu4_replay": eu4_replay,
        "eu4_case_inspect": eu4_case_inspect,
    }
    try:
        value = implementations[args.tool](args)
        code = 0 if value.get("ok") else 2
    except Exception as exc:  # noqa: BLE001 - one JSON envelope, always
        value = _envelope(args.tool, "error", reason=f"{type(exc).__name__}: {exc}")
        code = 3
    text = json.dumps(value, indent=2, sort_keys=False) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
