"""Candidate workspace materialization and immutable snapshot freezing.

The agent workspace is a **candidate-only** directory. It is created fresh for
each run and contains only:

* ``candidate.c`` - the defective source the agent repairs;
* ``synth.h`` - the read-only public interface (for reference only; the harness
  always compiles against its own trusted copy);
* ``fixtures/visible/`` - the visible corpus the agent is entitled to see;
* ``README.md`` - scope and constraints.

The held-out corpus and the Oracle source are never copied here. This is an
application-level boundary, not an OS sandbox.

Freezing is what makes certification trustworthy. Once the agent has stopped,
its remaining processes are reaped and the candidate is copied into a read-only
attempt snapshot. The controller then compiles and certifies *only* from that
snapshot, so a late writer cannot change the artifact that received the verdict.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from oracle.slice import corpus

WORKSPACE_CANDIDATE = "candidate.c"
WORKSPACE_HEADER = "synth.h"
WORKSPACE_README = "README.md"

_README = """# Candidate workspace

Repair `candidate.c` so it matches the pinned behaviour evidenced by the visible
fixtures. Use only the Oracle tools (`oracle_inspect`, `oracle_fixtures`,
`oracle_coverage`, `oracle_test`, `oracle_diff`).

* `synth.h` is the read-only public interface.
* `fixtures/visible/` holds the visible fixtures.
* Held-out expectations are not present here and cannot be requested.
"""


class WorkspaceError(RuntimeError):
    """The candidate workspace could not be prepared or frozen."""


@dataclass(frozen=True)
class Workspace:
    root: Path
    candidate_path: Path


@dataclass(frozen=True)
class FrozenCandidate:
    path: Path
    sha256: str
    bytes: int


def _is_plain_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def materialize_workspace(dest: Path, *, candidate_src: Path, header_src: Path,
                          visible_root: Path | None = None) -> Workspace:
    """Create a fresh candidate-only workspace at ``dest``."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise WorkspaceError(f"workspace destination is not empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)

    candidate_src = Path(candidate_src)
    if not _is_plain_file(candidate_src):
        raise WorkspaceError(f"candidate source is not a regular file: {candidate_src}")
    shutil.copyfile(candidate_src, dest / WORKSPACE_CANDIDATE)

    header_src = Path(header_src)
    if not _is_plain_file(header_src):
        raise WorkspaceError(f"public interface is not a regular file: {header_src}")
    shutil.copyfile(header_src, dest / WORKSPACE_HEADER)

    root = Path(visible_root) if visible_root else corpus.FIXTURES_DIR
    visible_dir = root / corpus.VISIBLE
    if not visible_dir.is_dir():
        raise WorkspaceError(f"missing visible fixture directory: {visible_dir}")
    target = dest / "fixtures" / corpus.VISIBLE
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(visible_dir, target)

    (dest / WORKSPACE_README).write_text(_README, encoding="utf-8")
    return Workspace(root=dest, candidate_path=dest / WORKSPACE_CANDIDATE)


def freeze_candidate(src: Path, dest_dir: Path) -> FrozenCandidate:
    """Copy a candidate into a fresh, read-only attempt snapshot."""
    src = Path(src)
    if not _is_plain_file(src):
        raise WorkspaceError(f"candidate to freeze is not a regular file: {src}")
    if src.suffix != ".c":
        raise WorkspaceError(f"candidate to freeze must be a .c file: {src}")
    dest_dir = Path(dest_dir)
    if dest_dir.exists() and any(dest_dir.iterdir()):
        raise WorkspaceError(f"snapshot destination is not empty: {dest_dir}")
    dest_dir.mkdir(parents=True, exist_ok=True)

    data = src.read_bytes()
    snapshot = dest_dir / "candidate.c"
    snapshot.write_bytes(data)
    snapshot.chmod(0o444)
    return FrozenCandidate(path=snapshot, sha256=sha256(data).hexdigest(), bytes=len(data))
