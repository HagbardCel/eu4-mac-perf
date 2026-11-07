"""python3 -m automation.cli run|resume|status|report <campaign.json>"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from automation.opencode import OpenCodeAgent
from automation.runner import Runner
from automation.state import load_campaign, load_state, repo_root
from automation.telemetry import report


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("run", "resume", "status", "report"):
        print(
            "usage: python3 -m automation.cli run|resume|status|report <campaign.json>",
            file=sys.stderr,
        )
        return 2
    command, campaign_path = args
    campaign = load_campaign(Path(campaign_path))
    run_root = repo_root(campaign.root) / ".local" / "automation" / campaign.id
    if command == "status":
        _print_status(load_state(run_root / "state.json"))
        return 0
    if command == "report":
        _print_report(report(run_root / "events.jsonl"))
        return 0
    agent = OpenCodeAgent(
        os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096"),
        password=os.environ.get("OPENCODE_PASSWORD"),
        timeout=float(os.environ.get("OPENCODE_TIMEOUT", "120")),
        model=campaign.model,
    )
    runner = Runner(campaign, agent, run_root)
    try:
        state = runner.execute(resume_only=command == "resume")
    except KeyboardInterrupt:
        return 130
    if state["status"] == "complete":
        return 0
    print(state.get("pause_reason") or state["status"], file=sys.stderr)
    return 1


def _print_status(state: dict | None) -> None:
    if not state:
        print("status: absent")
        return
    print(f"status: {state['status']}")
    print(f"reason: {state.get('pause_reason') or '-'}")
    print(f"session: {state.get('session_id') or '-'}")
    print(f"rounds: {state.get('rounds')}")
    print(f"elapsed_seconds: {state.get('elapsed_seconds')}")
    print(f"verdict: {state.get('last_verdict') or '-'}")
    print(f"observation: {state.get('last_observation') or '-'}")
    print(f"model: {state.get('model') or '-'}")


def _print_report(summary: dict) -> None:
    print(f"elapsed_seconds: {summary['elapsed_seconds']}")
    print(f"rounds: {summary['rounds']}")
    print(f"tool_calls: {summary['tool_calls']}")
    print(f"tool_failures: {summary['tool_failures']}")
    print(f"validations: {summary['validations']}")
    print(f"validation_passes: {summary['validation_passes']}")
    print(f"files: {', '.join(summary['files']) or '-'}")
    print(f"tokens: {summary['tokens']}")
    print(f"cost: {summary['cost']}")


if __name__ == "__main__":
    raise SystemExit(main())
