#!/usr/bin/env python3
"""Check one worktree candidate against the trusted RE2 reference.

The differential module is loaded from the repository that contains this file.
Inherited ``PYTHONPATH`` entries and the candidate worktree are not searched.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ALLOWED = {
    "analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/gfx_set_constant_buffer.cpp",
    "analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/gfx_set_constant_buffer.hpp",
    "progress.md",
}
RECON = Path("analysis/reconstruction/_GfxSetConstantBuffer/reconstruction")
FLAGS = [
    "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-Werror",
    "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
]


def trusted_root() -> Path:
    start = Path(__file__).resolve().parent
    for parent in (start, *start.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("trusted repository not found")


def emit(verdict: str, diagnostics: str) -> dict:
    return {"verdict": verdict, "diagnostics": diagnostics}


def _blocked(entry: str, workspace: Path) -> bool:
    if not entry:
        return True
    try:
        resolved = Path(entry).resolve()
    except OSError:
        return True
    return resolved == workspace or workspace in resolved.parents


def import_diff(workspace: Path):
    """Load reconstruction_diff from the campaign repository only."""
    root = trusted_root()
    workspace = workspace.resolve()
    sys.path = [str(root), *[entry for entry in sys.path if not _blocked(entry, workspace)]]
    for name in list(sys.modules):
        if name == "reverse_engineering" or name.startswith("reverse_engineering."):
            del sys.modules[name]
    import reverse_engineering.reconstruction_diff as diff
    loaded = Path(diff.__file__).resolve()
    if root not in loaded.parents or workspace in loaded.parents:
        raise RuntimeError(f"reconstruction_diff loaded from {loaded}")
    return diff


def compare_result(action) -> dict:
    try:
        failures = action()
    except subprocess.TimeoutExpired as exc:
        return emit("fail", f"scenario harness timed out: {exc}")
    if failures:
        sample = "; ".join(str(item) for item in failures[:8])
        suffix = f" ({len(failures)} total)" if len(failures) > 8 else ""
        return emit("fail", sample + suffix)
    return emit("pass", "reference validated and candidate matched")


def reference_gate(diff) -> dict | None:
    try:
        corpus = diff.load_corpus()
        reference = diff.load_reference()
        problems = diff.validate_reference(reference, corpus)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return emit("unavailable", f"reference unavailable: {exc}")
    if problems:
        return emit("unavailable", "; ".join(str(item) for item in problems[:8]))
    return None


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True)
    if completed.returncode != 0:
        raise OSError(completed.stderr.strip() or "git failed")
    return completed.stdout.strip()


def _common_dir(path: Path) -> Path:
    raw = _git(path, "rev-parse", "--git-common-dir")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = path / candidate
    return candidate.resolve()


def changed_paths(workspace: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True,
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr.decode() or "git status failed")
    parts = completed.stdout.split(b"\0")
    found: list[str] = []
    index = 0
    while index < len(parts):
        entry = parts[index]
        if not entry:
            break
        status = entry[:2].decode()
        found.append(entry[3:].decode())
        index += 1
        if "R" in status or "C" in status:
            if index < len(parts) and parts[index]:
                found.append(parts[index].decode())
                index += 1
    return found


def _pinned() -> tuple[str, Path]:
    data = json.loads((Path(__file__).resolve().parent / "campaign.json").read_text())
    return str(data["workspace"]["commit"]), trusted_root()


def _compiler() -> str | None:
    for name in ("c++", "clang++", "g++"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _compile(workspace: Path, binary: Path) -> tuple[bool, str]:
    compiler = _compiler()
    if compiler is None:
        return False, ""
    source = workspace / RECON
    command = [
        compiler, *FLAGS,
        str(source / "gfx_set_constant_buffer.cpp"),
        str(source / "harness.cpp"),
        str(source / "arith_literals.cpp"),
        "-o", str(binary),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if completed.returncode != 0:
        return False, (completed.stderr or completed.stdout or "compile failed").strip()
    return True, ""


def check(workspace: Path, commit: str | None = None, repo: Path | None = None, diff=None) -> dict:
    workspace = Path(workspace).resolve()
    file_commit, file_repo = _pinned()
    commit = file_commit if commit is None else commit
    repo = file_repo if repo is None else Path(repo).resolve()
    diff = import_diff(workspace) if diff is None else diff
    try:
        if _git(workspace, "rev-parse", "HEAD") != commit:
            return emit("fail", "worktree HEAD is not the pinned commit")
        if _common_dir(workspace) != _common_dir(repo):
            return emit("fail", "worktree is not this repository")
        illegal = [item for item in changed_paths(workspace) if item not in ALLOWED]
    except OSError as exc:
        return emit("unavailable", f"worktree unavailable: {exc}")
    if illegal:
        return emit("fail", "source scope violated: " + ", ".join(illegal[:8]))
    gate = reference_gate(diff)
    if gate:
        return gate
    if _compiler() is None:
        return emit("unavailable", "no C++ compiler on PATH")
    temporary = Path(tempfile.mkdtemp(prefix="eu4-re2-accept-"))
    try:
        binary = temporary / "harness"
        built, error = _compile(workspace, binary)
        if not built:
            return emit("fail", "candidate failed to compile: " + error[:2000])
        return compare_result(lambda: diff.compare_corpus(binary))
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: accept.py <worktree>", file=sys.stderr)
        return 2
    result = check(Path(args[0]))
    print(json.dumps(result))
    if result["verdict"] == "pass":
        return 0
    if result["verdict"] == "fail":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
