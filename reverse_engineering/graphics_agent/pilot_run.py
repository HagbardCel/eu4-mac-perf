#!/usr/bin/env python3
"""Local pilot runner: fixture smoke, live Ghidra smoke, canaries, and agent investigation."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reverse_engineering.graphics_agent.model_budget import paid_model_requests_allowed
from reverse_engineering.graphics_agent.runner import run_investigation
from reverse_engineering.graphics_agent.tools import EvidenceService
from reverse_engineering.graphics_agent.verification import run_verification
from reverse_engineering.intervals import format_rva, parse_rva
from reverse_engineering.manifest import inventory_bundle_digest
from reverse_engineering.paths import ROOT

PILOT_DOC = ROOT / "analysis" / "graphics-reconstruction/evidence/agent-pilot-review.md"
GRAPH_MINI = ROOT / "tests" / "re_fixtures" / "graph_mini"
AGENT_QUESTION = (
    "Determine how indexed drawing reaches OpenGL and what graphics state must have been "
    "established beforehand. Submit one supported_inference with evidence IDs or document a blocker."
)
RECONSTRUCT_QUESTION = (
    "Reconstruct the indexed draw submission path: list key RVAs, cite evidence IDs, "
    "and note required GL state."
)


def _append_report(section: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    block = f"\n\n## Pilot run {stamp}\n\n{section}\n"
    PILOT_DOC.parent.mkdir(parents=True, exist_ok=True)
    if PILOT_DOC.is_file():
        PILOT_DOC.write_text(PILOT_DOC.read_text() + block)
    else:
        PILOT_DOC.write_text(block)


def _caller_rva_for_symbol(svc: EvidenceService, symbol: str) -> str | None:
    edges = svc.inventory_dir() / "call_edges.jsonl"
    if not edges.is_file():
        return None
    for line in edges.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("ultimate_external_symbol") == symbol or row.get("callee_external_symbol") == symbol:
            return format_rva(parse_rva(row["caller_rva"]))
    return None


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
    os.environ["ALLOW_MODEL_REQUESTS"] = "false"
    os.environ["EU4_AGENT_AUTHORIZE_PAID_RUN"] = "0"
    svc, tmp = _open_fixture_service(investigation_id)
    try:
        caller = _caller_rva_for_symbol(svc, "_glDrawElements") or "0x0000000000007000"
        path = svc.find_paths(caller, "ext:_glDrawElements")
        lines = [
            "- mode: `fixture-smoke`",
            f"- investigation: `{investigation_id}`",
            f"- caller_rva: `{caller}`",
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
        os.environ.pop("EU4_AGENT_FIXTURE_MODE", None)


def run_live_smoke(baseline: str, investigation_id: str) -> int:
    os.environ["EU4_AGENT_FIXTURE_MODE"] = "0"
    svc = EvidenceService.open(baseline, investigation_id)
    caller = _caller_rva_for_symbol(svc, "_glDrawElements")
    if not caller:
        print("error: could not resolve caller RVA for _glDrawElements from inventory", file=sys.stderr)
        return 1
    path = svc.find_paths(caller, "ext:_glDrawElements")
    lines = [
        "- mode: `live-smoke`",
        f"- baseline: `{baseline}`",
        f"- caller_rva: `{caller}`",
        f"- find_paths: `{path.get('found')}`",
    ]
    d1 = svc.get_disassembly(caller)
    d2 = svc.get_disassembly(caller)
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


def _pilot_config() -> dict[str, Any]:
    return json.loads((Path(__file__).parent / "config" / "pilot.json").read_text())


def _canary_spend_config() -> dict[str, Any]:
    """Single cumulative authorization shared by all provider canaries."""
    pilot = _pilot_config()
    ms = dict(pilot.get("model_spend", {}))
    auth = ms.get("canary_spend_authorization_id", "provider-canary-bundle")
    os.environ["EU4_AGENT_SPEND_AUTHORIZATION_ID"] = auth
    ms["spend_authorization_id"] = auth
    ms["investigation_budget_usd"] = ms.get("canary_budget_usd", 0.5)
    ms["per_run_cap_usd"] = ms.get("canary_per_run_cap_usd", 0.2)
    return {"model_spend": ms}


def _require_paid_canary_budget() -> None:
    if not paid_model_requests_allowed():
        print(
            "error: canaries require ALLOW_MODEL_REQUESTS!=false and EU4_AGENT_AUTHORIZE_PAID_RUN=1",
            file=sys.stderr,
        )
        raise SystemExit(1)
    os.environ.setdefault("EU4_AGENT_MAX_MODEL_REQUESTS", "2")
    _canary_spend_config()


def run_canary_provider(baseline: str, investigation_id: str) -> int:
    _require_paid_canary_budget()
    cfg = _canary_spend_config()
    os.environ["EU4_AGENT_MAX_MODEL_REQUESTS"] = "1"
    os.environ["EU4_AGENT_FIXTURE_MODE"] = "0"
    r = run_investigation(
        baseline,
        investigation_id,
        "discovery",
        "Reply with the word CANARY_OK only.",
        "canary-1",
        config=cfg,
    )
    _append_report(f"- canary: provider connection\n- result: `{r}`")
    print(json.dumps({"ok": True, "canary": 1, "result": r}, indent=2, default=str))
    return 0


def run_canary_tool_roundtrip(baseline: str, investigation_id: str) -> int:
    _require_paid_canary_budget()
    cfg = _canary_spend_config()
    os.environ["EU4_AGENT_MAX_MODEL_REQUESTS"] = "2"
    os.environ["EU4_AGENT_FIXTURE_MODE"] = "0"
    prompt = (
        "Call search_functions once with query 'draw' and summarize results in one sentence."
    )
    r = run_investigation(baseline, investigation_id, "discovery", prompt, "canary-2", config=cfg)
    _append_report(f"- canary: tool round-trip\n- result: `{r}`")
    print(json.dumps({"ok": True, "canary": 2, "result": r}, indent=2, default=str))
    return 0


def run_canary_structured_verify(baseline: str, investigation_id: str) -> int:
    _require_paid_canary_budget()
    _canary_spend_config()
    os.environ["EU4_AGENT_FIXTURE_MODE"] = "0"
    svc = EvidenceService.open(baseline, investigation_id)
    caller = _caller_rva_for_symbol(svc, "_glDrawElements") or "0x0000000000007000"
    path = svc.find_paths(caller, "ext:_glDrawElements")
    if not path.get("evidence_ids"):
        print("error: canary 3 needs resolvable path evidence", file=sys.stderr)
        return 1
    claim_id = "canary-verify-claim"
    svc.submit_findings(
        {
            "claim_id": claim_id,
            "statement": "Indexed draw reaches OpenGL",
            "evidence_ids": path["evidence_ids"],
            "scientific_status": "supported_inference",
        }
    )
    ver = run_verification(svc.research_conn, investigation_id, claim_id, service=svc)
    _append_report(f"- canary: structured verify\n- result: `{ver}`")
    print(json.dumps({"ok": True, "canary": 3, "verification": ver}, indent=2, default=str))
    return 0


def run_reconstruct_pilot(baseline: str, investigation_id: str, session: str) -> int:
    if not paid_model_requests_allowed():
        print("error: reconstruct pilot requires paid authorization", file=sys.stderr)
        return 1
    os.environ["EU4_AGENT_FIXTURE_MODE"] = "0"
    r = run_investigation(
        baseline,
        investigation_id,
        "reconstruct",
        RECONSTRUCT_QUESTION,
        session,
        config=_pilot_config(),
    )
    _append_report(f"- mode: reconstruct pilot\n- result keys: `{list(r.keys())}`")
    print(json.dumps({"ok": True, "mode": "reconstruct", "result": r}, indent=2, default=str))
    return 0


def run_agent(baseline: str, investigation_id: str, session: str) -> int:
    if not paid_model_requests_allowed():
        print("error: agent pilot requires EU4_AGENT_AUTHORIZE_PAID_RUN=1", file=sys.stderr)
        return 1
    os.environ["EU4_AGENT_FIXTURE_MODE"] = "0"
    max_tools = int(os.environ.get("EU4_AGENT_MAX_TOOL_CALLS", "24"))
    os.environ["EU4_AGENT_MAX_TOOL_CALLS"] = str(max_tools)
    pilot_cfg = _pilot_config()
    r1 = run_investigation(
        baseline, investigation_id, "discovery", AGENT_QUESTION, session, config=pilot_cfg
    )
    r2 = run_investigation(
        baseline,
        investigation_id,
        "discovery",
        "Continue: cite evidence IDs, add one follow-up tool query with justification, or document blocker.",
        session,
        config=pilot_cfg,
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
    p.add_argument("--baseline-revision", default="rev", help="Required for live modes")
    p.add_argument("--investigation-id", default="pilot")
    p.add_argument("--session", default="pilot-agent-session")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixture-smoke", action="store_true", help="CI-safe fixture graph/evidence/verify")
    mode.add_argument("--live-smoke", action="store_true", help="Real corpus + Ghidra dedup smoke")
    mode.add_argument("--agent", action="store_true", help="Real model investigation (local merge gate)")
    mode.add_argument("--canary-provider", action="store_true", help="Paid canary 1: provider connection")
    mode.add_argument("--canary-tools", action="store_true", help="Paid canary 2: tool round-trip")
    mode.add_argument("--canary-verify", action="store_true", help="Paid canary 3: structured verification")
    mode.add_argument("--reconstruct", action="store_true", help="Budget-limited reconstruction pilot")
    mode.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.smoke or args.fixture_smoke:
        return run_fixture_smoke(args.investigation_id)
    if args.live_smoke:
        return run_live_smoke(args.baseline_revision, args.investigation_id)
    if args.canary_provider:
        return run_canary_provider(args.baseline_revision, args.investigation_id)
    if args.canary_tools:
        return run_canary_tool_roundtrip(args.baseline_revision, args.investigation_id)
    if args.canary_verify:
        return run_canary_structured_verify(args.baseline_revision, args.investigation_id)
    if args.reconstruct:
        return run_reconstruct_pilot(args.baseline_revision, args.investigation_id, args.session)
    return run_agent(args.baseline_revision, args.investigation_id, args.session)


if __name__ == "__main__":
    raise SystemExit(main())
