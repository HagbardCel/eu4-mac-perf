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
