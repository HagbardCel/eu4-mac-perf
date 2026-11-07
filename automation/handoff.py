"""Idempotent outer qualification handoff controller (A0.2)."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Callable

from automation.oracle_promote import promote_grounded_findings
from automation.review_result import load_review, validate_review, write_review
from automation.state import atomic_write, campaign_lock, load_state, repo_root
from automation.verification_manifest import digest_manifest, load_manifest

STATES = (
    "implementing",
    "qualified_by_checker",
    "reviewing",
    "review_rejected",
    "remediating",
    "awaiting_merge",
    "blocked",
)

MAX_ROUNDS = 3


def default_state(workflow_id: str, implementer_campaign_id: str, pin: str) -> dict:
    return {
        "schema": "eu4-handoff/v1",
        "workflow_id": workflow_id,
        "state": "implementing",
        "round": 0,
        "max_rounds": MAX_ROUNDS,
        "implementer_campaign_id": implementer_campaign_id,
        "pin_commit": pin,
        "candidate_commit": None,
        "candidate_tree": None,
        "oracle_manifest_sha256": None,
        "oracle_version": None,
        "reviewer_campaign_id": None,
        "review_path": None,
        "remediation_campaign_id": None,
        "unresolved_findings": [],
        "history": [],
    }


def load_workflow(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_workflow(path: Path, state: dict) -> None:
    atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def _note(state: dict, event: str, **data) -> None:
    entry = {"event": event, **data}
    state.setdefault("history", []).append(entry)


def snapshot_candidate(
    workspace: Path,
    snapshot_dir: Path,
    *,
    message: str = "qualification candidate snapshot",
) -> dict:
    """Create an immutable git commit in a dedicated snapshot repo of the worktree files.

    Uses a fresh git repo under snapshot_dir so we do not require committing to the
    main repository. Returns candidate_commit and tree hashes.
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    git = ["git", "-C", str(snapshot_dir)]
    if not (snapshot_dir / ".git").exists():
        subprocess.run(["git", "init"], cwd=snapshot_dir, check=True, capture_output=True)
        subprocess.run(
            [*git, "config", "user.email", "qualification@local"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [*git, "config", "user.name", "qualification"],
            check=True,
            capture_output=True,
        )
    # Copy tracked-relevant files via rsync-like walk excluding .git
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace)
        if ".git" in rel.parts:
            continue
        dest = snapshot_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
    subprocess.run([*git, "add", "-A"], check=True, capture_output=True)
    # Idempotent: if nothing changed, reuse HEAD
    status = subprocess.run(
        [*git, "status", "--porcelain"], capture_output=True, text=True, check=True
    )
    if status.stdout.strip():
        subprocess.run(
            [*git, "commit", "-m", message],
            check=True,
            capture_output=True,
        )
    head = subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    tree = subprocess.run(
        [*git, "rev-parse", "HEAD^{tree}"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return {"candidate_commit": head, "candidate_tree": tree, "snapshot_dir": str(snapshot_dir)}


def verify_snapshot_unmodified(snapshot_dir: Path, expected_commit: str) -> list[str]:
    git = ["git", "-C", str(snapshot_dir)]
    head = subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    errors = []
    if head != expected_commit:
        errors.append(f"snapshot HEAD moved: {head} != {expected_commit}")
    dirty = subprocess.run(
        [*git, "status", "--porcelain"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if dirty:
        errors.append("snapshot working tree dirty after review")
    return errors


class HandoffController:
    """Drive implementer → snapshot → review → remediate without resuming complete campaigns."""

    def __init__(
        self,
        *,
        repo: Path,
        workflow_path: Path,
        run_implementer: Callable[[str], dict],
        run_reviewer: Callable[[dict], Path],
        oracle_dir: Path,
        max_rounds: int = MAX_ROUNDS,
        trusted_helpers: list[str] | None = None,
        checker_relpath: str = "campaigns/eu4-a01-trusted-validation/accept.py",
    ):
        self.repo = repo
        self.workflow_path = workflow_path
        self.run_implementer = run_implementer
        self.run_reviewer = run_reviewer
        self.oracle_dir = oracle_dir
        self.max_rounds = max_rounds
        self.trusted_helpers = trusted_helpers or [
            "automation/evidence.py",
            "automation/macho_map.py",
            "automation/verification_manifest.py",
            "automation/review_result.py",
            "automation/oracle_promote.py",
        ]
        self.checker_relpath = checker_relpath

    def load_or_init(self, implementer_campaign_id: str, pin: str) -> dict:
        if self.workflow_path.is_file():
            return load_workflow(self.workflow_path)
        state = default_state(
            workflow_id="wf_" + uuid.uuid4().hex,
            implementer_campaign_id=implementer_campaign_id,
            pin=pin,
        )
        save_workflow(self.workflow_path, state)
        return state

    def step(self, state: dict) -> dict:
        """Advance one transition. Idempotent for already-recorded artifacts."""
        with campaign_lock(self.workflow_path.with_suffix(".lock")):
            current = state["state"]
            if current == "implementing" or current == "remediating":
                return self._run_implementer(state)
            if current == "qualified_by_checker":
                return self._begin_review(state)
            if current == "reviewing":
                return self._finish_review(state)
            if current == "review_rejected":
                return self._begin_remediation(state)
            if current in ("awaiting_merge", "blocked"):
                return state
            raise RuntimeError(f"unknown handoff state: {current}")

    def _run_implementer(self, state: dict) -> dict:
        campaign_id = (
            state.get("remediation_campaign_id")
            if state["state"] == "remediating"
            else state["implementer_campaign_id"]
        )
        result = self.run_implementer(campaign_id)
        if result.get("status") != "complete" or result.get("last_verdict") != "pass":
            state["last_implementer"] = {
                "campaign_id": campaign_id,
                "status": result.get("status"),
                "pause_reason": result.get("pause_reason"),
                "verdict": result.get("last_verdict"),
            }
            save_workflow(self.workflow_path, state)
            return state
        manifest_path = self.oracle_dir / "verification-manifest.json"
        manifest = load_manifest(manifest_path)
        state["oracle_manifest_sha256"] = digest_manifest(manifest)
        state["oracle_version"] = manifest.get("oracle_version")
        workspace = Path(result["workspace"])
        snap_root = self.workflow_path.parent / "snapshots" / state["workflow_id"]
        # Reuse snapshot if already created for this round
        existing = state.get("candidate_commit")
        snap_dir = snap_root / f"round-{state['round']}"
        if existing and snap_dir.is_dir() and (snap_dir / ".git").exists():
            try:
                head = subprocess.run(
                    ["git", "-C", str(snap_dir), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                if head == existing:
                    state["state"] = "qualified_by_checker"
                    save_workflow(self.workflow_path, state)
                    return state
            except subprocess.CalledProcessError:
                pass
        snap = snapshot_candidate(workspace, snap_dir)
        state["candidate_commit"] = snap["candidate_commit"]
        state["candidate_tree"] = snap["candidate_tree"]
        state["snapshot_dir"] = snap["snapshot_dir"]
        state["state"] = "qualified_by_checker"
        # Allow a later rejection to mint a fresh remediation campaign id.
        if state.get("remediation_campaign_id"):
            state["parent_remediation_campaign_id"] = state["remediation_campaign_id"]
            state["remediation_campaign_id"] = None
        _note(state, "qualified_by_checker", candidate=snap["candidate_commit"])
        save_workflow(self.workflow_path, state)
        return state

    def _begin_review(self, state: dict) -> dict:
        if state.get("reviewer_campaign_id") and state.get("review_path"):
            # Idempotent reuse
            state["state"] = "reviewing"
            save_workflow(self.workflow_path, state)
            return state
        reviewer_id = f"{state['implementer_campaign_id']}-review-r{state['round']}"
        state["reviewer_campaign_id"] = reviewer_id
        state["state"] = "reviewing"
        save_workflow(self.workflow_path, state)
        review_path = self.run_reviewer(state)
        state["review_path"] = str(review_path)
        save_workflow(self.workflow_path, state)
        return state

    def _finish_review(self, state: dict) -> dict:
        review_path = Path(state["review_path"])
        review = load_review(review_path)
        errors = validate_review(
            review,
            expected_candidate_commit=state["candidate_commit"],
            expected_oracle_manifest_sha256=state["oracle_manifest_sha256"],
        )
        if errors:
            state["state"] = "blocked"
            state["block_reason"] = "invalid_review: " + "; ".join(errors)
            save_workflow(self.workflow_path, state)
            return state
        snap_errors = verify_snapshot_unmodified(
            Path(state["snapshot_dir"]), state["candidate_commit"]
        )
        if snap_errors:
            state["state"] = "blocked"
            state["block_reason"] = "review mutated candidate: " + "; ".join(snap_errors)
            save_workflow(self.workflow_path, state)
            return state
        outcome = review["outcome"]
        if outcome == "qualified":
            state["state"] = "awaiting_merge"
            _note(state, "awaiting_merge", review=str(review_path))
            save_workflow(self.workflow_path, state)
            return state
        if outcome == "unresolved":
            state["unresolved_findings"] = list(review.get("findings") or [])
            state["state"] = "blocked"
            state["block_reason"] = "reviewer_unresolved"
            _note(state, "blocked_unresolved")
            save_workflow(self.workflow_path, state)
            return state
        # rejected
        state["state"] = "review_rejected"
        state["last_review"] = review
        _note(state, "review_rejected", findings=[f.get("id") for f in review["findings"]])
        save_workflow(self.workflow_path, state)
        return state

    def _begin_remediation(self, state: dict) -> dict:
        if state["round"] + 1 >= state.get("max_rounds", self.max_rounds):
            state["state"] = "blocked"
            state["block_reason"] = "review_round_limit"
            save_workflow(self.workflow_path, state)
            return state
        review = state.get("last_review") or load_review(Path(state["review_path"]))
        try:
            promoted = promote_grounded_findings(
                repo=self.repo,
                review=review,
                oracle_dir=self.oracle_dir,
                trusted_helpers=self.trusted_helpers,
                checker_relpath=self.checker_relpath,
            )
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            state["state"] = "blocked"
            state["block_reason"] = f"oracle_promotion_failed: {exc}"
            save_workflow(self.workflow_path, state)
            return state
        if promoted:
            state["oracle_manifest_sha256"] = digest_manifest(promoted)
            state["oracle_version"] = promoted.get("oracle_version")
        if not state.get("remediation_campaign_id"):
            new_id = f"{state['implementer_campaign_id']}-fix-r{state['round'] + 1}"
            state["remediation_campaign_id"] = new_id
            state["round"] = state["round"] + 1
            _note(state, "remediating", campaign_id=new_id)
        state["state"] = "remediating"
        # Clear reviewer fields for the next cycle
        state["reviewer_campaign_id"] = None
        state["review_path"] = None
        state["candidate_commit"] = None
        save_workflow(self.workflow_path, state)
        return state


def write_synthetic_rejection(
    path: Path,
    *,
    candidate_commit: str,
    oracle_manifest_sha256: str,
    reproducer_relpath: str,
) -> None:
    """Helper for A0.2 smoke: emit a validated rejected review."""
    write_review(
        path,
        {
            "schema": "eu4-review/v1",
            "candidate_commit": candidate_commit,
            "oracle_manifest_sha256": oracle_manifest_sha256,
            "outcome": "rejected",
            "findings": [
                {
                    "id": "a02-smoke-counterexample",
                    "severity": "P1",
                    "kind": "behavioral_counterexample",
                    "reproducer": reproducer_relpath,
                    "expected_provenance": "instruction_static",
                }
            ],
        },
    )
