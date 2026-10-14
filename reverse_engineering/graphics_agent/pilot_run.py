#!/usr/bin/env python3
"""Local pilot runner: deterministic smoke and agent-driven investigation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from reverse_engineering.graphics_agent.runner import run_investigation
from reverse_engineering.graphics_agent.tools import EvidenceService
from reverse_engineering.graphics_agent.verification import run_verification
from reverse_engineering.paths import ROOT

PILOT_DOC = ROOT / "analysis" / "graphics-reconstruction/evidence/agent-pilot-review.md"
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


def run_smoke(baseline: str, investigation_id: str) -> int:
    os.environ.setdefault("EU4_AGENT_FIXTURE_MODE", "0")
    svc = EvidenceService.open(baseline, investigation_id)
    path = svc.find_paths("0x5000", "ext:_glDrawElements")
    lines = [
        f"- baseline: `{baseline}`",
        f"- investigation: `{investigation_id}`",
        f"- find_paths found: `{path.get('found')}`",
        f"- evidence_ids: `{path.get('evidence_ids')}`",
    ]
    if path.get("evidence_ids"):
        claim_id = "pilot-smoke-claim"
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
    print(json.dumps({"ok": True, "mode": "smoke"}, indent=2))
    return 0


def run_agent(baseline: str, investigation_id: str, session: str) -> int:
    if os.environ.get("ALLOW_MODEL_REQUESTS", "true").lower() == "false":
        print("error: agent pilot requires real model (unset ALLOW_MODEL_REQUESTS)", file=sys.stderr)
        return 1
    r1 = run_investigation(baseline, investigation_id, "discovery", AGENT_QUESTION, session)
    r2 = run_investigation(
        baseline,
        investigation_id,
        "discovery",
        "Continue: refine claim or document blocker with evidence IDs.",
        session,
    )
    _append_report(
        "\n".join(
            [
                f"- mode: agent",
                f"- session: `{session}`",
                f"- turn1 keys: `{list(r1.keys())}`",
                f"- turn2 keys: `{list(r2.keys())}`",
                f"- model: `{r1.get('model', 'unknown')}`",
            ]
        )
    )
    print(json.dumps({"ok": True, "mode": "agent", "turn1": r1, "turn2": r2}, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pilot_run")
    p.add_argument("--baseline-revision", required=True)
    p.add_argument("--investigation-id", default="pilot")
    p.add_argument("--session", default="pilot-agent-session")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--agent", action="store_true")
    args = p.parse_args(argv)
    if args.smoke:
        return run_smoke(args.baseline_revision, args.investigation_id)
    return run_agent(args.baseline_revision, args.investigation_id, args.session)


if __name__ == "__main__":
    raise SystemExit(main())
