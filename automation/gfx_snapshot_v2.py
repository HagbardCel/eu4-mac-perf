"""V2-compatible candidate snapshots for generated L3.2 campaigns.

The legacy ``gfx_candidate_snapshot.snapshot`` path stays unchanged for the
completed #178/L3.2b-I review. Generated campaigns use a tracked template, a
v2 manifest and a candidate base that advances after integration.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from automation.gfx_checkpoint import source_tree_digest, source_tree_digest_at_commit
from automation.gfx_draw_accept_v2 import (
    changed_paths,
    load_config,
    manifest_problem,
    path_allowed,
    worktree_problem,
)
from automation.manifest_revision import verify_manifest_at_commit
from automation.verification_manifest import digest_manifest, load_manifest


class SnapshotError(RuntimeError):
    pass


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        raise SnapshotError(completed.stderr.strip() or "git failed")
    return completed.stdout.strip()


def _commit_candidate(
    repo: Path,
    *,
    pin: str,
    workspace: Path,
    paths: list[str],
    campaign_id: str,
    round_id: int,
    ref_prefix: str,
    message: str,
) -> tuple[str, str]:
    ref = f"refs/{ref_prefix}/{campaign_id}/r{round_id}"
    existing = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if existing.returncode == 0 and existing.stdout.strip():
        commit = existing.stdout.strip()
        committed = source_tree_digest_at_commit(repo, commit, paths)
        current = source_tree_digest(workspace, paths)
        if committed != current:
            raise SnapshotError(
                "existing candidate ref does not match the current workspace"
            )
        return commit, committed
    fd, index_name = tempfile.mkstemp(prefix=f"gfx-candidate-{uuid.uuid4().hex[:8]}-")
    os.close(fd)
    index_file = Path(index_name)
    index_file.unlink()
    env = {**os.environ, "GIT_INDEX_FILE": str(index_file)}
    try:
        _git(repo, "read-tree", pin, env=env)
        for rel in paths:
            source = workspace / rel
            if source.is_dir():
                raise SnapshotError(f"candidate path is a directory: {rel}")
            blob = _git(repo, "hash-object", "-w", str(source), env=env)
            _git(
                repo,
                "update-index",
                "--add",
                "--cacheinfo",
                f"100644,{blob},{rel}",
                env=env,
            )
        tree = _git(repo, "write-tree", env=env)
        commits = {
            **os.environ,
            "GIT_AUTHOR_NAME": "gfx-candidate",
            "GIT_AUTHOR_EMAIL": "gfx-candidate@local",
            "GIT_COMMITTER_NAME": "gfx-candidate",
            "GIT_COMMITTER_EMAIL": "gfx-candidate@local",
        }
        commit = subprocess.run(
            ["git", "-C", str(repo), "commit-tree", tree, "-p", pin, "-m", message],
            capture_output=True,
            text=True,
            check=False,
            env=commits,
        )
        if commit.returncode != 0:
            raise SnapshotError(commit.stderr.strip() or "commit-tree failed")
        candidate = commit.stdout.strip()
        _git(repo, "update-ref", ref, candidate)
    finally:
        if index_file.exists():
            index_file.unlink()
    return candidate, source_tree_digest(workspace, paths)


def snapshot_v2(workspace: Path, campaign_dir: Path, round_id: int = 1) -> dict:
    workspace = Path(workspace).resolve()
    campaign_dir = Path(campaign_dir).resolve()
    try:
        campaign, config = load_config(campaign_dir)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"campaign configuration unreadable: {exc}") from exc
    pin = str(campaign["workspace"]["commit"])
    campaign_id = str(campaign["id"])
    problem = worktree_problem(workspace, pin)
    if problem:
        raise SnapshotError(problem[1])
    manifest_error = manifest_problem(campaign_dir, config, pin, campaign)
    if manifest_error:
        raise SnapshotError(manifest_error)
    paths = changed_paths(workspace)
    if not paths:
        raise SnapshotError("candidate worktree has no changes")
    allowed = [str(item) for item in config["allowed_paths"]]
    forbidden = [str(item) for item in config.get("forbidden_paths", [])]
    for path in paths:
        if not path_allowed(path, allowed):
            raise SnapshotError(f"candidate path outside allowlist: {path}")
        if any(path == pattern or path.startswith(pattern.rstrip("/") + "/")
               for pattern in forbidden):
            raise SnapshotError(f"frozen path modified: {path}")
    repo = ROOT
    commit, candidate_digest = _commit_candidate(
        repo,
        pin=pin,
        workspace=workspace,
        paths=paths,
        campaign_id=campaign_id,
        round_id=round_id,
        ref_prefix="gfx-candidate",
        message=f"L3.2b v2 candidate snapshot ({campaign_id} r{round_id})",
    )
    template = Path(str(campaign.get("_template_dir") or campaign_dir))
    manifest = load_manifest(template / str(config["manifest"]))
    return {
        "schema": "eu4-gfx-candidate/v2",
        "campaign_id": campaign_id,
        "pin_commit": pin,
        "candidate_commit": commit,
        "candidate_ref": f"refs/gfx-candidate/{campaign_id}/r{round_id}",
        "candidate_digest": candidate_digest,
        "oracle_manifest_sha256": digest_manifest(manifest),
        "changed_paths": paths,
        "workspace": str(workspace),
    }


def snapshot_contract_v1(workspace: Path, campaign_dir: Path, round_id: int = 1) -> dict:
    workspace = Path(workspace).resolve()
    campaign_dir = Path(campaign_dir).resolve()
    try:
        campaign = json.loads((campaign_dir / "campaign.json").read_text(encoding="utf-8"))
        config = json.loads((campaign_dir / "contract.json").read_text(encoding="utf-8"))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"contract campaign configuration unreadable: {exc}") from exc
    pin = str(campaign["workspace"]["commit"])
    campaign_id = str(campaign["id"])
    version = str(config["candidate_version"])
    problem = worktree_problem(workspace, pin)
    if problem:
        raise SnapshotError(problem[1])
    manifest_path = campaign_dir / str(config["manifest"])
    try:
        manifest = load_manifest(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"contract manifest unreadable: {exc}") from exc
    errors = verify_manifest_at_commit(ROOT, manifest, pin)
    if errors:
        raise SnapshotError("contract manifest: " + "; ".join(errors[:6]))
    paths = changed_paths(workspace)
    if not paths:
        raise SnapshotError("contract worktree has no changes")
    allowed = [str(item) for item in config["allowed_paths"]]
    forbidden = [str(item) for item in config.get("forbidden_paths", [])]
    for path in paths:
        if not path_allowed(path, allowed):
            raise SnapshotError(f"contract path outside allowlist: {path}")
        if any(path == pattern or path.startswith(pattern.rstrip("/") + "/")
               for pattern in forbidden):
            raise SnapshotError(f"frozen path modified: {path}")
    commit, candidate_digest = _commit_candidate(
        ROOT,
        pin=pin,
        workspace=workspace,
        paths=paths,
        campaign_id=campaign_id,
        round_id=round_id,
        ref_prefix="gfx-contract-candidate",
        message=f"L3.2b contract candidate ({campaign_id} {version} r{round_id})",
    )
    module = workspace / "automation" / "gfx_contracts" / f"{version}.py"
    if not module.is_file():
        raise SnapshotError(f"contract module is missing: {module}")
    import hashlib

    return {
        "schema": "eu4-gfx-contract-candidate/v1",
        "campaign_id": campaign_id,
        "pin_commit": pin,
        "candidate_commit": commit,
        "candidate_ref": f"refs/gfx-contract-candidate/{campaign_id}/r{round_id}",
        "candidate_digest": candidate_digest,
        "contract_version": version,
        "contract_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
        "oracle_manifest_sha256": digest_manifest(manifest),
        "changed_paths": paths,
        "workspace": str(workspace),
    }
