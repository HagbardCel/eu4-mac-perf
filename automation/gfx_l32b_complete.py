"""Dynamic coordinator for autonomous L3.2b evidence discovery.

The coordinator is a deterministic state machine, not a reasoning agent. It
validates provisional checkpoints, snapshots candidates, chains contract
qualification and independent review, integrates accepted histories, and keeps
one workflow-level launch ledger. Real campaigns are run through the existing
OpenCode ``Runner``; the state machine is also testable with injected hooks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import launch_ledger  # noqa: E402
from automation.gfx_adversarial import cases_digest, validate_cases  # noqa: E402
from automation.gfx_checkpoint import (  # noqa: E402
    CheckpointError,
    source_tree_digest_at_commit,
    validate_checkpoint,
    validate_qualified_bundle,
)
from automation.review_result import load_review, validate_review  # noqa: E402
from automation.state import atomic_write, load_campaign, repo_root  # noqa: E402

SCHEMA = "eu4-l32b-completion/v1"
INTEGRATION_VERIFICATION_SCHEMA = "eu4-l32b-integration-verification/v1"
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
    integrate: Callable[..., dict]
    snapshot_contract: Callable[[Path, dict], dict] | None = None
    run_adversarial_cases: Callable[[dict, dict, Path, dict], dict] | None = None
    run_research: Callable[[dict], dict] | None = None
    run_remediation: Callable[[dict], dict] | None = None
    run_capture: Callable[[dict], dict] | None = None


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
        "campaign_sequence": 0,
        "active_campaign": None,
        "checkpoint": None,
        "candidate": None,
        "contract": None,
        "review": None,
        "integration": None,
        "review_target": None,
        "remediation_target": None,
        "promotion_kind": None,
        "contract_review": None,
        "candidate_review": None,
        "final_review": None,
        "trusted_contract": workflow.get("trusted_contract"),
        "oracle_manifest_sha256": workflow.get("oracle_manifest_sha256"),
        "contract_workspace": None,
        "contract_version": None,
        "contract_checkpoint": None,
        "contract_sha256": None,
        "adversarial_cases": None,
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


def _file_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _absorb_snapshot(state: dict, snapshot: Any) -> None:
    digest = snapshot.get("oracle_manifest_sha256") if isinstance(snapshot, dict) else None
    if isinstance(digest, str) and len(digest) == 64 \
            and all(char in "0123456789abcdef" for char in digest):
        state["oracle_manifest_sha256"] = digest


def _candidate_commit(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    commit = value.get("commit") or value.get("candidate_commit")
    return commit if isinstance(commit, str) and commit else None


def _git_blob_sha(repo: Path, commit: str, rel: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{rel}"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return hashlib.sha256(completed.stdout).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            return self._promote(state)
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
            if state["state"] in TERMINAL:
                break
            if state["state"] == before:
                state["state"] = "blocked"
                state["blocker"] = {"reason": "no_progress"}
                save_state(self.state_path, state)
                break
        else:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "step_limit"}
            save_state(self.state_path, state)
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
        trusted = state.get("trusted_contract")
        if isinstance(trusted, dict) and trusted.get("commit") == "PIN_AT_RUNTIME":
            completed = subprocess.run(
                ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0 or not completed.stdout.strip():
                state["state"] = "blocked"
                state["blocker"] = {"reason": "trusted_contract_pin_unresolved"}
                return state
            trusted["commit"] = completed.stdout.strip()
        if not state.get("oracle_manifest_sha256"):
            trusted = state.get("trusted_contract") or {}
            manifest = trusted.get("manifest")
            if isinstance(manifest, str) and manifest:
                path = Path(manifest)
                if not path.is_absolute():
                    path = (self.repo / path).resolve()
                if path.is_file():
                    state["oracle_manifest_sha256"] = _file_sha(path)
        _note(state, "preflight", ledger=state.get("ledger"))
        state["state"] = "investigating"
        return state

    def _begin_operation(self, state: dict, kind: str) -> dict:
        active = state.get("active_campaign")
        if isinstance(active, dict) and active.get("kind") == kind \
                and active.get("resumable"):
            return active
        sequence = int(state.get("campaign_sequence", 0)) + 1
        state["campaign_sequence"] = sequence
        active = {
            "operation_id": f"{kind}-{sequence}",
            "kind": kind,
            "resumable": True,
        }
        state["active_campaign"] = active
        save_state(self.state_path, state)
        return active

    def _implementer(self, state: dict) -> dict:
        self._begin_operation(state, "implementer")
        result = self.hooks.run_implementer(state)
        state["last_implementer"] = result
        checkpoint = result.get("checkpoint")
        if isinstance(checkpoint, str):
            trusted_commit = (state.get("trusted_contract") or {}).get("commit")
            if not trusted_commit:
                state["state"] = "blocked"
                state["blocker"] = {"reason": "trusted_contract_missing"}
                return state
            reported = result.get("trusted_contract_commit")
            if reported and reported != trusted_commit:
                state["state"] = "blocked"
                state["blocker"] = {"reason": "checkpoint_trusted_contract_mismatch"}
                return state
            errors = validate_checkpoint(
                Path(checkpoint),
                expected_campaign_id=result.get("campaign_id"),
                expected_candidate_base=result.get("candidate_base_commit"),
                expected_trusted_contract=trusted_commit,
            )
            if errors:
                state["state"] = "blocked"
                state["blocker"] = {
                    "reason": "checkpoint_invalid",
                    "errors": errors,
                }
                return state
            state["checkpoint"] = checkpoint
            state["campaign_dir"] = result.get("campaign_dir")
            try:
                snapshot = self.hooks.snapshot(Path(result["workspace"]), state)
                _absorb_snapshot(state, snapshot)
                state["candidate"] = snapshot
            except (OSError, RuntimeError, KeyError) as exc:
                state["state"] = "blocked"
                state["blocker"] = {"reason": "snapshot_failed", "detail": str(exc)}
                return state
            state["active_campaign"] = None
            _note(state, "checkpoint", checkpoint=checkpoint)
            state["state"] = "checkpoint_ready"
            return state
        verdict = result.get("last_verdict")
        status = result.get("status")
        if status == "complete" and verdict == "pass":
            state["campaign_dir"] = result.get("campaign_dir")
            try:
                snapshot = self.hooks.snapshot(Path(result["workspace"]), state)
                _absorb_snapshot(state, snapshot)
                state["candidate"] = snapshot
            except (OSError, RuntimeError, KeyError) as exc:
                state["state"] = "blocked"
                state["blocker"] = {"reason": "snapshot_failed", "detail": str(exc)}
                return state
            state["active_campaign"] = None
            required = set(self.workflow.get("required_inputs") or [])
            receipt = result.get("qualification_receipt")
            trusted = state.get("trusted_contract") or {}
            if not receipt or not Path(str(receipt)).is_file():
                state["state"] = "blocked"
                state["blocker"] = {"reason": "final_receipt_invalid",
                                    "detail": "qualification receipt is missing"}
                return state
            checker = _git_blob_sha(
                self.repo, str(trusted.get("commit")),
                "automation/gfx_draw_accept_v2.py",
            )
            errors = validate_qualified_bundle(
                Path(str(receipt)).parent,
                expected_candidate_digest=(snapshot or {}).get("candidate_digest"),
                expected_checker_sha256=checker,
                expected_trusted_commit=trusted.get("commit"),
                expected_fields=required,
            )
            if errors:
                state["state"] = "blocked"
                state["blocker"] = {"reason": "final_receipt_invalid",
                                    "errors": errors}
                return state
            if required <= set(result.get("established") or []):
                state["stage"] = "final"
            if state["stage"] == "final":
                state["review_target"] = "final"
                state["state"] = "final_review"
            else:
                state["review_target"] = "candidate"
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
        state["active_campaign"] = None
        state["state"] = "blocked"
        state["blocker"] = {
            "reason": result.get("pause_reason") or "implementer_incomplete",
            "detail": result.get("last_diagnostics"),
        }
        return state

    def _contract(self, state: dict) -> dict:
        self._begin_operation(state, "contract")
        result = self.hooks.run_contract(state["checkpoint"], state)
        state["last_contract"] = result
        if result.get("status") != "complete" or result.get("last_verdict") != "pass":
            state["state"] = "blocked"
            state["blocker"] = {
                "reason": "contract_campaign_incomplete",
                "detail": result.get("pause_reason") or result.get("last_diagnostics"),
            }
            return state
        workspace = result.get("workspace")
        if not workspace or self.hooks.snapshot_contract is None:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "contract_snapshot_unavailable"}
            return state
        state["contract_campaign_dir"] = result.get("campaign_dir")
        try:
            contract = self.hooks.snapshot_contract(Path(workspace), state)
            _absorb_snapshot(state, contract)
        except (OSError, RuntimeError, KeyError) as exc:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "contract_snapshot_failed", "detail": str(exc)}
            return state
        if not isinstance(contract, dict) or not _candidate_commit(contract):
            state["state"] = "blocked"
            state["blocker"] = {"reason": "contract_snapshot_incomplete"}
            return state
        state["contract"] = contract
        state["contract_workspace"] = str(workspace)
        state["contract_version"] = str(
            result.get("contract_version") or result.get("version") or ""
        )
        module = Path(workspace) / "automation" / "gfx_contracts" / (
            f"{state['contract_version']}.py"
        )
        if module.is_file():
            state["contract_sha256"] = hashlib.sha256(module.read_bytes()).hexdigest()
        else:
            state["contract_sha256"] = (
                result.get("contract_sha256") or contract.get("contract_sha256")
            )
        state["contract_checkpoint"] = result.get("checkpoint") or state["checkpoint"]
        state["active_campaign"] = None
        state["review_target"] = "contract"
        state["state"] = "qualifying_review"
        return state

    def _review(self, state: dict) -> dict:
        target_key = state.get("review_target") or (
            "contract" if state["state"] == "qualifying_review"
            else ("final" if state["state"] == "final_review" else "candidate")
        )
        target = state.get("candidate") if target_key in ("candidate", "final") \
            else state.get("contract")
        if not state.get("oracle_manifest_sha256"):
            state["state"] = "blocked"
            state["blocker"] = {"reason": "oracle_manifest_missing"}
            return state
        expected_candidate = _candidate_commit(target)
        if expected_candidate is None:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "review_target_has_no_candidate_commit"}
            return state
        result = self.hooks.run_reviewer(target, state)
        path = Path(str(result.get("path") or ""))
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
            "target": target_key,
        }
        if errors:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "invalid_review", "errors": errors}
            return state
        if outcome == "qualified":
            if target_key == "contract":
                qualification_errors = self._qualify_contract(state, path)
                if qualification_errors:
                    state["state"] = "blocked"
                    state["blocker"] = {
                        "reason": "adversarial_qualification_failed",
                        "errors": qualification_errors,
                    }
                    return state
                state["promotion_kind"] = "contract"
                state["state"] = "promote"
            elif target_key == "candidate":
                state["candidate_review"] = self._candidate_receipt(state, path)
                state["promotion_kind"] = "implementation"
                state["state"] = "promote"
            elif target_key == "final":
                final_errors = self._final_receipt(state, path)
                if final_errors:
                    state["state"] = "blocked"
                    state["blocker"] = {
                        "reason": "final_qualification_failed",
                        "errors": final_errors,
                    }
                    return state
                state["promotion_kind"] = "final"
                state["state"] = "promote"
            else:
                state["state"] = "blocked"
                state["blocker"] = {"reason": f"unknown_review_target_{target_key}"}
            return state
        if outcome == "rejected":
            state["remediation_target"] = target_key
            state["state"] = "remediating"
            return state
        if outcome == "unresolved":
            state["state"] = "researching"
            return state
        state["state"] = "blocked"
        state["blocker"] = {"reason": f"review_outcome_{outcome}"}
        return state

    def _qualify_contract(self, state: dict, report_path: Path) -> list[str]:
        cases_path = report_path.with_name("review-adversarial.json")
        if not cases_path.is_file():
            return ["reviewer-authored adversarial cases are missing"]
        checkpoint_path = Path(
            str(state.get("contract_checkpoint") or state.get("checkpoint") or "")
        )
        if not checkpoint_path.is_file():
            return ["checkpoint is missing"]
        checkpoint_sha = _file_sha(checkpoint_path)
        try:
            checkpoint_doc = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return [f"checkpoint is unreadable: {exc}"]
        required = {
            gap.get("name")
            for gap in checkpoint_doc.get("contract_gaps") or []
            if isinstance(gap, dict) and isinstance(gap.get("name"), str)
        }
        try:
            document = json.loads(cases_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return [f"adversarial cases are unreadable: {exc}"]
        errors = validate_cases(
            document,
            candidate_commit=str(_candidate_commit(state["contract"])),
            contract_version=str(state.get("contract_version") or ""),
            checkpoint_sha256=checkpoint_sha,
            required_fields=required,
        )
        if errors:
            return errors
        if self.hooks.run_adversarial_cases is None:
            return ["adversarial-case runner is unavailable"]
        result = self.hooks.run_adversarial_cases(
            checkpoint_path, state["contract"], cases_path, state
        )
        if not result.get("ok") or not result.get("positive_ok")                 or not result.get("negative_ok"):
            return [
                "adversarial qualification did not pass: "
                + json.dumps(result.get("cases") or result.get("errors") or [])[:2000]
            ]
        state["adversarial_cases"] = str(cases_path)
        state["adversarial_report"] = result
        state["contract_review"] = {
            "contract_commit": _candidate_commit(state["contract"]),
            "contract_version": state.get("contract_version"),
            "contract_sha256": state.get("contract_sha256"),
            "checkpoint_sha256": checkpoint_sha,
            "reviewer_report_sha256": _file_sha(report_path),
            "adversarial_cases_sha256": cases_digest(cases_path),
            "positive_ok": True,
            "negative_ok": True,
            "runner_report_sha256": _canonical_sha(result),
            "qualified_at": _now(),
        }
        return []

    def _candidate_receipt(self, state: dict, report_path: Path) -> dict:
        trusted = state.get("trusted_contract") or {}
        return {
            "candidate_commit": _candidate_commit(state["candidate"]),
            "reviewer_report_sha256": _file_sha(report_path),
            "trusted_contract_commit": trusted.get("commit"),
            "trusted_contract_sha256": trusted.get("sha256"),
            "qualified_at": _now(),
        }

    def _final_receipt(self, state: dict, report_path: Path) -> list[str]:
        implementer = state.get("last_implementer") or {}
        if implementer.get("last_verdict") != "pass":
            return ["final checker did not pass"]
        receipt = implementer.get("qualification_receipt")
        if not receipt or not Path(str(receipt)).is_file():
            return ["final qualification receipt is missing"]
        try:
            document = json.loads(Path(str(receipt)).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return [f"final qualification receipt is unreadable: {exc}"]
        if document.get("schema") != "eu4-gfx-qualification-receipt/v1":
            return ["final qualification receipt has the wrong schema"]
        trusted = state.get("trusted_contract") or {}
        if not trusted.get("commit") or not trusted.get("sha256"):
            return ["final trusted contract identity is missing"]
        if document.get("trusted_contract_commit") != trusted.get("commit"):
            return ["final receipt trusted contract differs"]
        if not report_path.is_file():
            return ["final reviewer report is missing"]
        checker = _git_blob_sha(
            self.repo, str(trusted.get("commit")),
            "automation/gfx_draw_accept_v2.py",
        )
        if checker is None or document.get("checker_sha256") != checker:
            return ["final receipt checker identity differs"]
        candidate = state.get("candidate") or {}
        candidate_digest = candidate.get("candidate_digest")
        if not candidate_digest and _candidate_commit(candidate) \
                and isinstance(candidate.get("changed_paths"), list):
            try:
                candidate_digest = source_tree_digest_at_commit(
                    self.repo,
                    str(_candidate_commit(candidate)),
                    list(candidate["changed_paths"]),
                )
            except CheckpointError as exc:
                return [f"final candidate digest cannot be recomputed: {exc}"]
        if not candidate_digest or document.get("candidate_digest") != candidate_digest:
            return ["final receipt candidate digest differs"]
        packet = document.get("packet") or {}
        if packet.get("packet_ready") is not True:
            return ["final receipt does not establish packet_ready"]
        fields = document.get("fields")
        if not isinstance(fields, list) or not fields:
            return ["final receipt has no field results"]
        if any(not isinstance(row, dict) or row.get("state") != "validated" for row in fields):
            return ["final receipt has unvalidated fields"]
        state["final_review"] = {
            "candidate_commit": _candidate_commit(state["candidate"]),
            "reviewer_report_sha256": _file_sha(report_path),
            "qualification_receipt_sha256": _file_sha(Path(str(receipt))),
            "checker_verdict": "pass",
            "qualified_at": _now(),
        }
        return []

    def _promote(self, state: dict) -> dict:
        kind = state.get("promotion_kind")
        errors = self._validate_promotion(state, kind)
        if errors:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "promotion_invalid", "errors": errors}
            return state
        if kind == "contract":
            contract = state["contract"]
        elif kind in ("implementation", "final"):
            contract = state.get("trusted_contract")
            if not isinstance(contract, dict) or not _candidate_commit(contract) \
                    or not contract.get("sha256"):
                state["state"] = "blocked"
                state["blocker"] = {"reason": "trusted_contract_missing"}
                return state
        else:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "unknown_promotion_kind"}
            return state
        result = self.hooks.integrate(
            state["candidate"], contract, state, kind=kind
        )
        state["integration"] = result
        if not result.get("ok"):
            state["state"] = "blocked"
            state["blocker"] = {
                "reason": "integration_failed",
                "detail": result.get("detail"),
            }
            return state
        verification = result.get("verification") or {}
        if verification.get("status") == "pending_capture" \
                and not result.get("published"):
            state["state"] = "capture_required"
            state["capture_requirement"] = verification
            return state
        verification_errors = self._validate_integration_verification(
            state, result, kind
        )
        if verification_errors:
            state["state"] = "blocked"
            state["blocker"] = {
                "reason": "integration_not_requalified",
                "errors": verification_errors,
            }
            return state
        state["integration_verification"] = result.get("verification")
        if kind == "implementation":
            state["integration_unresolved"] = list(
                result["verification"].get("unresolved_fields") or []
            )
        _note(state, "integrated", commit=result.get("commit"), kind=kind)
        if kind == "contract":
            state["trusted_contract"] = {
                "commit": result.get("commit"),
                "version": state.get("contract_version"),
                "sha256": state.get("contract_sha256"),
            }
            if isinstance(state.get("contract_review"), dict):
                state["contract_review"]["integration_commit"] = result.get("commit")
            state["state"] = "continuation"
        elif kind == "implementation":
            state["state"] = "continuation"
        elif kind == "final":
            state["state"] = "awaiting_merge"
        return state

    def _validate_integration_verification(
        self, state: dict, result: dict, kind: str | None
    ) -> list[str]:
        if not isinstance(result, dict):
            return ["integration result is not an object"]
        commit = result.get("commit")
        if not isinstance(commit, str) or not commit:
            return ["integration commit is missing"]
        verification = result.get("verification")
        if not isinstance(verification, dict):
            return ["integration verification is missing"]
        if verification.get("schema") != INTEGRATION_VERIFICATION_SCHEMA:
            return ["integration verification has the wrong schema"]
        if verification.get("commit") != commit:
            return ["integration verification is not bound to the integration commit"]
        if verification.get("kind") != kind:
            return ["integration verification kind differs from the promotion kind"]
        if verification.get("build_ok") is not True:
            return ["integrated build did not pass"]
        if verification.get("tests_ok") is not True:
            return ["integrated tests did not pass"]
        regressions = verification.get("regressions")
        if not isinstance(regressions, list) or regressions:
            return ["integrated tree regressed previously qualified evidence"]
        checker = verification.get("checker")
        if checker not in ("pass", "inconclusive", "not_run", "fail"):
            return ["integration checker verdict is invalid"]
        if checker == "fail":
            return ["integrated checker failed"]
        if not isinstance(verification.get("established_fields"), list) \
                or not isinstance(verification.get("unresolved_fields"), list):
            return ["integration verification field lists are missing"]
        if kind in ("contract", "implementation"):
            if checker not in ("pass", "inconclusive"):
                return ["integrated checker did not run for an intermediate promotion"]
            return []
        if kind == "final":
            if checker != "pass":
                return ["final integration checker did not pass"]
            if verification.get("packet_ready") is not True:
                return ["final integration did not establish packet_ready"]
            if verification.get("unresolved_fields"):
                return ["final integration still has unresolved fields"]
            implementer = state.get("last_implementer") or {}
            receipt = Path(str(implementer.get("qualification_receipt") or ""))
            if not receipt.is_file():
                return ["final qualification receipt is missing"]
            if verification.get("qualification_receipt_sha256") != _file_sha(receipt):
                return ["final integration receipt hash differs"]
            return []
        return ["unknown promotion kind"]

    def _validate_promotion(self, state: dict, kind: str | None) -> list[str]:
        if kind == "contract":
            receipt = state.get("contract_review")
            if not isinstance(receipt, dict):
                return ["contract qualification receipt is missing"]
            contract = state.get("contract") or {}
            if receipt.get("contract_commit") != _candidate_commit(contract):
                return ["contract receipt does not match the contract commit"]
            if receipt.get("contract_version") != state.get("contract_version"):
                return ["contract receipt version differs"]
            if receipt.get("contract_sha256") != state.get("contract_sha256"):
                return ["contract receipt hash differs"]
            checkpoint = Path(str(state.get("contract_checkpoint") or state.get("checkpoint") or ""))
            if not checkpoint.is_file() or receipt.get("checkpoint_sha256") != _file_sha(checkpoint):
                return ["contract receipt checkpoint is stale"]
            report = Path(str((state.get("review") or {}).get("path") or ""))
            if not report.is_file() or receipt.get("reviewer_report_sha256") != _file_sha(report):
                return ["contract reviewer report changed after qualification"]
            cases = Path(str(state.get("adversarial_cases") or ""))
            if not cases.is_file() or receipt.get("adversarial_cases_sha256") != cases_digest(cases):
                return ["adversarial cases changed after qualification"]
            if receipt.get("positive_ok") is not True or receipt.get("negative_ok") is not True:
                return ["adversarial qualification did not pass"]
            if receipt.get("runner_report_sha256") != _canonical_sha(
                state.get("adversarial_report") or {}
            ):
                return ["adversarial runner report changed after qualification"]
            module = f"automation/gfx_contracts/{state.get('contract_version')}.py"
            actual = _git_blob_sha(self.repo, str(_candidate_commit(contract)), module)
            if actual is None or actual != state.get("contract_sha256"):
                return ["contract module changed after qualification"]
            return []
        if kind == "implementation":
            receipt = state.get("candidate_review")
            if not isinstance(receipt, dict):
                return ["implementation review receipt is missing"]
            candidate = state.get("candidate") or {}
            if receipt.get("candidate_commit") != _candidate_commit(candidate):
                return ["implementation receipt does not match the candidate commit"]
            trusted = state.get("trusted_contract") or {}
            if not trusted.get("commit") or not trusted.get("sha256"):
                return ["trusted contract identity is missing"]
            if receipt.get("trusted_contract_commit") != trusted.get("commit") \
                    or receipt.get("trusted_contract_sha256") != trusted.get("sha256"):
                return ["trusted contract changed without requalification"]
            report = Path(str((state.get("review") or {}).get("path") or ""))
            if not report.is_file() or receipt.get("reviewer_report_sha256") != _file_sha(report):
                return ["implementation reviewer report changed after qualification"]
            return []
        if kind == "final":
            trusted = state.get("trusted_contract") or {}
            if not trusted.get("commit") or not trusted.get("sha256"):
                return ["final trusted contract identity is missing"]
            receipt = state.get("final_review")
            if not isinstance(receipt, dict):
                return ["final review receipt is missing"]
            candidate = state.get("candidate") or {}
            if receipt.get("candidate_commit") != _candidate_commit(candidate):
                return ["final receipt does not match the candidate commit"]
            if receipt.get("checker_verdict") != "pass":
                return ["final checker did not pass"]
            implementer = state.get("last_implementer") or {}
            qualification = Path(str(implementer.get("qualification_receipt") or ""))
            if not qualification.is_file()                     or receipt.get("qualification_receipt_sha256") != _file_sha(qualification):
                return ["final qualification receipt changed after review"]
            report = Path(str((state.get("review") or {}).get("path") or ""))
            if not report.is_file() or receipt.get("reviewer_report_sha256") != _file_sha(report):
                return ["final reviewer report changed after qualification"]
            return []
        return ["unknown promotion kind"]

    def _remediation(self, state: dict) -> dict:
        if self.hooks.run_remediation is None:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "remediation_unavailable"}
            return state
        self._begin_operation(state, "remediation")
        result = self.hooks.run_remediation(state)
        state["last_implementer"] = result
        if result.get("status") != "complete" or result.get("last_verdict") != "pass":
            state["state"] = "blocked"
            state["blocker"] = {
                "reason": "remediation_incomplete",
                "detail": result.get("last_diagnostics"),
            }
            return state
        workspace = result.get("workspace")
        if not workspace:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "remediation_workspace_missing"}
            return state
        target = state.get("remediation_target")
        state["active_campaign"] = None
        state["campaign_dir"] = result.get("campaign_dir")
        state["contract_campaign_dir"] = result.get("campaign_dir")
        try:
            if target == "contract":
                if self.hooks.snapshot_contract is None:
                    state["state"] = "blocked"
                    state["blocker"] = {"reason": "contract_snapshot_unavailable"}
                    return state
                contract = self.hooks.snapshot_contract(Path(workspace), state)
                _absorb_snapshot(state, contract)
                state["contract"] = contract
                version = result.get("contract_version") or state.get("contract_version")
                module = Path(workspace) / "automation" / "gfx_contracts" / (
                    f"{version}.py"
                )
                state["contract_sha256"] = (
                    hashlib.sha256(module.read_bytes()).hexdigest()
                    if module.is_file()
                    else (result.get("contract_sha256") or contract.get("contract_sha256"))
                )
                state["contract_version"] = (
                    result.get("contract_version") or state.get("contract_version")
                )
                state["contract_checkpoint"] = (
                    result.get("checkpoint") or state.get("contract_checkpoint")
                )
                state["review_target"] = "contract"
                state["state"] = "qualifying_review"
            elif target in ("candidate", "final"):
                snapshot = self.hooks.snapshot(Path(workspace), state)
                _absorb_snapshot(state, snapshot)
                state["candidate"] = snapshot
                state["review_target"] = target
                state["state"] = "reviewing" if target == "candidate" else "final_review"
            else:
                state["state"] = "blocked"
                state["blocker"] = {"reason": "remediation_target_missing"}
        except (OSError, RuntimeError, KeyError) as exc:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "remediation_snapshot_failed", "detail": str(exc)}
        return state

    def _capture_required(self, state: dict) -> dict:
        if self.hooks.run_capture is None:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "capture_hook_missing"}
            return state
        result = self.hooks.run_capture(state)
        receipt = result.get("capture_receipt")
        if not receipt:
            state["state"] = "blocked"
            state["blocker"] = {
                "reason": "capture_failed",
                "detail": result.get("detail") or "no capture receipt",
            }
            return state
        state["capture_receipt"] = receipt
        state["capture"] = result.get("capture")
        _note(state, "capture", receipt=receipt)
        return self._promote(state)

    def _research(self, state: dict) -> dict:
        if self.hooks.run_research is None:
            state["state"] = "blocked"
            state["blocker"] = {"reason": "research_unavailable"}
            return state
        self._begin_operation(state, "research")
        result = self.hooks.run_research(state)
        state["active_campaign"] = None
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
    from automation.gfx_l32b_hooks import L32bHooks

    hooks = L32bHooks(repo=repo, workflow=workflow, run_root=run_root)
    coordinator = Coordinator(
        repo=repo,
        workflow=workflow,
        state_path=state_path,
        hooks=hooks.hooks(),
    )
    state = coordinator.run()
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0 if state["state"] in ("awaiting_merge", "complete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
