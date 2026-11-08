"""Dynamic coordinator for autonomous L3.2b evidence discovery.

The coordinator is a deterministic state machine, not a reasoning agent. It
validates provisional checkpoints, snapshots candidates, chains contract
qualification and independent review, integrates accepted histories, and keeps
one workflow-level launch ledger. Real campaigns are run through the existing
OpenCode ``Runner``; the state machine is also testable with injected hooks.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import launch_ledger  # noqa: E402
from automation.gfx_checkpoint import validate_checkpoint  # noqa: E402
from automation.review_result import load_review, validate_review  # noqa: E402
from automation.state import atomic_write, load_campaign, repo_root  # noqa: E402

SCHEMA = "eu4-l32b-completion/v1"
TERMINAL = frozenset({"awaiting_merge", "blocked", "complete"})


class WorkflowError(RuntimeError):
    pass


@dataclass
class Hooks:
    """Injected operations; the real CLI supplies Runner-based implementations."""

    run_implementer: Callable[[dict], dict]
    snapshot: Callable[[Path, dict], dict]
    run_contract: Callable[[dict, dict], dict]
    run_reviewer: Callable[[dict, dict], dict]
    integrate: Callable[[dict, dict, dict], dict]
    run_research: Callable[[dict], dict] | None = None
    run_remediation: Callable[[dict], dict] | None = None


def load_workflow(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise WorkflowError("workflow is not an object")
    for key in ("schema", "workflow_id", "baseline_commit"):
        if key not in value:
            raise WorkflowError(f"workflow is missing {key}")
    return value


def default_state(workflow: dict) -> dict:
    return {
        "schema": SCHEMA,
        "workflow_id": str(workflow["workflow_id"]),
        "baseline_commit": str(workflow["baseline_commit"]),
        "integration_branch": workflow.get("integration_branch"),
        "state": "preflight",
        "stage": str(workflow.get("initial_stage", "investigation")),
        "round": 0,
        "checkpoint": None,
        "candidate": None,
        "contract": None,
        "review": None,
        "integration": None,
        "last_implementer": None,
        "last_contract": None,
        "blocker": None,
        "history": [],
        "ledger": None,
    }


def load_state(path: Path) -> dict | None:
    if not Path(path).is_file():
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def _note(state: dict, event: str, **data: Any) -> None:
    state.setdefault("history", []).append({"event": event, **data})


def _checkpoint_identity(checkpoint: dict) -> dict:
    return {
        "path": checkpoint.get("path"),
        "sha256": checkpoint.get("sha256"),
        "bundle": checkpoint.get("bundle"),
        "bundle_manifest_sha256": checkpoint.get("bundle_manifest_sha256"),
    }


def _review_outcome(
    report_path: Path,
    *,
    expected_candidate: str | None = None,
    expected_oracle: str | None = None,
) -> tuple[str, list[str]]:
    try:
        review = load_review(report_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return "unavailable", [f"review report unreadable: {exc}"]
    errors = validate_review(
        review,
        expected_candidate_commit=expected_candidate,
        expected_oracle_manifest_sha256=expected_oracle,
    )
    if errors:
        return "unavailable", errors
    return str(review.get("outcome")), []


class Coordinator:
    def __init__(self, *, repo: Path, workflow: dict, state_path: Path, hooks: Hooks):
        self.repo = Path(repo).resolve()
        self.workflow = workflow
        self.state_path = Path(state_path)
        self.hooks = hooks
        self.max_steps = int(workflow.get("max_steps", 40))

    def load_or_init(self) -> dict:
        state = load_state(self.state_path)
        if state is not None:
            return state
        state = default_state(self.workflow)
        save_state(self.state_path, state)
        return state

    def step(self, state: dict) -> dict:
        current = state["state"]
        if current in TERMINAL:
            return state
        if current == "preflight":
            return self._preflight(state)
        if current in ("investigating", "continuation"):
            return self._implementer(state)
        if current == "checkpoint_ready":
            return self._contract(state)
        if current in ("qualifying_review", "reviewing", "final_review"):
            return self._review(state)
        if current == "promote":
            return self._integrate(state)
        if current == "remediating":
            return self._remediation(state)
        if current == "researching":
            return self._research(state)
        if current == "final_capture":
            return self._implementer(state)
        raise WorkflowError(f"unknown coordinator state: {current}")

    def run(self, state: dict | None = None) -> dict:
        state = state or self.load_or_init()
        for _ in range(self.max_steps):
            if state["state"] in TERMINAL:
                break
            before = state["state"]
            state = self.step(state)
            save_state(self.state_path, state)
            if state["state"] == before:
                break
        return state

    def _preflight(self, state: dict) -> dict:
        ledger = self.workflow.get("launch_ledger")
        if isinstance(ledger, dict):
            path = Path(str(ledger["path"]))
            if not path.is_absolute():
                path = (self.repo / path).resolve()
            limit = int(ledger.get("limit", 6))
            launch_ledger.create_ledger(
                path,
                workflow_id=state["workflow_id"],
                limit=limit,
                deadline_seconds=ledger.get("deadline_seconds"),
            )
            state["ledger"] = {
                "path": str(path),
                "limit": limit,
                "used": len(launch_ledger.load_ledger(path)["launches"]),
            }
        _note(state, "preflight", ledger=state.get("ledger"))
        state["state"] = "investigating"
        return state

    def _implementer(self, state: dict) -> dict:
        result = self.hooks.run_implementer(state)
        state["last_implementer"] = result
        checkpoint = result.get("checkpoint")
        if isinstance(checkpoint, str):
            errors = validate_checkpoint(
                Path(checkpoint),
                expected_campaign_id=result.get("campaign_id"),
                expected_candidate_base=result.get("candidate_base_commit"),
                expected_trusted_contract=result.get("trusted_contract_commit"),
            )
            if errors:
                state["state"] = "blocked"
                state["blocker"] = {
                    "reason": "checkpoint_invalid",
                    "errors": errors,
                }
                return state
            state["checkpoint"] = checkpoint
            try:
                state["candidate"] = self.hooks.snapshot(
                    Path(result["workspace"]), state
                )
            except (OSError, RuntimeError, KeyError) as exc:
                state["state"] = "blocked"
                state["blocker"] = {"reason": "snapshot_failed", "detail": str(exc)}
                return state
            _note(state, "checkpoint", checkpoint=checkpoint)
            state["state"] = "checkpoint_ready"
            return state
        verdict = result.get("last_verdict")
        status = result.get("status")
        if status == "complete" and verdict == "pass":
            try:
                state["candidate"] = self.hooks.snapshot(
                    Path(result["workspace"]), state
                )
            except (OSError, RuntimeError, KeyError) as exc:
                state["state"] = "blocked"
                state["blocker"] = {"reason": "snapshot_failed", "detail": str(exc)}
                return state
            if state["stage"] == "final":
                state["state"] = "final_review"
            else:
                state["state"] = "reviewing"
            return state
        if status == "paused" and result.get("pause_reason") in (
            "round_limit", "time_limit", "blocked_on_permission",
        ):
            state["state"] = "blocked"
            state["blocker"] = {
                "reason": result.get("pause_reason"),
                "detail": result.get("last_diagnostics"),
            }
            return state
        state["state"] = "blocked"
        state["blocker"] = {
            "reason": result.get("pause_reason") or "implementer_incomplete",
            "detail": result.get("last_diagnostics"),
        }
        return state

    def _contract(self, state: dict) -> dict:
        result = self.hooks.run_contract(state["checkpoint"], state)
        state["last_contract"] = result
        if result.get("status") != "complete":
            state["state"] = "blocked"
            state["blocker"] = {
                "reason": "contract_campaign_incomplete",
                "detail": result.get("pause_reason"),
            }
            return state
        state["contract"] = result.get("candidate") or result
        state["state"] = "qualifying_review"
        return state

    def _review(self, state: dict) -> dict:
        target = state["contract"] if state["state"] == "qualifying_review" else state["candidate"]
        result = self.hooks.run_reviewer(target, state)
        path = Path(str(result.get("path") or ""))
        expected_candidate = (
            target.get("commit") if isinstance(target, dict) else None
        )
        outcome, errors = _review_outcome(
            path,
            expected_candidate=expected_candidate,
            expected_oracle=state.get("oracle_manifest_sha256"),
        )
        state["review"] = {
            "path": str(path),
            "outcome": outcome,
            "errors": errors,
            "session_id": result.get("session_id"),
        }
        if errors:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "invalid_review", "errors": errors}
            return state
        if outcome == "qualified":
            if state["state"] == "qualifying_review":
                state["state"] = "promote"
            elif state["state"] == "final_review":
                state["state"] = "awaiting_merge"
            else:
                state["state"] = "promote"
            return state
        if outcome == "rejected":
            state["state"] = "remediating"
            return state
        if outcome == "unresolved":
            state["state"] = "researching"
            return state
        state["state"] = "blocked"
        state["blocker"] = {"reason": f"review_outcome_{outcome}"}
        return state

    def _integrate(self, state: dict) -> dict:
        result = self.hooks.integrate(state["candidate"], state["contract"], state)
        state["integration"] = result
        if not result.get("ok"):
            state["state"] = "blocked"
            state["blocker"] = {
                "reason": "integration_failed",
                "detail": result.get("detail"),
            }
            return state
        _note(state, "integrated", commit=result.get("commit"))
        state["state"] = "continuation"
        return state

    def _remediation(self, state: dict) -> dict:
        if self.hooks.run_remediation is None:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "remediation_unavailable"}
            return state
        result = self.hooks.run_remediation(state)
        state["last_implementer"] = result
        if result.get("status") == "complete":
            state["state"] = "reviewing"
        else:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "remediation_incomplete"}
        return state

    def _research(self, state: dict) -> dict:
        if self.hooks.run_research is None:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "research_unavailable"}
            return state
        result = self.hooks.run_research(state)
        state["last_implementer"] = result
        state["state"] = "investigating" if result.get("status") == "complete" else "blocked"
        return state


def prepare_campaign_run_root(
    run_root: Path,
    *,
    workflow_id: str,
    ledger_path: Path,
    limit: int,
) -> Path:
    """Create the run root and its workflow-ledger pointer before a campaign."""
    run_root = Path(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    return launch_ledger.write_pointer(
        run_root,
        workflow_id=workflow_id,
        ledger_path=Path(ledger_path),
        limit=limit,
    )


def _load_workflow(path: Path) -> dict:
    return load_workflow(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status", "report"))
    parser.add_argument("--workflow", required=True)
    args = parser.parse_args(argv)
    workflow_path = Path(args.workflow).resolve()
    workflow = _load_workflow(workflow_path)
    repo = repo_root(workflow_path)
    run_root = repo / ".local" / "automation" / str(workflow["workflow_id"])
    state_path = run_root / "workflow.json"
    if args.command == "status":
        state = load_state(state_path)
        print(json.dumps(state or {"state": "absent"}, indent=2, sort_keys=True))
        return 0
    if args.command == "report":
        state = load_state(state_path)
        print(json.dumps(state or {"state": "absent"}, indent=2, sort_keys=True))
        return 0
    print(
        "The real Runner-backed hooks are configured by the completion campaign; "
        "this bootstrap CLI validates the workflow state machine and ledger.",
        file=sys.stderr,
    )
    state = load_state(state_path) or default_state(workflow)
    save_state(state_path, state)
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
