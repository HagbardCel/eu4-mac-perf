#!/usr/bin/env python3
"""Local pilot runner: fixture smoke, live Ghidra smoke, and agent investigation."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from reverse_engineering.graphics_agent.runner import run_investigation
from reverse_engineering.graphics_agent.tools import EvidenceService
from reverse_engineering.graphics_agent.verification import run_verification
from reverse_engineering.manifest import inventory_bundle_digest
from reverse_engineering.paths import ROOT

PILOT_DOC = ROOT / "analysis" / "graphics-reconstruction/evidence/agent-pilot-review.md"
GRAPH_MINI = ROOT / "tests" / "re_fixtures" / "graph_mini"
AGENT_QUESTION = (
    "Determine how indexed drawing reaches OpenGL and what graphics state must have been "
    "established beforehand. Submit one supported_inference with evidence IDs or document a blocker."
)


def _append_report(section: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    block = f"\n\n## Pilot run {stamp}\n\n{section}\n"
    PILOT_DOC.parent.mkdir(parents=True, exist_ok=True)
    if PILOT_DOC.is_file():
        PILOT_DOC.write_text(PILOT_DOC.read_text() + block)
    else:
        PILOT_DOC.write_text(block)


def _open_fixture_service(investigation_id: str) -> tuple[EvidenceService, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    inv_src = GRAPH_MINI / "inventory"
    exp = Path(tmp.name) / "exports" / "rev"
    inv = exp / "inventory"
    inv.mkdir(parents=True)
    for name in ("functions.jsonl", "call_edges.jsonl", "coverage.json"):
        (inv / name).write_text((inv_src / name).read_text())
    digest = inventory_bundle_digest(inv_src)
    (exp / "provenance.json").write_text(
        json.dumps({"inventory_bundle_digest": digest, "baseline_revision": "rev"}) + "\n"
    )
    from reverse_engineering import index_db

    index_db.rebuild_index(exp / "index.sqlite", inv, exp / "decompiled", manifest={"batches": {}})
    (exp / "agent").mkdir(parents=True)
    os.environ["EU4_RE_EXPORT"] = tmp.name
    os.environ["EU4_AGENT_FIXTURE_MODE"] = "1"
    svc = EvidenceService.open("rev", investigation_id)
    return svc, tmp


def run_fixture_smoke(investigation_id: str) -> int:
    svc, tmp = _open_fixture_service(investigation_id)
    try:
        path = svc.find_paths("0x5000", "ext:_glDrawElements")
        lines = [
            "- mode: `fixture-smoke`",
            f"- investigation: `{investigation_id}`",
            f"- find_paths found: `{path.get('found')}`",
            f"- evidence_ids: `{path.get('evidence_ids')}`",
        ]
        if path.get("evidence_ids"):
            claim_id = "pilot-fixture-claim"
            sub = svc.submit_findings(
                {
                    "claim_id": claim_id,
                    "statement": "Draw path reaches OpenGL",
                    "evidence_ids": path["evidence_ids"],
                    "scientific_status": "supported_inference",
                }
            )
            ver = run_verification(svc.research_conn, investigation_id, claim_id, service=svc)
            lines.append(f"- submit: `{sub}`")
            lines.append(f"- verify: `{ver}`")
        _append_report("\n".join(lines))
        print(json.dumps({"ok": True, "mode": "fixture-smoke"}, indent=2))
        return 0
    finally:
        tmp.cleanup()


def run_live_smoke(baseline: str, investigation_id: str) -> int:
    os.environ.setdefault("EU4_AGENT_FIXTURE_MODE", "0")
    svc = EvidenceService.open(baseline, investigation_id)
    path = svc.find_paths("0x5000", "ext:_glDrawElements")
    lines = [
        "- mode: `live-smoke`",
        f"- baseline: `{baseline}`",
        f"- find_paths: `{path.get('found')}`",
    ]
    rva = "0x5000"
    d1 = svc.get_disassembly(rva)
    d2 = svc.get_disassembly(rva)
    lines.append(f"- disassembly pass1 keys: `{list(d1.keys())}`")
    lines.append(f"- disassembly pass2 keys: `{list(d2.keys())}`")
    if path.get("evidence_ids"):
        claim_id = "pilot-live-claim"
        sub = svc.submit_findings(
            {
                "claim_id": claim_id,
                "statement": "Draw path reaches OpenGL",
                "evidence_ids": path["evidence_ids"],
                "scientific_status": "supported_inference",
            }
        )
        ver = run_verification(svc.research_conn, investigation_id, claim_id, service=svc)
        lines.append(f"- verify: `{ver}`")
        lines.append(f"- submit: `{sub}`")
    _append_report("\n".join(lines))
    print(json.dumps({"ok": True, "mode": "live-smoke"}, indent=2))
    return 0


def run_agent(baseline: str, investigation_id: str, session: str) -> int:
    if os.environ.get("ALLOW_MODEL_REQUESTS", "true").lower() == "false":
        print("error: agent pilot requires real model (unset ALLOW_MODEL_REQUESTS)", file=sys.stderr)
        return 1
    max_tools = int(os.environ.get("EU4_AGENT_MAX_TOOL_CALLS", "24"))
    os.environ["EU4_AGENT_MAX_TOOL_CALLS"] = str(max_tools)
    r1 = run_investigation(baseline, investigation_id, "discovery", AGENT_QUESTION, session)
    r2 = run_investigation(
        baseline,
        investigation_id,
        "discovery",
        "Continue: cite evidence IDs, add one follow-up tool query with justification, or document blocker.",
        session,
    )
    _append_report(
        "\n".join(
            [
                "- mode: `agent`",
                f"- baseline: `{baseline}`",
                f"- session: `{session}`",
                f"- max_tool_calls: `{max_tools}`",
                f"- model: `{r1.get('model', 'unknown')}`",
                f"- turn1 keys: `{list(r1.keys())}`",
                f"- turn2 keys: `{list(r2.keys())}`",
            ]
        )
    )
    print(json.dumps({"ok": True, "mode": "agent", "turn1": r1, "turn2": r2}, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pilot_run")
    p.add_argument("--baseline-revision", default="rev", help="Required for --live-smoke and --agent")
    p.add_argument("--investigation-id", default="pilot")
    p.add_argument("--session", default="pilot-agent-session")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixture-smoke", action="store_true", help="CI-safe fixture graph/evidence/verify")
    mode.add_argument("--live-smoke", action="store_true", help="Real corpus + Ghidra dedup smoke")
    mode.add_argument("--agent", action="store_true", help="Real model investigation (local merge gate)")
    mode.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.smoke:
        return run_fixture_smoke(args.investigation_id)
    if args.fixture_smoke:
        return run_fixture_smoke(args.investigation_id)
    if args.live_smoke:
        return run_live_smoke(args.baseline_revision, args.investigation_id)
    return run_agent(args.baseline_revision, args.investigation_id, args.session)


if __name__ == "__main__":
    raise SystemExit(main())
