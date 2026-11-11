"""Content-addressed candidate deltas, for both backends.

``automation.gfx_draw_accept.changed_paths`` returns names only: the porcelain
status letter is discarded, so a deletion, a mode change and a rename all look
like a modification, and ``automation.handoff.snapshot_candidate`` can only
discover one filename under ``analysis/qualification``. A DSH reconstruction
candidate may add, delete, rename, chmod or retype anything its policy allows,
so the delta has to be enumerated, validated and reproduced faithfully.

Nothing here decides whether a candidate is *good*; it decides whether the
snapshot the evidence refers to is exactly the change that was on disk.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

SCHEMA = "eu4-candidate-snapshot/v1"
KINDS = ("add", "modify", "delete", "rename", "typechange")
MODE_FILE = "100644"
MODE_EXEC = "100755"
MODE_LINK = "120000"
_STATUS_KINDS = {
    "A": {"add"},
    "D": {"delete"},
    "M": {"modify"},
    "T": {"modify", "typechange"},
    "R": {"rename"},
    "C": {"add", "rename"},
}


class SnapshotError(RuntimeError):
    """A delta that cannot be represented faithfully."""


def _git(repo: Path, *args: str, env: dict | None = None, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if check and completed.returncode != 0:
        raise SnapshotError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout


def _status_kind(status: str) -> str:
    if status == "?" or status == "??":
        return "add"
    if "D" in status:
        return "delete"
    if "R" in status:
        return "rename"
    if "T" in status:
        return "typechange"
    if "A" in status:
        return "add"
    return "modify"


def _exec_mode(path: Path) -> str:
    return MODE_EXEC if os.stat(path).st_mode & 0o100 else MODE_FILE


def enumerate_delta(workspace: Path) -> list[dict]:
    """Every difference between the worktree and its HEAD, with its kind.

    Uses porcelain v2 because v1 loses type and mode information: a deletion is
    a missing file, not an error, and a mode change is not a modification.
    """
    raw = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain=v2", "-z",
         "--untracked-files=all", "--ignored=no"],
        capture_output=True,
        check=False,
    )
    if raw.returncode != 0:
        raise SnapshotError(raw.stderr.decode() or "git status failed")

    tokens = raw.stdout.split(b"\0")
    entries: list[dict] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token:
            index += 1
            continue
        text = token.decode("utf-8", "surrogateescape")
        if text.startswith("1 "):
            parts = text.split(" ", 8)
            kind = _status_kind(parts[1])
            path = parts[8]
            entries.append(_entry(workspace, path, kind))
        elif text.startswith("2 "):
            parts = text.split(" ", 9)
            kind = _status_kind(parts[1])
            path = parts[9]
            index += 1
            original = tokens[index].decode("utf-8", "surrogateescape") if index < len(tokens) else ""
            entry = _entry(workspace, path, kind)
            entry["old_path"] = original
            entries.append(entry)
        elif text.startswith("u "):
            parts = text.split(" ", 10)
            raise SnapshotError(f"unmerged path in candidate worktree: {parts[10]}")
        elif text.startswith("? "):
            entries.append(_entry(workspace, text[2:], "add"))
        index += 1
    entries.sort(key=lambda item: item["path"])
    return entries


def _entry(workspace: Path, path: str, kind: str) -> dict:
    target = workspace / path
    symlink = target.is_symlink()
    exists = target.exists() or symlink
    mode = MODE_LINK if symlink else (_exec_mode(target) if exists else None)
    return {
        "path": path,
        "kind": kind,
        "mode": mode,
        "symlink": symlink,
        "exists": exists,
    }


def validate_delta(
    delta: list[dict],
    allowed: tuple[str, ...] | list[str],
    forbidden: tuple[str, ...] | list[str] = (),
    *,
    allow_symlinks: bool = False,
) -> list[str]:
    """Every entry must be permitted; nothing is silently dropped."""
    from automation.dsh_campaign import path_allowed

    problems: list[str] = []
    for entry in delta:
        for path in filter(None, (entry["path"], entry.get("old_path"))):
            if not path_allowed(path, allowed):
                problems.append(f"candidate path outside allowlist: {path}")
        for pattern in forbidden:
            joined = str(pattern).rstrip("/")
            for path in filter(None, (entry["path"], entry.get("old_path"))):
                if path == joined or path.startswith(joined + "/"):
                    problems.append(f"frozen path modified: {path}")
        if entry["symlink"] and not allow_symlinks:
            problems.append(f"symlink not permitted by policy: {entry['path']}")
        if entry["kind"] in KINDS and not entry["exists"] and entry["kind"] != "delete":
            problems.append(f"candidate entry is missing but not a deletion: {entry['path']}")
    return sorted(set(problems))


def _hash_blob(repo: Path, path: Path, env: dict) -> str:
    return _git(repo, "hash-object", "-w", "--", str(path), env=env).strip()


def _hash_bytes(repo: Path, payload: bytes, env: dict) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=payload,
        capture_output=True,
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        raise SnapshotError(completed.stderr.decode() or "hash-object failed")
    return completed.stdout.decode().strip()


DEFAULT_IDENTITY = {
    "GIT_AUTHOR_NAME": "eu4-candidate-snapshot",
    "GIT_AUTHOR_EMAIL": "eu4-candidate-snapshot@local",
    "GIT_COMMITTER_NAME": "eu4-candidate-snapshot",
    "GIT_COMMITTER_EMAIL": "eu4-candidate-snapshot@local",
}


def build_candidate(
    workspace: Path, repo: Path, baseline: str, delta: list[dict],
    message: str = "candidate snapshot",
    identity: dict | None = None,
) -> dict:
    """Materialize exactly *delta* on top of *baseline* as a commit."""
    workspace = Path(workspace)
    repo = Path(repo)
    fd, index_name = tempfile.mkstemp(prefix=f"eu4-dsh-index-{uuid.uuid4().hex[:8]}-")
    os.close(fd)
    index_file = Path(index_name)
    index_file.unlink()
    env = {**os.environ, "GIT_INDEX_FILE": str(index_file)}
    touched: list[str] = []
    try:
        _git(repo, "read-tree", baseline, env=env)
        for entry in delta:
            path = entry["path"]
            touched.append(path)
            if entry["kind"] == "delete":
                _git(repo, "update-index", "--force-remove", "--", path,
                     env=env, check=False)
                continue
            if entry["kind"] == "rename" and entry.get("old_path"):
                # A rename is a delete plus an add once materialized.
                _git(repo, "update-index", "--force-remove", "--",
                     entry["old_path"], env=env, check=False)
            source = workspace / path
            if entry["symlink"]:
                blob = _hash_bytes(repo, os.readlink(source).encode("utf-8"), env)
                mode = MODE_LINK
            else:
                if not source.is_file():
                    raise SnapshotError(f"candidate file disappeared: {path}")
                blob = _hash_blob(repo, source, env)
                mode = _exec_mode(source)
            _git(repo, "update-index", "--add", "--cacheinfo",
                 f"{mode},{blob},{path}", env=env)
        tree = _git(repo, "write-tree", env=env).strip()
        authorship = {**os.environ, **(identity or DEFAULT_IDENTITY)}
        commit = subprocess.run(
            ["git", "-C", str(repo), "commit-tree", tree, "-p", baseline, "-m", message],
            capture_output=True, text=True, check=False, env=authorship,
        )
        if commit.returncode != 0:
            raise SnapshotError(commit.stderr.strip() or "commit-tree failed")
    finally:
        if index_file.exists():
            index_file.unlink()
    return {
        "schema": SCHEMA,
        "baseline": baseline,
        "candidate": commit.stdout.strip(),
        "tree": tree,
        "paths": touched,
    }


def raw_diff(repo: Path, base: str, candidate: str) -> list[dict]:
    """Paths, modes and status letters between two commits (rename-aware)."""
    raw = subprocess.run(
        ["git", "-C", str(repo), "diff-tree", "-r", "-z", "--no-commit-id",
         "--raw", "--find-renames", base, candidate],
        capture_output=True,
        check=False,
    )
    if raw.returncode != 0:
        raise SnapshotError(raw.stderr.decode() or "diff-tree failed")
    tokens = raw.stdout.split(b"\0")
    rows: list[dict] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith(b":"):
            index += 1
            continue
        # ":<oldmode> <newmode> <oldsha> <newsha> <status>" -- the colon is part
        # of the first token, so the status is index 4, not 5.
        header = token.decode().split()
        old_mode, new_mode = header[0][1:], header[1]
        status = header[4]
        letter = status[0]
        if letter in ("R", "C"):
            old_path = tokens[index + 1].decode("utf-8", "surrogateescape")
            new_path = tokens[index + 2].decode("utf-8", "surrogateescape")
            rows.append({"path": new_path, "old_path": old_path, "status": letter,
                         "old_mode": old_mode, "new_mode": new_mode})
            index += 3
            continue
        path = tokens[index + 1].decode("utf-8", "surrogateescape")
        rows.append({"path": path, "old_path": None, "status": letter,
                     "old_mode": old_mode, "new_mode": new_mode})
        index += 2
    return rows


def verify_round_trip(
    repo: Path, baseline: str, candidate: str, delta: list[dict]
) -> list[str]:
    """The built commit must reproduce *exactly* the intended delta."""
    rows = raw_diff(repo, baseline, candidate)
    problems: list[str] = []

    expected: dict[str, dict] = {}
    for entry in delta:
        expected[entry["path"]] = entry
        if entry.get("old_path"):
            expected[entry["old_path"]] = {"path": entry["old_path"], "alias": True}

    actual: dict[str, dict] = {}
    for row in rows:
        actual[row["path"]] = row
        if row.get("old_path"):
            actual[row["old_path"]] = row

    for path in expected:
        if path not in actual:
            problems.append(f"snapshot is missing an intended change: {path}")
    for path in actual:
        if path not in expected:
            problems.append(f"snapshot contains an unintended change: {path}")

    for path, entry in expected.items():
        if entry.get("alias"):
            continue
        row = actual.get(path)
        if row is None:
            continue
        allowed = _STATUS_KINDS.get(row["status"], set())
        if entry["kind"] not in allowed:
            problems.append(
                f"snapshot reproduced {path} as {row['status']}, intended {entry['kind']}"
            )
        if entry["kind"] != "delete" and entry.get("mode"):
            if row["new_mode"] != entry["mode"]:
                problems.append(
                    f"snapshot mode for {path} is {row['new_mode']}, intended {entry['mode']}"
                )
    return sorted(set(problems))


IGNORED_FILE_LIMIT = 2000
IGNORED_BYTE_LIMIT = 64 * 1024 * 1024


def ignored_inputs(workspace: Path) -> dict:
    """Content digests for ignored files a checker might read.

    Evidence, not policy: the campaign allowlist governs what the agent may
    change, while this records the ignored state the checker actually saw.

    Git reports an entirely ignored directory as one record (``!! .local/``), so
    directories are expanded here. The walk is bounded -- a renderer workspace
    holds large build and evidence trees, and hashing all of it would make every
    validation expensive -- and says so when it stops early.
    """
    import hashlib
    import os

    try:
        listing = _git(
            workspace, "status", "--porcelain=v1", "-z",
            "--untracked-files=all", "--ignored=matching",
        )
    except Exception:  # noqa: BLE001 - a seed workspace is not a repository
        return {"entries": [], "truncated": False}

    roots: list[Path] = []
    for record in listing.split("\0"):
        if record.startswith("!! "):
            roots.append(workspace / record[3:].rstrip("/"))

    rows: list[dict] = []
    truncated = False
    total = 0
    for root in sorted(set(roots)):
        if root.is_symlink():
            rows.append({
                "path": str(root.relative_to(workspace)),
                "target_sha256": hashlib.sha256(
                    os.readlink(root).encode("utf-8", "surrogateescape")
                ).hexdigest(),
            })
            continue
        if root.is_file():
            rows.append({
                "path": str(root.relative_to(workspace)),
                "sha256": hashlib.sha256(root.read_bytes()).hexdigest(),
            })
            continue
        if not root.is_dir():
            continue
        for directory, subdirectories, files in os.walk(root):
            subdirectories[:] = [
                name for name in subdirectories
                if name != ".git"
            ]
            for name in sorted(files):
                path = Path(directory) / name
                if len(rows) >= IGNORED_FILE_LIMIT or total >= IGNORED_BYTE_LIMIT:
                    truncated = True
                    break
                if path.is_symlink():
                    rows.append({
                        "path": str(path.relative_to(workspace)),
                        "target_sha256": hashlib.sha256(
                            os.readlink(path).encode("utf-8", "surrogateescape")
                        ).hexdigest(),
                    })
                    continue
                if not path.is_file():
                    continue
                size = path.stat().st_size
                total += size
                rows.append({
                    "path": str(path.relative_to(workspace)),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size": size,
                })
            if truncated:
                break
        if truncated:
            break
    rows.sort(key=lambda row: row["path"])
    return {"entries": rows, "truncated": truncated}


def ignored_digest(workspace: Path) -> str:
    """Stable digest of the ignored-input inventory."""
    import hashlib
    import json

    payload = json.dumps(ignored_inputs(workspace), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint(workspace: Path) -> dict:
    """Cheap identity of the working tree, to detect edits during a snapshot."""
    rows = []
    for current, dirs, files in os.walk(workspace):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in sorted(files):
            path = Path(current) / name
            if path.is_symlink():
                rows.append([path.relative_to(workspace).as_posix(), "link",
                             os.readlink(path)])
                continue
            stat = path.stat()
            rows.append([path.relative_to(workspace).as_posix(), "file",
                         stat.st_size, stat.st_mtime_ns, stat.st_mode])
    rows.sort(key=lambda row: row[0])
    digest = hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()
    return {"digest": digest, "entries": len(rows)}


def recheck(workspace: Path, expected: dict) -> list[str]:
    actual = fingerprint(workspace)
    if actual["digest"] != expected.get("digest"):
        return ["the candidate worktree changed while the snapshot was being built"]
    return []
