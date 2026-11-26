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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPLAY_SOURCES = (
    "renderer/metal/eu4_gfx_frame_replay.mm",
    "renderer/metal/eu4_gfx_frame_replay_cli.mm",
)
_LEGACY_REPLAY_SOURCES = tuple(f"benchmark/{Path(source).name}"
                               for source in REPLAY_SOURCES)
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
_COMPILER_DEP_CACHE: dict[tuple[str, int, str], tuple[list[Path],
                                                      list[tuple[str, int]]]] = {}
_BUILD_ENVIRONMENT_KEYS = (
    "PATH", "HOME", "TMPDIR", "SDKROOT", "MACOSX_DEPLOYMENT_TARGET",
    "ARCHFLAGS", "DEVELOPER_DIR", "LANG", "LC_ALL",
)


def candidate_sources(trusted_root: Path | str, baseline_commit: str) -> tuple[str, ...]:
    """Select one complete replay layout from the authorized baseline tree."""
    layouts = (REPLAY_SOURCES, _LEGACY_REPLAY_SOURCES)
    paths = (*REPLAY_SOURCES, *_LEGACY_REPLAY_SOURCES)
    completed = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(trusted_root), "ls-tree", "-z",
         baseline_commit, "--", *paths],
        capture_output=True, text=True, check=False, timeout=60)
    if completed.returncode != 0:
        raise OSError(f"cannot read pinned replay sources: {completed.stderr.strip()}")
    entries = {}
    for row in filter(None, completed.stdout.split("\0")):
        entry, path = row.split("\t", 1)
        entries[path] = entry
    for sources in layouts:
        if entries.keys() == set(sources) and all(
                entry.startswith(("100644 blob ", "100755 blob ")) for entry in entries.values()):
            return sources
    raise OSError(f"missing, incomplete or ambiguous replay source layout at {baseline_commit}")


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


def build_environment() -> dict[str, str]:
    """Fixed build environment: no unrecorded include/library injection."""
    return {key: os.environ[key] for key in _BUILD_ENVIRONMENT_KEYS
            if key in os.environ}


def _system_dependency(path: Path) -> bool:
    """True for toolchain/SDK files that are covered by compiler identity."""
    roots = ["/usr", "/System", "/Library/Developer", "/Applications",
             "/opt/homebrew", "/opt/local"]
    sdk = os.environ.get("SDKROOT")
    if sdk:
        roots.append(sdk)
    compiler = compiler_identity()["path"]
    if compiler:
        roots.append(str(Path(compiler).parent))
    resolved = path.resolve()
    return any(resolved == Path(root).resolve()
               or Path(root).resolve() in resolved.parents
               for root in roots if Path(root).exists())


def _native_dependency_scan() -> bool:
    return sys.platform == "darwin" and shutil.which("clang") is not None


def _quoted_dependency_paths(source: Path, root: Path, *,
                             strict: bool) -> list[Path]:
    """Portable fallback closure for non-native hosts.

    This is intentionally a quoted-include scan, not a claim of macOS compiler
    provenance.  ``build_identity`` records that distinction explicitly.
    """
    pending = [source]
    closure: list[Path] = []
    seen: set[Path] = set()
    while pending:
        path = pending.pop()
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        closure.append(resolved)
        text = resolved.read_text(encoding="utf-8", errors="replace")
        for match in _LOCAL_INCLUDE.finditer(text):
            include = Path(match.group(1))
            candidate = (resolved.parent / include).resolve()
            if (not include.is_absolute() and candidate.is_file()
                    and _relative(candidate, root) is not None):
                pending.append(candidate)
            elif strict:
                raise OSError(
                    f"unattested source include {match.group(1)!r} in "
                    f"{_relative(resolved, root) or resolved}")
    return closure


def _compiler_dependencies(path: Path, compiler: str | None) -> list[Path]:
    """Ask the compiler for one source's non-system dependency closure."""
    compiler_path = compiler_identity(compiler)["path"] or compiler or "clang"
    try:
        source_mtime = path.stat().st_mtime_ns
    except OSError:
        source_mtime = 0
    key = (str(path.resolve()), source_mtime, compiler_path)
    cached = _COMPILER_DEP_CACHE.get(key)
    if cached is not None:
        dependencies, stamps = cached
        if all(Path(item).is_file() and Path(item).stat().st_mtime_ns == stamp
               for item, stamp in stamps):
            return dependencies
    completed = subprocess.run(
        [compiler_path, "-arch", "arm64", "-fobjc-arc", "-MM", "-MT", "dep",
         str(path)],
        capture_output=True, text=True, check=False, timeout=120,
        env=build_environment())
    if completed.returncode != 0:
        raise OSError(
            (completed.stderr or completed.stdout or
             "compiler dependency scan failed").strip()[-2000:])
    text = completed.stdout.replace("\\\n", " ")
    if ":" not in text:
        raise OSError("compiler dependency output has no rule")
    dependencies = text.split(":", 1)[1]
    result: list[Path] = []
    token = ""
    escaped = False
    for character in dependencies + " ":
        if escaped:
            token += character
            escaped = False
        elif character == "\\":
            escaped = True
        elif character.isspace():
            if token:
                result.append(Path(token))
                token = ""
        else:
            token += character
    stamps = [(str(item), item.stat().st_mtime_ns if item.is_file() else -1)
              for item in result]
    _COMPILER_DEP_CACHE[key] = (result, stamps)
    return result


def dependency_paths(root: Path | str = ROOT,
                     sources=REPLAY_SOURCES,
                     *, strict: bool = False,
                     compiler: str | None = None) -> dict[str, Path]:
    """Return the compiler-reported non-system dependency closure.

    The compiler is the authority: a quoted include that escapes the checkout
    is rejected in strict mode instead of compiling from the host filesystem.
    The optional quoted-include scanner is no longer a separate authority.
    """
    root = Path(root).resolve()
    seen: dict[Path, str] = {}
    for item in sources:
        source = Path(item) if Path(item).is_absolute() else root / item
        if not source.is_file():
            raise OSError(f"replay source is absent: {source}")
        native = _native_dependency_scan()
        if strict and (source.absolute() != source.resolve() or _relative(source, root) is None):
            raise OSError(f"unattested replay source path: {source}")
        quoted = _quoted_dependency_paths(source, root, strict=strict) if strict or not native else []
        deps = _compiler_dependencies(source, compiler) if native else quoted
        for dependency in [source, *deps]:
            try:
                resolved = dependency.resolve()
            except OSError:
                continue
            relative = _relative(resolved, root)
            if relative is None:
                if _system_dependency(resolved):
                    continue
                if strict:
                    raise OSError(
                        "unattested source dependency outside the checkout: "
                        f"{resolved}")
                continue
            if not resolved.is_file():
                if strict:
                    raise OSError(f"source dependency is absent: {resolved}")
                continue
            seen[resolved] = relative
    return {relative: path for path, relative in sorted(
        seen.items(), key=lambda item: item[1])}


def relative_dependencies(root: Path | str = ROOT,
                          sources=REPLAY_SOURCES, *, strict: bool = False,
                          compiler: str | None = None) -> dict[str, str]:
    """Hash the authoritative dependency closure."""
    return {relative: _sha256(path)
            for relative, path in dependency_paths(
                root, sources, strict=strict, compiler=compiler).items()}


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
                   flags=None, *, compiler: str | None = None,
                   strict: bool = False) -> dict:
    """Hash all inputs that can change the replay-host binary."""
    flags = BUILD_FLAGS if flags is None else flags
    root = Path(root).resolve()
    units = tuple(Path(source) for source in sources)
    dependencies = relative_dependencies(root, units, strict=strict,
                                         compiler=compiler)
    environment = build_environment()
    value = {
        "schema": "gfx-replay-host-build-identity/v1",
        "platform": sys.platform,
        "dependency_authority": ("compiler" if _native_dependency_scan()
                                 else "quoted-include"),
        "sources": [str(unit.relative_to(root) if unit.is_relative_to(root) else unit)
                    for unit in units],
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
    dependencies = dependency_paths(root, sources, strict=True)
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
                        *, sources=REPLAY_SOURCES, compiler: str | None = None,
                        timeout: float = 180.0,
                        environment: dict | None = None) -> dict:
    """Build the replay host from an isolated candidate source snapshot."""
    snapshot_root = Path(snapshot_root).resolve()
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    identity = build_identity(snapshot_root, sources, compiler=compiler, strict=True)
    compiler_info = compiler_identity(compiler)
    command = build_command(output, [snapshot_root / relative for relative in sources],
                            compiler=compiler)
    build_env = environment or build_environment()
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False,
        timeout=timeout, env=build_env)
    if completed.returncode != 0:
        raise OSError(
            (completed.stderr or completed.stdout or
             "replay host build failed").strip()[-4000:])
    if identity != build_identity(snapshot_root, sources, compiler=compiler, strict=True):
        raise OSError("snapshot dependencies changed during replay host build")
    return {
        "schema": "gfx-replay-host-candidate-build/v1",
        "command": command,
        "compiler": compiler_info,
        "flags": list(BUILD_FLAGS),
        "environment": build_env,
        "source_root": str(snapshot_root),
        "source_files": sorted(identity["dependencies"]),
        "source_digests": identity["dependencies"],
        "build_fingerprint": identity["fingerprint"],
        "host_path": str(output),
        "host_sha256": _sha256(output),
        "stderr": (completed.stderr or "").strip()[-4000:],
    }
