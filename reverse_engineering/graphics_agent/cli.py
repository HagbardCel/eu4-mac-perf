#!/usr/bin/env python3
"""CLI for the graphics reconstruction agent harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from reverse_engineering.graphics_agent.context_builder import prepare_package
from reverse_engineering.graphics_agent.research_store import connect, export_events_jsonl
from reverse_engineering.graphics_agent.runner import run_investigation
from reverse_engineering.graphics_agent.tools import EvidenceService
from reverse_engineering.paths import ReError, binary_sha256, local_binary_root, resolve_export_dir


def _require_agent_runtime() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit(
            "graphics_agent CLI requires Python 3.11+ (uv sync --extra graphics-agent)"
        )


def _load_json_config(name: str) -> dict:
    path = Path(__file__).resolve().parent / "config" / name
    return json.loads(path.read_text())


def cmd_prepare(args: argparse.Namespace) -> int:
    try:
        agent_dir = prepare_package(args.baseline_revision, allow_digest_mismatch=args.allow_digest_mismatch)
        print(json.dumps({"agent_dir": str(agent_dir)}, indent=2))
        return 0
    except (ReError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_doctor(args: argparse.Namespace) -> int:
    try:
        digest = binary_sha256()
        binary_root = local_binary_root(digest)
        exp = resolve_export_dir(binary_root, args.baseline_revision)
        inv = exp / "inventory"
        idx = exp / "index.sqlite"
        agent = exp / "agent"
        issues = []
        if not inv.is_dir():
            issues.append("missing inventory")
        if not idx.is_file():
            issues.append("missing index.sqlite — run corpus index")
        ctx = agent / "graphics-agent-context.json"
        if not ctx.is_file():
            issues.append("missing agent context — run prepare")
        ghidra_meta = binary_root / "ghidra_project.json"
        if not ghidra_meta.is_file():
            issues.append("missing ghidra_project.json")
        print(json.dumps({"ok": not issues, "issues": issues, "export_dir": str(exp)}, indent=2))
        return 0 if not issues else 1
    except ReError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_run(args: argparse.Namespace) -> int:
    pilot = _load_json_config("pilot.json")
    inv_id = pilot.get("investigation_id", "pilot")
    result = run_investigation(
        args.baseline_revision,
        inv_id,
        args.mode,
        args.prompt,
        args.session or "default",
        config=pilot,
        use_test_model=args.test_model,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    digest = binary_sha256()
    exp = resolve_export_dir(local_binary_root(digest), args.baseline_revision)
    conn = connect(exp / "agent" / "research.sqlite")
    inv = args.investigation_id or "default-mcp"
    row = conn.execute("SELECT * FROM investigations WHERE id = ?", (inv,)).fetchone()
    print(json.dumps(dict(row) if row else {"error": "not_found"}, indent=2, default=str))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    args.test_model = False
    return cmd_run(args)


def cmd_report(args: argparse.Namespace) -> int:
    digest = binary_sha256()
    exp = resolve_export_dir(local_binary_root(digest), args.baseline_revision)
    service = EvidenceService.open(args.baseline_revision, args.investigation_id or "default-mcp")
    service.export_snapshots()
    if args.export_events:
        export_events_jsonl(connect(exp / "agent" / "research.sqlite"), exp / "agent" / "events.jsonl")
    print(json.dumps({"reports_dir": str(exp / "agent")}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    _require_agent_runtime()
    parser = argparse.ArgumentParser(prog="graphics_agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("--baseline-revision", required=True)
    p_prepare.add_argument("--allow-digest-mismatch", action="store_true")
    p_prepare.set_defaults(func=cmd_prepare)

    p_doctor = sub.add_parser("doctor")
    p_doctor.add_argument("--baseline-revision", required=True)
    p_doctor.set_defaults(func=cmd_doctor)

    p_run = sub.add_parser("run")
    p_run.add_argument("--baseline-revision", required=True)
    p_run.add_argument("--profile", default="pilot")
    p_run.add_argument("--mode", default="discovery", choices=["discovery", "reconstruct", "verify", "report"])
    p_run.add_argument("--prompt", default="Survey known Gfx draw boundary evidence.")
    p_run.add_argument("--session", default=None)
    p_run.add_argument("--test-model", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_resume = sub.add_parser("resume")
    p_resume.add_argument("--baseline-revision", required=True)
    p_resume.add_argument("--session", required=True)
    p_resume.add_argument("--mode", default="discovery")
    p_resume.add_argument("--prompt", default="Continue investigation.")
    p_resume.set_defaults(func=cmd_resume)

    p_status = sub.add_parser("status")
    p_status.add_argument("--baseline-revision", required=True)
    p_status.add_argument("--investigation-id", default=None)
    p_status.set_defaults(func=cmd_status)

    p_report = sub.add_parser("report")
    p_report.add_argument("--baseline-revision", required=True)
    p_report.add_argument("--investigation-id", default=None)
    p_report.add_argument("--export-events", action="store_true")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
