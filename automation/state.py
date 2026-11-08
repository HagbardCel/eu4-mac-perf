"""Campaign JSON, atomic state, and a single file lock."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Campaign:
    id: str
    root: Path
    objective_text: str
    runtime: str
    model: str
    opencode_version: str
    seed: Path | None
    worktree: Path | None
    worktree_commit: str | None
    accept: list[str]
    checker: Path
    max_rounds: int
    max_seconds: float
    accept_timeout: float
    verification_manifest: Path | None = None
    live_launch_limit: int | None = None
    live_deadline_seconds: float | None = None


def load_campaign(path: Path) -> Campaign:
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    required = (
        "id", "objective", "runtime", "model", "opencode_version", "workspace",
        "accept", "max_rounds", "max_seconds", "accept_timeout_seconds",
    )
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError("campaign missing " + ", ".join(missing))
    if data["runtime"] != "opencode":
        raise ValueError("only the opencode runtime is supported")
    model = str(data["model"])
    if "/" not in model:
        raise ValueError("model must be provider/id")
    root = path.parent
    checker = _checker_file(root, data["accept"])
    seed, worktree, commit = _workspace(root, data["workspace"])
    live_limit = None
    live_deadline = None
    raw_live = data.get("live_observation")
    if raw_live is not None:
        if not isinstance(raw_live, dict):
            raise ValueError("live_observation must be an object")
        if raw_live.get("launch_limit") is not None:
            live_limit = int(raw_live["launch_limit"])
            if live_limit < 0 or live_limit > 10:
                raise ValueError("live_observation.launch_limit must be 0..10")
        if raw_live.get("deadline_seconds") is not None:
            live_deadline = float(raw_live["deadline_seconds"])
            if live_deadline < 1 or live_deadline > 600:
                raise ValueError(
                    "live_observation.deadline_seconds must be 1..600"
                )
    manifest = None
    raw_manifest = data.get("verification_manifest")
    if raw_manifest:
        candidate = Path(str(raw_manifest))
        if not candidate.is_absolute():
            candidate = (root / candidate).resolve()
        manifest = candidate
    return Campaign(
        id=str(data["id"]),
        root=root,
        objective_text=(root / data["objective"]).read_text(encoding="utf-8"),
        runtime=data["runtime"],
        model=model,
        opencode_version=str(data["opencode_version"]),
        seed=seed,
        worktree=worktree,
        worktree_commit=commit,
        accept=[str(part) for part in data["accept"]],
        checker=checker,
        max_rounds=int(data["max_rounds"]),
        max_seconds=float(data["max_seconds"]),
        accept_timeout=float(data["accept_timeout_seconds"]),
        verification_manifest=manifest,
        live_launch_limit=live_limit,
        live_deadline_seconds=live_deadline,
    )


def _checker_file(root: Path, accept: list) -> Path:
    for part in accept:
        candidate = Path(str(part))
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError("accept command does not name a checker file")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def _workspace(campaign_dir: Path, spec) -> tuple[Path | None, Path | None, str | None]:
    if isinstance(spec, str):
        return (campaign_dir / spec).resolve(), None, None
    if not isinstance(spec, dict) or spec.get("mode") != "worktree":
        raise ValueError("workspace must be a seed path or a worktree object")
    raw = Path(str(spec["path"]))
    live = raw if raw.is_absolute() else repo_root(campaign_dir) / raw
    commit = str(spec["commit"])
    if not commit:
        raise ValueError("worktree commit is required")
    return None, live.resolve(), commit


def git_common_dir(path: Path) -> Path:
    """Absolute git common directory. Relative git output is resolved from ``path``."""
    raw = _git(path, "rev-parse", "--git-common-dir")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = path / candidate
    return candidate.resolve()


def require_worktree(path: Path, repo: Path, commit: str) -> None:
    path = Path(path).resolve()
    repo = Path(repo).resolve()
    if not path.is_dir():
        raise RuntimeError(f"worktree does not exist: {path}")
    if path == repo or (path / ".git").is_dir():
        raise RuntimeError("refusing the primary checkout")
    if not (path / ".git").is_file():
        raise RuntimeError("not a linked git worktree")
    if _git(path, "rev-parse", "--is-inside-work-tree") != "true":
        raise RuntimeError("not a git worktree")
    if git_common_dir(path) != git_common_dir(repo):
        raise RuntimeError("worktree belongs to a different repository")
    if _git(path, "rev-parse", "HEAD") != commit:
        raise RuntimeError("worktree HEAD is not the pinned commit")


def _git(path: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}") from exc
    return completed.stdout.strip()


def ensure_workspace(seed: Path, live: Path) -> None:
    if live.exists():
        return
    shutil.copytree(seed, live)


@contextmanager
def campaign_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError(f"campaign lock held: {path}") from None
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def repo_root(start: Path) -> Path:
    for parent in (start, *start.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("repository root not found")


LAUNCH_SCHEMA = "live-launch/v0"


def launch_counter_path(run_root: Path) -> Path:
    return Path(run_root) / "live-launch.json"


def initialize_launch_counter(
    run_root: Path,
    campaign_id: str,
    limit: int,
    deadline_seconds: float | None,
) -> Path:
    """Create the campaign launch counter once; preserve usage on resume.

    This file is separate from state.json: the campaign runner holds
    campaign.lock for the whole execution, so the child launcher must never
    touch that lock or the runner's in-memory state. The launcher updates this
    file under live-launch.lock.
    """
    path = launch_counter_path(run_root)
    if path.exists():
        return path
    atomic_write(
        path,
        json.dumps(
            {
                "schema": LAUNCH_SCHEMA,
                "campaign_id": campaign_id,
                "limit": int(limit),
                "deadline_seconds": (
                    float(deadline_seconds)
                    if deadline_seconds is not None
                    else None
                ),
                "used": 0,
                "launches": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return path
