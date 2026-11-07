"""Idempotent outer qualification handoff with repo-native candidate refs."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from typing import Callable

from automation.oracle_promote import promote_grounded_findings
from automation.review_result import load_review, validate_review, write_review
from automation.state import atomic_write, campaign_lock
from automation.verification_manifest import digest_manifest, file_sha256, load_manifest

MAX_ROUNDS = 3
CANDIDATE_ALLOWLIST_PREFIXES = (
    "analysis/qualification/",
    "progress.md",
)


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
        "candidate_ref": None,
        "candidate_parent": None,
        "oracle_manifest_sha256": None,
        "oracle_version": None,
        "oracle_manifest_path": None,
        "checker_sha256": None,
        "reviewer_campaign_id": None,
        "reviewer_session_id": None,
        "implementer_session_id": None,
        "review_path": None,
        "review_digest": None,
        "remediation_campaign_id": None,
        "qualification_receipt": None,
        "unresolved_findings": [],
        "history": [],
    }


def path_allowlisted(rel: str) -> bool:
    if rel == "progress.md":
        return True
    return any(rel.startswith(prefix) for prefix in CANDIDATE_ALLOWLIST_PREFIXES)


def load_workflow(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_workflow(path: Path, state: dict) -> None:
    atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def _note(state: dict, event: str, **data) -> None:
    state.setdefault("history", []).append({"event": event, **data})


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def collect_candidate_rels(workspace: Path) -> list[str]:
    rels: list[str] = []
    base = workspace / "analysis" / "qualification"
    if base.is_dir():
        for path in base.rglob("*"):
            if path.is_file():
                rels.append(path.relative_to(workspace).as_posix())
    progress = workspace / "progress.md"
    if progress.is_file():
        rels.append("progress.md")
    return rels


def snapshot_candidate(
    repo: Path,
    workspace: Path,
    *,
    workflow_id: str,
    round_id: int,
    parent_commit: str,
    message: str = "qualification candidate snapshot",
) -> dict:
    """Full baseline + allowlisted overlay; parented commit in the original object store."""
    import os
    import tempfile

    rels = collect_candidate_rels(workspace)
    if not rels:
        raise RuntimeError("no candidate files to snapshot under analysis/qualification")
    for rel in rels:
        if not path_allowlisted(rel):
            raise RuntimeError(f"candidate path outside allowlist: {rel}")

    fd, index_name = tempfile.mkstemp(prefix=f"qual-index-{workflow_id}-{round_id}-")
    os.close(fd)
    index_file = Path(index_name)
    index_file.unlink()
    env = {**os.environ, "GIT_INDEX_FILE": str(index_file)}
    try:
        # Start from pinned baseline tree
        subprocess.run(
            ["git", "-C", str(repo), "read-tree", parent_commit],
            check=True,
            capture_output=True,
            env=env,
        )
        for rel in rels:
            src = workspace / rel
            blob = subprocess.run(
                ["git", "-C", str(repo), "hash-object", "-w", str(src)],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            ).stdout.strip()
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    "100644",
                    blob,
                    rel,
                ],
                check=True,
                capture_output=True,
                env=env,
            )
        tree = subprocess.run(
            ["git", "-C", str(repo), "write-tree"],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        ).stdout.strip()
        commit = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "commit-tree",
                tree,
                "-p",
                parent_commit,
                "-m",
                f"{message} ({workflow_id} r{round_id})",
            ],
            capture_output=True,
            text=True,
            check=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "qualification",
                "GIT_AUTHOR_EMAIL": "qualification@local",
                "GIT_COMMITTER_NAME": "qualification",
                "GIT_COMMITTER_EMAIL": "qualification@local",
            },
        ).stdout.strip()
    finally:
        if index_file.exists():
            index_file.unlink()

    # Every diff vs parent must stay inside the allowlist
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", parent_commit, commit],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for path in diff:
        if not path_allowlisted(path):
            raise RuntimeError(f"snapshot diff outside allowlist: {path}")

    ref = f"refs/qualification/{workflow_id}/r{round_id}"
    subprocess.run(
        ["git", "-C", str(repo), "update-ref", ref, commit],
        check=True,
        capture_output=True,
    )
    return {
        "candidate_commit": commit,
        "candidate_tree": tree,
        "candidate_ref": ref,
        "candidate_parent": parent_commit,
        "changed_paths": diff,
    }


def verify_worktree_clean(repo: Path, worktree: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return [f"git status failed in {worktree}: {completed.stderr}"]
    dirty = [line for line in completed.stdout.splitlines() if line.strip()]
    return [f"dirty: {line}" for line in dirty] if dirty else []


def verify_ref_unmodified(repo: Path, ref: str, expected_commit: str) -> list[str]:
    errors = []
    try:
        head = _git(repo, "rev-parse", ref)
    except subprocess.CalledProcessError as exc:
        return [f"ref missing: {ref}: {exc}"]
    if head != expected_commit:
        errors.append(f"ref {ref} moved: {head} != {expected_commit}")
    return errors


def receipt_tuple(state: dict) -> dict:
    return {
        "candidate_commit": state.get("candidate_commit"),
        "oracle_digest": state.get("oracle_manifest_sha256"),
        "checker_digest": state.get("checker_sha256"),
        "review_digest": state.get("review_digest"),
    }


class HandoffController:
    def __init__(
        self,
        *,
        repo: Path,
        workflow_path: Path,
        run_implementer: Callable[[str, dict], dict],
        run_reviewer: Callable[[dict], Path],
        oracle_dir: Path,
        checker_path: Path,
        max_rounds: int = MAX_ROUNDS,
        trusted_helpers: list[str] | None = None,
        checker_relpath: str = "campaigns/eu4-a01-trusted-validation/accept.py",
    ):
        self.repo = repo
        self.workflow_path = workflow_path
        self.run_implementer = run_implementer
        self.run_reviewer = run_reviewer
        self.oracle_dir = oracle_dir
        self.checker_path = checker_path
        self.max_rounds = max_rounds
        self.trusted_helpers = trusted_helpers or [
            "automation/evidence.py",
            "automation/macho_map.py",
            "automation/verification_manifest.py",
            "automation/review_result.py",
            "automation/oracle_promote.py",
            "automation/replay_primitive.py",
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
        state["oracle_manifest_sha256"] = digest_manifest(
            load_manifest(self.oracle_dir / "verification-manifest.json")
        )
        state["checker_sha256"] = file_sha256(self.checker_path)
        cur = self.oracle_dir / "CURRENT"
        state["oracle_version"] = (
            cur.read_text(encoding="utf-8").strip() if cur.is_file() else "v0"
        )
        save_workflow(self.workflow_path, state)
        return state

    def step(self, state: dict) -> dict:
        with campaign_lock(self.workflow_path.with_suffix(".lock")):
            current = state["state"]
            if current in ("implementing", "remediating"):
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
        # Pin oracle version into campaign context before the turn.
        result = self.run_implementer(campaign_id, state)
        if result.get("session_id"):
            state["implementer_session_id"] = result["session_id"]
        if result.get("status") != "complete" or result.get("last_verdict") != "pass":
            state["last_implementer"] = {
                "campaign_id": campaign_id,
                "status": result.get("status"),
                "pause_reason": result.get("pause_reason"),
                "verdict": result.get("last_verdict"),
            }
            save_workflow(self.workflow_path, state)
            return state
        workspace = Path(result["workspace"])
        # Reuse snapshot if ref already exists for this round
        if state.get("candidate_ref") and state.get("candidate_commit"):
            errs = verify_ref_unmodified(
                self.repo, state["candidate_ref"], state["candidate_commit"]
            )
            if not errs:
                state["state"] = "qualified_by_checker"
                save_workflow(self.workflow_path, state)
                return state
        parent = state.get("candidate_parent") or state.get("pin_commit")
        if not parent:
            raise RuntimeError("pin_commit required for complete candidate snapshot")
        # Snapshot immediately after checker pass — no further agent edits.
        snap = snapshot_candidate(
            self.repo,
            workspace,
            workflow_id=state["workflow_id"],
            round_id=state["round"],
            parent_commit=parent,
        )
        state["candidate_commit"] = snap["candidate_commit"]
        state["candidate_tree"] = snap["candidate_tree"]
        state["candidate_ref"] = snap["candidate_ref"]
        state["candidate_parent"] = snap["candidate_parent"]
        tip_manifest = load_manifest(self.oracle_dir / "verification-manifest.json")
        state["oracle_manifest_sha256"] = digest_manifest(tip_manifest)
        state["oracle_version"] = tip_manifest.get("oracle_version_name") or state.get(
            "oracle_version"
        )
        state["oracle_manifest_path"] = tip_manifest.get("version_manifest_path") or str(
            self.oracle_dir / "verification-manifest.json"
        )
        state["checker_sha256"] = file_sha256(self.checker_path)
        state["state"] = "qualified_by_checker"
        if state.get("remediation_campaign_id"):
            state["parent_remediation_campaign_id"] = state["remediation_campaign_id"]
            state["remediation_campaign_id"] = None
        _note(state, "qualified_by_checker", **snap)
        save_workflow(self.workflow_path, state)
        return state

    def _begin_review(self, state: dict) -> dict:
        if not state.get("candidate_ref") or not state.get("candidate_commit"):
            state["state"] = "blocked"
            state["block_reason"] = "missing_candidate_ref"
            save_workflow(self.workflow_path, state)
            return state
        if state.get("review_path") and Path(state["review_path"]).is_file():
            state["state"] = "reviewing"
            save_workflow(self.workflow_path, state)
            return self._finish_review(state)
        if not state.get("reviewer_campaign_id"):
            state["reviewer_campaign_id"] = (
                f"{state['implementer_campaign_id']}-review-r{state['round']}"
            )
        # Persist reviewing BEFORE invoking reviewer only with campaign id;
        # review_path stays null until artifact exists (resume re-invokes).
        state["state"] = "reviewing"
        save_workflow(self.workflow_path, state)
        review_result = self.run_reviewer(state)
        if isinstance(review_result, dict):
            report = Path(review_result["path"])
            if review_result.get("session_id"):
                state["reviewer_session_id"] = review_result["session_id"]
            if review_result.get("worktree"):
                state["reviewer_worktree"] = str(review_result["worktree"])
        else:
            report = Path(review_result)
        if not report.is_file():
            state["block_reason"] = "reviewer_returned_missing_path"
            state["state"] = "blocked"
            save_workflow(self.workflow_path, state)
            return state
        state["review_path"] = str(report)
        save_workflow(self.workflow_path, state)
        return state

    def _finish_review(self, state: dict) -> dict:
        if not state.get("review_path"):
            # Resume: re-invoke reviewer
            return self._begin_review({**state, "state": "qualified_by_checker"})
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
        ref_errs = verify_ref_unmodified(
            self.repo, state["candidate_ref"], state["candidate_commit"]
        )
        if ref_errs:
            state["state"] = "blocked"
            state["block_reason"] = "candidate_ref_changed: " + "; ".join(ref_errs)
            save_workflow(self.workflow_path, state)
            return state
        if state.get("reviewer_worktree"):
            dirty = verify_worktree_clean(self.repo, Path(state["reviewer_worktree"]))
            if dirty:
                state["state"] = "blocked"
                state["block_reason"] = "reviewer_worktree_dirty: " + "; ".join(dirty)
                save_workflow(self.workflow_path, state)
                return state
        if (
            state.get("reviewer_session_id")
            and state.get("implementer_session_id")
            and state["reviewer_session_id"] == state["implementer_session_id"]
        ):
            state["state"] = "blocked"
            state["block_reason"] = "reviewer_session_id matches implementer"
            save_workflow(self.workflow_path, state)
            return state
        state["review_digest"] = file_sha256(review_path)
        outcome = review["outcome"]
        if outcome == "qualified":
            if not state.get("reviewer_session_id") and state.get("require_live_sessions"):
                state["state"] = "blocked"
                state["block_reason"] = "missing_reviewer_session_id"
                save_workflow(self.workflow_path, state)
                return state
            state["qualification_receipt"] = receipt_tuple(state)
            state["state"] = "awaiting_merge"
            _note(state, "awaiting_merge")
            save_workflow(self.workflow_path, state)
            return state
        if outcome == "unresolved":
            state["unresolved_findings"] = list(review.get("findings") or [])
            state["state"] = "blocked"
            state["block_reason"] = "reviewer_unresolved"
            save_workflow(self.workflow_path, state)
            return state
        state["state"] = "review_rejected"
        state["last_review"] = review
        state["rejected_candidate_commit"] = state["candidate_commit"]
        state["rejected_candidate_ref"] = state["candidate_ref"]
        _note(state, "review_rejected")
        save_workflow(self.workflow_path, state)
        return state

    def _begin_remediation(self, state: dict) -> dict:
        if state["round"] + 1 >= state.get("max_rounds", self.max_rounds):
            state["state"] = "blocked"
            state["block_reason"] = "review_round_limit"
            save_workflow(self.workflow_path, state)
            return state
        review = state.get("last_review") or load_review(Path(state["review_path"]))
        review_path = Path(state["review_path"]) if state.get("review_path") else None
        try:
            promoted = promote_grounded_findings(
                repo=self.repo,
                review=review,
                oracle_dir=self.oracle_dir,
                trusted_helpers=self.trusted_helpers,
                checker_relpath=self.checker_relpath,
                review_path=review_path,
            )
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            state["state"] = "blocked"
            state["block_reason"] = f"oracle_promotion_failed: {exc}"
            save_workflow(self.workflow_path, state)
            return state
        if promoted.get("promoted"):
            state["oracle_manifest_sha256"] = promoted["manifest_sha256"]
            state["oracle_version"] = promoted["oracle_version"]
            if promoted.get("version_manifest_path"):
                state["oracle_manifest_path"] = promoted["version_manifest_path"]
            # New oracle => new campaign; never retarget paused campaign in place.
        else:
            state.setdefault("unresolved_findings", [])
            for d in promoted.get("decisions") or []:
                if d.get("decision") != "promote":
                    state["unresolved_findings"].append(d)

        if not state.get("remediation_campaign_id"):
            new_id = f"{state['implementer_campaign_id']}-fix-r{state['round'] + 1}"
            state["remediation_campaign_id"] = new_id
            state["round"] = state["round"] + 1
            _note(
                state,
                "remediating",
                campaign_id=new_id,
                oracle_version=state.get("oracle_version"),
                rejected_candidate=state.get("rejected_candidate_commit"),
            )
        state["state"] = "remediating"
        state["reviewer_campaign_id"] = None
        state["review_path"] = None
        state["review_digest"] = None
        # Keep rejected_* for remediation seed; clear active candidate for next snapshot
        state["candidate_parent"] = state.get("rejected_candidate_commit") or state.get(
            "pin_commit"
        )
        state["candidate_commit"] = None
        state["candidate_tree"] = None
        state["candidate_ref"] = None
        save_workflow(self.workflow_path, state)
        return state


def write_synthetic_rejection(
    path: Path,
    *,
    candidate_commit: str,
    oracle_manifest_sha256: str,
    binding: dict,
    finding_id: str = "a02-smoke-counterexample",
) -> None:
    write_review(
        path,
        {
            "schema": "eu4-review/v1",
            "candidate_commit": candidate_commit,
            "oracle_manifest_sha256": oracle_manifest_sha256,
            "outcome": "rejected",
            "findings": [
                {
                    "id": finding_id,
                    "severity": "P1",
                    "kind": "behavioral_counterexample",
                    "reproducer": "analysis/qualification/v0/primitive-lookup.candidate.json",
                    "expected_provenance": "instruction_static",
                    "binding": binding,
                }
            ],
        },
    )
