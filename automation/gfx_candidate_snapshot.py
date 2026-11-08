"""Narrow candidate snapshot/reviewer materialization for L3.2b campaigns.

This does not run an implementer or reviewer session; it only creates the
allowlisted candidate commit and, optionally, a reviewer campaign JSON from the
tracked reviewer template. The human/operator runs the reviewer through the
existing OpenCode runner.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation.gfx_draw_accept import (  # noqa: E402
    changed_paths,
    load_config,
    manifest_problem,
    path_allowed,
    worktree_problem,
)
from automation.state import repo_root  # noqa: E402
from automation.verification_manifest import (  # noqa: E402
    digest_manifest,
    load_manifest,
    verify_manifest,
)


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


def _changed_files(workspace: Path) -> list[str]:
    return changed_paths(workspace)


def snapshot(
    workspace: Path, campaign_dir: Path, round_id: int = 1
) -> dict:
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
    manifest_error = manifest_problem(campaign_dir, config, pin)
    if manifest_error:
        raise SnapshotError(manifest_error)
    paths = _changed_files(workspace)
    if not paths:
        raise SnapshotError("candidate worktree has no changes")
    allowed = [str(item) for item in config["allowed_paths"]]
    forbidden = [str(item) for item in config.get("forbidden_paths", [])]
    for path in paths:
        if not path_allowed(path, allowed):
            raise SnapshotError(f"candidate path outside allowlist: {path}")
        for pattern in forbidden:
            if path == pattern or path.startswith(pattern.rstrip("/") + "/"):
                raise SnapshotError(f"frozen path modified: {path}")

    repo = repo_root(campaign_dir)
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
            [
                "git",
                "-C",
                str(repo),
                "commit-tree",
                tree,
                "-p",
                pin,
                "-m",
                f"L3.2b candidate snapshot ({campaign_id} r{round_id})",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=commits,
        )
        if commit.returncode != 0:
            raise SnapshotError(commit.stderr.strip() or "commit-tree failed")
        candidate = commit.stdout.strip()
        diff = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", pin, candidate],
            capture_output=True,
            text=True,
            check=False,
        )
        if diff.returncode != 0:
            raise SnapshotError("candidate diff failed")
        for path in diff.stdout.splitlines():
            if not path_allowed(path, allowed):
                raise SnapshotError(f"snapshot diff outside allowlist: {path}")
        ref = f"refs/gfx-candidate/{campaign_id}/r{round_id}"
        _git(repo, "update-ref", ref, candidate)
    finally:
        if index_file.exists():
            index_file.unlink()

    manifest = load_manifest(campaign_dir / str(config["manifest"]))
    oracle_digest = digest_manifest(manifest)
    return {
        "schema": "eu4-gfx-candidate/v1",
        "campaign_id": campaign_id,
        "pin_commit": pin,
        "candidate_commit": candidate,
        "candidate_ref": ref,
        "oracle_manifest_sha256": oracle_digest,
        "changed_paths": paths,
        "workspace": str(workspace),
    }


def materialize_reviewer(
    campaign_dir: Path, candidate: dict, round_id: int = 1
) -> Path:
    repo = repo_root(Path(campaign_dir).resolve())
    review_id = f"eu4-l32b-review-{candidate['campaign_id']}-r{round_id}"
    review_root = repo / ".local" / "automation" / review_id
    reviewer_dir = review_root / "reviewer"
    if review_root.exists():
        raise SnapshotError(f"reviewer campaign already exists: {review_root}")
    shutil.copytree(ROOT / "campaigns" / "eu4-l32b-reviewer", reviewer_dir)
    campaign = json.loads((reviewer_dir / "campaign.json").read_text())
    campaign["id"] = review_id
    campaign["workspace"] = {
        "mode": "worktree",
        "path": str(review_root / "workspace"),
        "commit": candidate["candidate_commit"],
    }
    (reviewer_dir / "campaign.json").write_text(
        json.dumps(campaign, indent=2) + "\n"
    )
    workspace = review_root / "workspace"
    completed = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(workspace),
         candidate["candidate_commit"]],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        shutil.rmtree(review_root, ignore_errors=True)
        raise SnapshotError(
            completed.stderr.strip() or "reviewer worktree creation failed"
        )
    report = review_root / "review.json"
    return {
        "review_id": review_id,
        "campaign": str(reviewer_dir / "campaign.json"),
        "workspace": str(review_root / "workspace"),
        "report": str(report),
        "env": {
            "EU4_REVIEW_REPORT_PATH": str(report),
            "EU4_REVIEW_EXPECTED_CANDIDATE": candidate["candidate_commit"],
            "EU4_REVIEW_EXPECTED_ORACLE": candidate["oracle_manifest_sha256"],
        },
        "command": f"python3 -m automation.cli run {reviewer_dir / 'campaign.json'}",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--workspace", required=True)
    snap.add_argument("--campaign", required=True)
    snap.add_argument("--round", type=int, default=1)
    snap.add_argument("--output")
    rev = sub.add_parser("reviewer")
    rev.add_argument("--campaign", required=True)
    rev.add_argument("--candidate-json", required=True)
    rev.add_argument("--round", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        campaign_path = Path(args.campaign)
        campaign_dir = campaign_path.parent if campaign_path.is_file() else campaign_path
        if args.command == "snapshot":
            result = snapshot(Path(args.workspace), campaign_dir, args.round)
            if args.output:
                Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2))
            return 0
        candidate = json.loads(Path(args.candidate_json).read_text())
        result = materialize_reviewer(campaign_dir, candidate, args.round)
        print(json.dumps(result, indent=2))
        return 0
    except (SnapshotError, OSError, json.JSONDecodeError) as exc:
        print(f"snapshot failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
