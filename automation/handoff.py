"""Idempotent outer qualification handoff with repo-native candidate refs."""

from __future__ import annotations

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
# Narrow editable surface: only the candidate artifact + progress. Frozen
# evidence / manifests / helpers stay on the trusted parent tree.
CANDIDATE_EDITABLE_BASENAME = "primitive-lookup.candidate.json"
PROTECTED_EVIDENCE_BASENAMES = (
    "primitive-table.evidence.json",
    "primitive-lookup.golden.json",
    "verification-manifest.json",
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
        "reviewer_worktree": None,
        "reviewer_receipt_path": None,
        "implementer_session_id": None,
        "review_path": None,
        "review_digest": None,
        "remediation_campaign_id": None,
        "qualification_receipt": None,
        "unresolved_findings": [],
        "history": [],
    }


def path_allowlisted(rel: str) -> bool:
    """True iff *rel* may differ from the trusted parent in a candidate snapshot."""
    if rel == "progress.md":
        return True
    if not rel.startswith("analysis/qualification/"):
        return False
    return Path(rel).name == CANDIDATE_EDITABLE_BASENAME


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
    """Collect only authorized editable candidate + progress files."""
    rels: list[str] = []
    base = workspace / "analysis" / "qualification"
    if base.is_dir():
        for path in base.rglob(CANDIDATE_EDITABLE_BASENAME):
            if path.is_file():
                rels.append(path.relative_to(workspace).as_posix())
    progress = workspace / "progress.md"
    if progress.is_file():
        rels.append("progress.md")
    return rels


def _blob_at(repo: Path, commit: str, rel: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{commit}:{rel}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def assert_protected_evidence_matches_parent(
    repo: Path, *, parent_commit: str, candidate_commit: str
) -> None:
    """Protected evidence/checker blobs in the snapshot must equal the trusted parent."""
    names = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", candidate_commit],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for rel in names:
        if not rel.startswith("analysis/qualification/"):
            continue
        if path_allowlisted(rel):
            continue
        name = Path(rel).name
        if name not in PROTECTED_EVIDENCE_BASENAMES and not rel.endswith(
            "/accept.py"
        ):
            # Still require any non-editable qualification path match parent if present
            pass
        parent_blob = _blob_at(repo, parent_commit, rel)
        cand_blob = _blob_at(repo, candidate_commit, rel)
        if parent_blob is None:
            # Candidate must not introduce protected evidence absent from parent
            if name in PROTECTED_EVIDENCE_BASENAMES:
                raise RuntimeError(
                    f"snapshot introduced protected evidence absent from parent: {rel}"
                )
            continue
        if cand_blob != parent_blob:
            raise RuntimeError(
                f"protected evidence diverged from trusted parent: {rel}"
            )


def snapshot_candidate(
    repo: Path,
    workspace: Path,
    *,
    workflow_id: str,
    round_id: int,
    parent_commit: str,
    message: str = "qualification candidate snapshot",
) -> dict:
    """Full baseline + editable overlay only; parented commit in the original object store.

    The delta machinery is shared with the DSH path (``automation.candidate_snapshot``),
    so there is one implementation of "reproduce exactly these changes on top of a
    pinned baseline". What stays here is A0's policy: which files are editable, the
    protected-evidence check, the ref namespace and the commit authorship.
    """
    from automation import candidate_snapshot

    rels = collect_candidate_rels(workspace)
    if not rels:
        raise RuntimeError("no candidate files to snapshot under analysis/qualification")
    for rel in rels:
        if not path_allowlisted(rel):
            raise RuntimeError(f"candidate path outside allowlist: {rel}")

    delta = []
    for rel in rels:
        source = workspace / rel
        if not source.is_file():
            raise RuntimeError(f"candidate file is missing: {rel}")
        parent_blob = _blob_at(repo, parent_commit, rel)
        if parent_blob is not None:
            live = subprocess.run(
                ["git", "-C", str(repo), "hash-object", "--", str(source)],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            if live == parent_blob:
                # Unchanged files carry no delta; including them would ask the
                # snapshot to reproduce a change that does not exist.
                continue
        # Report the mode the snapshot will actually carry: the shared builder
        # preserves the executable bit, and the round-trip check compares against
        # this value.
        mode = "100755" if source.stat().st_mode & 0o100 else "100644"
        delta.append({
            "path": rel,
            # An untracked candidate file is an addition, not a modification.
            "kind": "modify" if parent_blob is not None else "add",
            "mode": mode,
            "symlink": False,
            "exists": True,
        })
    if not delta:
        # Nothing differs from the parent. A0 produced a parent-equal commit
        # here, so do the same rather than failing the round.
        tree = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", f"{parent_commit}^{{tree}}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "-C", str(repo), "commit-tree", tree, "-p", parent_commit,
             "-m", f"{message} ({workflow_id} r{round_id})"],
            capture_output=True, text=True, check=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "qualification",
                "GIT_AUTHOR_EMAIL": "qualification@local",
                "GIT_COMMITTER_NAME": "qualification",
                "GIT_COMMITTER_EMAIL": "qualification@local",
            },
        ).stdout.strip()
        ref = f"refs/qualification/{workflow_id}/r{round_id}"
        subprocess.run(["git", "-C", str(repo), "update-ref", ref, commit],
                       check=True, capture_output=True)
        return {
            "candidate_commit": commit,
            "candidate_tree": tree,
            "candidate_ref": ref,
            "candidate_parent": parent_commit,
            "changed_paths": [],
        }

    built = candidate_snapshot.build_candidate(
        workspace, repo, parent_commit, delta,
        message=f"{message} ({workflow_id} r{round_id})",
        identity={
            "GIT_AUTHOR_NAME": "qualification",
            "GIT_AUTHOR_EMAIL": "qualification@local",
            "GIT_COMMITTER_NAME": "qualification",
            "GIT_COMMITTER_EMAIL": "qualification@local",
        },
    )
    commit = built["candidate"]
    tree = built["tree"]

    problems = candidate_snapshot.verify_round_trip(repo, parent_commit, commit, delta)
    if problems:
        raise RuntimeError(
            "snapshot does not reproduce the candidate: " + "; ".join(problems[:4])
        )

    # Every diff vs parent must stay inside the editable allowlist
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", parent_commit, commit],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for path in diff:
        if not path_allowlisted(path):
            raise RuntimeError(f"snapshot diff outside allowlist: {path}")

    assert_protected_evidence_matches_parent(
        repo, parent_commit=parent_commit, candidate_commit=commit
    )

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
    """Return dirty-path errors. ``progress.md`` updates alone are allowed."""
    completed = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return [f"git status failed in {worktree}: {completed.stderr}"]
    errors: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        # porcelain: XY PATH or XY ORIG -> PATH
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if path == "progress.md":
            continue
        errors.append(f"dirty: {line}")
    return errors


def run_root_for(repo: Path, workflow_id: str, campaign_id: str) -> Path:
    """Runner state is workflow-scoped so independent workflows never share it."""
    return repo / ".local" / "automation" / workflow_id / campaign_id


def verify_reviewer_checkout(
    repo: Path, worktree: Path, candidate_commit: str
) -> list[str]:
    """Reviewer must judge the frozen candidate from a registered, clean checkout."""
    if not worktree.is_dir():
        return [f"reviewer worktree missing: {worktree}"]
    registered = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    target = str(worktree.resolve())
    if registered.returncode != 0 or not any(
        line == f"worktree {target}" for line in registered.stdout.splitlines()
    ):
        return [f"reviewer worktree not registered: {worktree}"]
    head_run = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if head_run.returncode != 0:
        return [f"reviewer worktree unusable: {worktree}"]
    errors: list[str] = []
    head = head_run.stdout.strip()
    if head != candidate_commit:
        errors.append(f"reviewer worktree HEAD {head} != {candidate_commit}")
    errors.extend(verify_worktree_clean(repo, worktree))
    return errors


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


def reviewer_receipt_path_for(report_path: Path) -> Path:
    return report_path.with_suffix(report_path.suffix + ".receipt.json")


def reviewer_invocation_path_for(report_path: Path) -> Path:
    return report_path.with_suffix(report_path.suffix + ".invocation.json")


def write_reviewer_invocation(
    path: Path,
    *,
    campaign_id: str,
    candidate_commit: str,
    oracle_digest: str,
    report_path: Path,
    worktree: Path,
) -> dict:
    """Launch record: the report path was absent when this reviewer session began."""
    payload = {
        "schema": "eu4-review-invocation/v1",
        "campaign_id": campaign_id,
        "candidate_commit": candidate_commit,
        "oracle_digest": oracle_digest,
        "report_path": str(report_path),
        "worktree": str(worktree),
    }
    atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def load_reviewer_invocation(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_reviewer_receipt(
    path: Path,
    *,
    campaign_id: str,
    candidate_commit: str,
    oracle_digest: str,
    reviewer_session_id: str,
    runner_status: str,
    report_sha256: str,
    qualification_mode: str = "live",
) -> dict:
    if runner_status != "complete":
        raise ValueError("reviewer receipt requires completed runner_status")
    if not reviewer_session_id:
        raise ValueError("reviewer receipt requires reviewer_session_id")
    payload = {
        "schema": "eu4-reviewer-receipt/v1",
        "campaign_id": campaign_id,
        "candidate_commit": candidate_commit,
        "oracle_digest": oracle_digest,
        "reviewer_session_id": reviewer_session_id,
        "runner_status": runner_status,
        "report_sha256": report_sha256,
        "qualification_mode": qualification_mode,
    }
    atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def load_reviewer_receipt(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_reviewer_receipt(
    receipt: dict,
    *,
    campaign_id: str,
    candidate_commit: str,
    oracle_digest: str,
    report_sha256: str,
    require_live: bool = True,
) -> list[str]:
    errors: list[str] = []
    if receipt.get("schema") != "eu4-reviewer-receipt/v1":
        errors.append("bad receipt schema")
    if receipt.get("campaign_id") != campaign_id:
        errors.append("receipt campaign_id mismatch")
    if receipt.get("candidate_commit") != candidate_commit:
        errors.append("receipt candidate_commit mismatch")
    if receipt.get("oracle_digest") != oracle_digest:
        errors.append("receipt oracle_digest mismatch")
    if receipt.get("report_sha256") != report_sha256:
        errors.append("receipt report_sha256 mismatch")
    if receipt.get("runner_status") != "complete":
        errors.append("receipt runner_status not complete")
    if not receipt.get("reviewer_session_id"):
        errors.append("receipt missing reviewer_session_id")
    if require_live and receipt.get("qualification_mode") == "scripted":
        errors.append("scripted receipt not valid for live qualification")
    return errors


def recover_completed_reviewer_receipt(
    repo: Path,
    *,
    run_root: Path,
    report_path: Path,
    invocation_path: Path,
    campaign_id: str,
    candidate_commit: str,
    oracle_digest: str,
    worktree: Path,
) -> dict | None:
    """Reconstruct a receipt only when launch record + completed runner vouch for the report.

    A report alone is untrusted: it may predate the session. The launch record proves
    the path was clear when the session started.
    """
    report_path = Path(report_path)
    state_path = Path(run_root) / "state.json"
    if not (
        report_path.is_file()
        and Path(invocation_path).is_file()
        and state_path.is_file()
    ):
        return None
    try:
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        invocation = load_reviewer_invocation(Path(invocation_path))
        review = load_review(report_path)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None
    if invocation.get("schema") != "eu4-review-invocation/v1":
        return None
    if invocation.get("campaign_id") != campaign_id:
        return None
    if invocation.get("candidate_commit") != candidate_commit:
        return None
    if invocation.get("oracle_digest") != oracle_digest:
        return None
    if Path(str(invocation.get("report_path"))) != report_path:
        return None
    if saved.get("campaign_id") != campaign_id:
        return None
    if saved.get("status") != "complete" or saved.get("last_verdict") != "pass":
        return None
    if saved.get("verification_manifest_sha256") != oracle_digest:
        return None
    session_id = saved.get("session_id")
    if not session_id:
        return None
    if validate_review(
        review,
        expected_candidate_commit=candidate_commit,
        expected_oracle_manifest_sha256=oracle_digest,
    ):
        return None
    receipt_path = reviewer_receipt_path_for(report_path)
    write_reviewer_receipt(
        receipt_path,
        campaign_id=campaign_id,
        candidate_commit=candidate_commit,
        oracle_digest=oracle_digest,
        reviewer_session_id=session_id,
        runner_status="complete",
        report_sha256=file_sha256(report_path),
        qualification_mode="live",
    )
    return {
        "path": report_path,
        "session_id": session_id,
        "receipt_path": str(receipt_path),
        "worktree": str(saved.get("workspace") or worktree),
        "qualification_mode": "live",
    }


def quarantine_report(path: Path) -> Path | None:
    """Move an unverified pre-existing report aside; return quarantine path."""
    if not path.is_file():
        return None
    dest = path.with_name(path.name + f".quarantine-{uuid.uuid4().hex[:8]}")
    path.replace(dest)
    receipt = reviewer_receipt_path_for(path)
    if receipt.is_file():
        receipt.replace(dest.with_name(dest.name + ".receipt.json"))
    invocation = reviewer_invocation_path_for(path)
    if invocation.is_file():
        invocation.replace(dest.with_name(dest.name + ".invocation.json"))
    return dest


def reviewer_worktree_path(repo: Path, workflow_id: str, round_id: int) -> Path:
    return repo / ".local" / "qualification" / f"review-wt-{workflow_id}-r{round_id}"


def ensure_reviewer_worktree(
    repo: Path,
    *,
    workflow_id: str,
    round_id: int,
    candidate_commit: str,
    active_session: bool = False,
    existing_path: str | None = None,
) -> Path:
    """Reuse or safely recreate a detached reviewer worktree keyed by workflow+round."""
    wt = reviewer_worktree_path(repo, workflow_id, round_id)
    if existing_path:
        prior = Path(existing_path)
        if prior.resolve() != wt.resolve() and prior.exists() and active_session:
            # Active session owns a different path — do not destroy it.
            return prior

    def _head(path: Path) -> str | None:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    def _is_registered(path: Path) -> bool:
        completed = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return False
        target = str(path.resolve())
        return any(
            line.startswith("worktree ") and line[len("worktree ") :] == target
            for line in completed.stdout.splitlines()
        )

    if wt.exists() and _is_registered(wt):
        head = _head(wt)
        if head == candidate_commit:
            return wt
        if active_session:
            raise RuntimeError(
                f"active reviewer session worktree HEAD {head} != {candidate_commit}"
            )
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "prune"],
            check=False,
            capture_output=True,
        )
    elif wt.exists():
        if active_session:
            raise RuntimeError(f"refusing to destroy active reviewer workspace: {wt}")
        # Orphan path (not a registered worktree) — safe to clear.
        import shutil

        shutil.rmtree(wt)

    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "--detach",
            str(wt),
            candidate_commit,
        ],
        check=True,
        capture_output=True,
    )
    return wt


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
            state = load_workflow(self.workflow_path)
            # Backfill the pin path for states created before it was recorded,
            # but only when the file still matches the workflow's pinned digest.
            if not state.get("oracle_manifest_path"):
                candidate = self.oracle_dir / "verification-manifest.json"
                try:
                    if candidate.is_file() and digest_manifest(
                        load_manifest(candidate)
                    ) == state.get("oracle_manifest_sha256"):
                        state["oracle_manifest_path"] = str(candidate)
                        save_workflow(self.workflow_path, state)
                except (OSError, json.JSONDecodeError):
                    pass
            return state
        state = default_state(
            workflow_id="wf_" + uuid.uuid4().hex,
            implementer_campaign_id=implementer_campaign_id,
            pin=pin,
        )
        state["oracle_manifest_sha256"] = digest_manifest(
            load_manifest(self.oracle_dir / "verification-manifest.json")
        )
        state["checker_sha256"] = file_sha256(self.checker_path)
        state["oracle_manifest_path"] = str(
            self.oracle_dir / "verification-manifest.json"
        )
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
        # The checker-qualified oracle is the digest the runner started with; never
        # relabel the candidate from the (mutable) active tip.
        runner_digest = result.get("verification_manifest_sha256")
        pinned_digest = state.get("oracle_manifest_sha256")
        if (
            not runner_digest
            or not pinned_digest
            or runner_digest != pinned_digest
        ):
            state["state"] = "blocked"
            state["block_reason"] = (
                "oracle_pin_mismatch: "
                f"runner={runner_digest or 'missing'} "
                f"workflow={pinned_digest or 'missing'}"
            )
            save_workflow(self.workflow_path, state)
            return state
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
        # New review round: clear prior reviewer identity so a pre-existing
        # report cannot inherit a later session id.
        state["reviewer_session_id"] = None
        state["reviewer_worktree"] = None
        state["reviewer_receipt_path"] = None
        if not state.get("reviewer_campaign_id"):
            state["reviewer_campaign_id"] = (
                f"{state['implementer_campaign_id']}-review-r{state['round']}"
                f"-{state['workflow_id']}"
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
            if review_result.get("receipt_path"):
                state["reviewer_receipt_path"] = str(review_result["receipt_path"])
            if review_result.get("qualification_mode"):
                state["review_qualification_mode"] = review_result["qualification_mode"]
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

    def _require_live_reviewer_receipt(self, state: dict, report_sha: str) -> list[str]:
        if not state.get("require_live_sessions"):
            return []
        receipt_path = state.get("reviewer_receipt_path")
        if not receipt_path:
            candidate = reviewer_receipt_path_for(Path(state["review_path"]))
            if candidate.is_file():
                receipt_path = str(candidate)
        if not receipt_path or not Path(receipt_path).is_file():
            return ["missing_reviewer_receipt"]
        try:
            receipt = load_reviewer_receipt(Path(receipt_path))
        except (OSError, json.JSONDecodeError) as exc:
            return [f"unreadable_reviewer_receipt: {exc}"]
        return verify_reviewer_receipt(
            receipt,
            campaign_id=state["reviewer_campaign_id"],
            candidate_commit=state["candidate_commit"],
            oracle_digest=state["oracle_manifest_sha256"],
            report_sha256=report_sha,
            require_live=True,
        )

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
        if state.get("reviewer_worktree") or state.get("require_live_sessions"):
            wt = state.get("reviewer_worktree") or str(
                reviewer_worktree_path(
                    self.repo, state["workflow_id"], state["round"]
                )
            )
            worktree_errs = verify_reviewer_checkout(
                self.repo, Path(wt), state["candidate_commit"]
            )
            if worktree_errs:
                state["state"] = "blocked"
                state["block_reason"] = "reviewer_worktree: " + "; ".join(
                    worktree_errs
                )
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
        if state.get("require_live_sessions"):
            receipt_errs = self._require_live_reviewer_receipt(
                state, state["review_digest"]
            )
            if receipt_errs:
                state["state"] = "blocked"
                state["block_reason"] = "live_reviewer_receipt: " + "; ".join(
                    receipt_errs
                )
                save_workflow(self.workflow_path, state)
                return state
            if not state.get("reviewer_session_id"):
                state["state"] = "blocked"
                state["block_reason"] = "missing_reviewer_session_id"
                save_workflow(self.workflow_path, state)
                return state
        outcome = review["outcome"]
        if outcome == "qualified":
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
            new_id = (
                f"{state['implementer_campaign_id']}-fix-r{state['round'] + 1}"
                f"-{state['workflow_id']}"
            )
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
        state["reviewer_session_id"] = None
        state["reviewer_worktree"] = None
        state["reviewer_receipt_path"] = None
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
