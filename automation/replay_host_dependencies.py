"""Authoritative dependency enumeration for the replay host.

The replay host is built from two Objective-C++ translation units.  Both the
incremental build decision and the baseline identity must use the same
dependency closure: the two compilation units plus every local header they
include (directly or transitively).  This module is deliberately only an
enumerator plus a build-identity fingerprint; it does not introduce a second
build system.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPLAY_SOURCES = (
    "benchmark/eu4_gfx_frame_replay.mm",
    "benchmark/eu4_gfx_frame_replay_cli.mm",
)
BUILD_FLAGS = (
    "-arch",
    "arm64",
    "-O2",
    "-Wall",
    "-Wextra",
    "-fobjc-arc",
    "-lc++",
    "-framework",
    "Foundation",
    "-framework",
    "Metal",
)
# Environment variables that can change the produced binary.
BUILD_ENV_KEYS = (
    "SDKROOT",
    "MACOSX_DEPLOYMENT_TARGET",
    "ARCHFLAGS",
    "DEVELOPER_DIR",
)
_LOCAL_INCLUDE = re.compile(
    r'^\s*#\s*(?:include|import)\s+"([^"]+)"', re.MULTILINE)
_COMPILER_CACHE: dict[tuple[str, int], dict] = {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def dependency_paths(root: Path | str = ROOT,
                     sources=REPLAY_SOURCES) -> dict[str, Path]:
    """Return the source/header closure keyed by checkout-relative path."""
    root = Path(root).resolve()
    bench = root / "benchmark"
    seen: dict[Path, str] = {}
    pending = [Path(item) if Path(item).is_absolute() else root / item
               for item in sources]
    while pending:
        path = pending.pop()
        try:
            path = path.resolve()
        except OSError:
            continue
        if path in seen or not path.is_file():
            continue
        relative = _relative(path, root)
        if relative is None:
            continue
        seen[path] = relative
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _LOCAL_INCLUDE.finditer(text):
            candidate = (path.parent / match.group(1)).resolve()
            if not candidate.is_file():
                candidate = (bench / match.group(1)).resolve()
            if candidate.is_file() and _relative(candidate, root) is not None:
                pending.append(candidate)
    return {relative: path for path, relative in sorted(
        seen.items(), key=lambda item: item[1])}


def relative_dependencies(root: Path | str = ROOT,
                          sources=REPLAY_SOURCES) -> dict[str, str]:
    """Hash the authoritative dependency closure."""
    return {relative: _sha256(path)
            for relative, path in dependency_paths(root, sources).items()}


def compiler_identity(compiler: str | None = None) -> dict:
    """Path/version of the compiler that would build the host."""
    path = shutil.which(compiler or "clang")
    if not path:
        return {"path": None, "version": None}
    try:
        stat = Path(path).stat()
        key = (path, stat.st_mtime_ns)
    except OSError:
        key = (path, 0)
    if key in _COMPILER_CACHE:
        return dict(_COMPILER_CACHE[key])
    try:
        completed = subprocess.run(
            [path, "--version"], capture_output=True, text=True, check=False,
            timeout=30)
        version = (completed.stdout or completed.stderr or "").strip()
    except (OSError, subprocess.SubprocessError):
        version = None
    value = {"path": path, "version": version}
    _COMPILER_CACHE[key] = value
    return dict(value)


def build_command(output: Path | str, sources=REPLAY_SOURCES,
                  *, compiler: str | None = None) -> list[str]:
    compiler_path = compiler_identity(compiler)["path"] or compiler or "clang"
    source_paths = [str(Path(item) if Path(item).is_absolute()
                        else Path(ROOT) / item) for item in sources]
    return [compiler_path, *BUILD_FLAGS, "-o", str(output), *source_paths]


def build_identity(root: Path | str = ROOT, sources=REPLAY_SOURCES,
                   flags=None, *, compiler: str | None = None) -> dict:
    """Hash all inputs that can change the replay-host binary."""
    flags = BUILD_FLAGS if flags is None else flags
    root = Path(root).resolve()
    dependencies = relative_dependencies(root, sources)
    environment = {key: os.environ.get(key)
                   for key in BUILD_ENV_KEYS if os.environ.get(key) is not None}
    value = {
        "schema": "gfx-replay-host-build-identity/v1",
        "dependencies": dependencies,
        "compiler": compiler_identity(compiler),
        "flags": list(flags),
        "environment": environment,
    }
    value["fingerprint"] = hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return value


def snapshot_sources(root: Path | str, destination: Path | str,
                     sources=REPLAY_SOURCES) -> dict:
    """Copy the authoritative dependency closure into an isolated snapshot.

    Only the two replay translation units and the local headers they include
    are copied.  The returned identity is computed from the snapshot bytes, so
    a later change to the candidate worktree cannot change what was built.
    """
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    if destination.exists():
        shutil.rmtree(destination)
    dependencies = dependency_paths(root, sources)
    if not dependencies:
        raise OSError("candidate renderer dependency closure is empty")
    for relative, path in dependencies.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return {
        "root": str(destination),
        "files": sorted(dependencies),
        "digests": {relative: _sha256(destination / relative)
                    for relative in sorted(dependencies)},
    }


def build_snapshot_host(snapshot_root: Path | str, output: Path | str,
                        *, compiler: str | None = None,
                        timeout: float = 180.0,
                        environment: dict | None = None) -> dict:
    """Build the replay host from an isolated candidate source snapshot."""
    snapshot_root = Path(snapshot_root).resolve()
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    sources = [snapshot_root / relative for relative in REPLAY_SOURCES]
    if any(not source.is_file() for source in sources):
        raise OSError("snapshot is missing a replay translation unit")
    compiler_info = compiler_identity(compiler)
    compiler_path = compiler_info["path"] or compiler or "clang"
    command = [compiler_path, *BUILD_FLAGS, "-o", str(output),
               *map(str, sources)]
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False,
        timeout=timeout, env=environment)
    if completed.returncode != 0:
        raise OSError(
            (completed.stderr or completed.stdout or
             "replay host build failed").strip()[-4000:])
    identity = build_identity(snapshot_root, sources=REPLAY_SOURCES,
                              compiler=compiler)
    return {
        "schema": "gfx-replay-host-candidate-build/v1",
        "command": command,
        "compiler": compiler_info,
        "flags": list(BUILD_FLAGS),
        "environment": {key: os.environ.get(key)
                        for key in BUILD_ENV_KEYS
                        if os.environ.get(key) is not None},
        "source_root": str(snapshot_root),
        "source_files": sorted(identity["dependencies"]),
        "source_digests": identity["dependencies"],
        "build_fingerprint": identity["fingerprint"],
        "host_path": str(output),
        "host_sha256": _sha256(output),
        "stderr": (completed.stderr or "").strip()[-4000:],
    }
