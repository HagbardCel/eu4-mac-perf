"""Separate native prerequisites from an eligible native build failure.

The native replay suites used to convert *any* compiler nonzero into
``SkipTest``.  On a host that satisfies the platform, tool and framework
requirements that silently removed whole classes of assertions, including ABI,
receipt, lifetime and blob-verifier coverage.  A portable executed-test floor
could still pass on unrelated tests, so a syntax error in native code was not
visible.

This module keeps the two decisions distinct:

* :func:`require_prerequisites` skips only when a declared platform, tool,
  source or framework prerequisite is genuinely absent, with a reason;
* :func:`build` runs the compiler after those prerequisites and **fails** the
  suite on a nonzero exit, a timeout or an execution error, reporting argv,
  status, bounded compiler output and the full log path.

:func:`require_build` is the shared entry point the suites use: probe first,
then compile, so no suite duplicates the skip/failure decision.  ``build`` and
``require_build`` accept an injectable ``runner`` so the decision can be tested
without a real compiler.

The module is deliberately small and shared.  It does not probe for
capabilities a suite does not use, and it never turns a compile failure,
timeout or execution error into a skip.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from automation.gfx_artifacts import sha256_file

ROOT = Path(__file__).resolve().parents[1]
LOG_ROOT = ROOT / ".local" / "native-builds"
MAX_OUTPUT = 4000

Runner = Callable[..., subprocess.CompletedProcess]


def _framework_present(name: str) -> bool:
    return Path(f"/System/Library/Frameworks/{name}.framework").exists()


def require_prerequisites(*, sources=(), tools=("clang",), frameworks=(),
                          require_arm64: bool = True,
                          require_macos: bool = True,
                          reason: str = "") -> None:
    """Skip only for a declared, genuinely absent prerequisite.

    ``sources`` are repository inputs the host cannot synthesize.  A missing
    source is a fixture/dependency gap, not a compiler failure.  Once this
    function returns, the caller is entitled to assert that the build works.

    ``require_arm64`` is opt-in: a suite whose host is architecture-independent
    (for example a pure OpenGL trace host) passes ``False`` so an Intel macOS
    host still executes it.  Suites that build and run an ``-arch arm64`` Metal
    binary keep the default.
    """
    if require_macos and sys.platform != "darwin":
        raise unittest.SkipTest(
            reason or "the native Metal/OpenGL host requires macOS")
    if require_arm64 and platform.machine().lower() not in ("arm64", "aarch64"):
        raise unittest.SkipTest(
            reason or "the native host requires arm64")
    for tool in tools:
        if shutil.which(tool) is None:
            raise unittest.SkipTest(
                f"required native tool is unavailable: {tool}")
    for source in sources:
        path = Path(source)
        if not path.is_file():
            raise unittest.SkipTest(
                f"required native source is unavailable: {path}")
    for framework in frameworks:
        if not _framework_present(framework):
            raise unittest.SkipTest(
                f"required macOS framework is unavailable: {framework}")


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _bounded(text: Any, limit: int = MAX_OUTPUT) -> str:
    value = _coerce_text(text).strip()
    if len(value) > limit:
        value = value[:limit] + " …[truncated]"
    return value


def _write_log(label: str, argv, *, returncode: int | None,
               stdout: Any = "", stderr: Any = "",
               note: str = "") -> Path:
    """Write the full build evidence and return its path.

    Reporting must never hide the build failure itself, so an unwritable log
    degrades to the log root instead of masking the failure.
    """
    try:
        LOG_ROOT.mkdir(parents=True, exist_ok=True)
        stamp = _datetime.datetime.now(_datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ")
        safe = "".join(character if character.isalnum() or character in "-_"
                       else "-" for character in label)[:80]
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=f"{stamp}-{safe}-",
            suffix=".log", dir=LOG_ROOT, delete=False)
        with handle:
            handle.write(f"label: {label}\n")
            handle.write("argv: " + repr([str(item) for item in argv]) + "\n")
            handle.write(
                f"exit: {'not-run' if returncode is None else returncode}\n")
            if note:
                handle.write(f"note: {note}\n")
            handle.write("stdout:\n" + _coerce_text(stdout) + "\n")
            handle.write("stderr:\n" + _coerce_text(stderr) + "\n")
        return Path(handle.name)
    except OSError:
        return LOG_ROOT


def _failure_detail(completed: subprocess.CompletedProcess) -> tuple[str, str]:
    stderr = _bounded(getattr(completed, "stderr", ""))
    if stderr:
        return "stderr", stderr
    stdout = _bounded(getattr(completed, "stdout", ""))
    if stdout:
        return "stdout", stdout
    return "stderr", "<empty>"


def build(argv, *, label: str, timeout: float = 300.0,
          runner: Runner | None = None) -> subprocess.CompletedProcess:
    """Run one native build and fail on a nonzero exit, timeout or exec error.

    The suite has already established its prerequisites.  A failure here is a
    real, actionable regression: it is raised as ``AssertionError`` so the
    suite is reported failed (never skipped), with bounded compiler output and
    the full log path.

    ``runner`` defaults to :func:`subprocess.run`.  Tests inject a fake runner
    to exercise this decision without a compiler.
    """
    command = [str(item) for item in argv]
    if runner is None:
        runner = subprocess.run
    try:
        completed = runner(command, capture_output=True, text=True,
                           check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        log = _write_log(label, command, returncode=None,
                         stdout=getattr(exc, "stdout", ""),
                         stderr=getattr(exc, "stderr", ""),
                         note=f"timed out after {timeout:g}s")
        raise AssertionError(
            f"eligible native build timed out after {timeout:g}s: {label}; "
            f"argv={command}; stderr="
            f"{_bounded(getattr(exc, 'stderr', '')) or '<empty>'}; log={log}"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        log = _write_log(label, command, returncode=None,
                         note=f"execution error: "
                              f"{exc.__class__.__name__}: {_coerce_text(exc)}")
        raise AssertionError(
            f"eligible native build could not run: {label}; argv={command}; "
            f"error={exc.__class__.__name__}: {_bounded(str(exc))}; "
            f"log={log}"
        ) from exc
    if completed.returncode != 0:
        log = _write_log(label, command, returncode=completed.returncode,
                         stdout=getattr(completed, "stdout", ""),
                         stderr=getattr(completed, "stderr", ""))
        field, detail = _failure_detail(completed)
        raise AssertionError(
            f"eligible native build failed: {label} (exit "
            f"{completed.returncode}); argv={command}; "
            f"{field}={detail}; log={log}")
    return completed


def require_build(argv, *, label: str, sources=(), tools=("clang",),
                  frameworks=(), require_arm64: bool = True,
                  require_macos: bool = True, reason: str = "",
                  timeout: float = 300.0,
                  runner: Runner | None = None
                  ) -> subprocess.CompletedProcess:
    """Probe declared prerequisites, then run the build assertion.

    This is the shared suite entry point.  Keeping both steps here means every
    suite makes the same skip/failure decision and no suite can accidentally
    wrap an eligible failure in ``SkipTest``.
    """
    require_prerequisites(
        sources=sources, tools=tools, frameworks=frameworks,
        require_arm64=require_arm64, require_macos=require_macos,
        reason=reason)
    return build(argv, label=label, timeout=timeout, runner=runner)


# The reviewed build outputs are pinned here for deployment integrity.
# gfx_gl_control_authorities separately controls which receipts are trusted.
_GL_DRIVER_SHA256 = "153fd9647236bde7eb1b60d91074fa0f3370d22782d62c5e88718f79d6cb3feb"
_GL_EXECUTABLE_SHA256 = "092a3cdb9075974116fbf8458e7375b83e04d619872a23f1323744a3509cb617"
_GL_SOURCE_SHA256 = "9cbb09f45f4e455887e810c4827e50baec93869093a04cb08e7b4467255ed1ae"
_GL_DRIVER_SUFFIX = b"\n# R2b authority for the UUID-linked macOS OpenGL executor.\n"
_GL_DRIVER = "gfx_gl_control_renderer.py"
_GL_EXECUTABLE = "eu4_gfx_draw_control"
_GL_CLANG = "Apple clang version 21.0.0 (clang-2100.4.1.1)"
_GL_SDK_BUILD = "26B118"


def _gl_control_paths(directory: Path) -> dict[str, str]:
    driver = directory / _GL_DRIVER
    executable = directory / _GL_EXECUTABLE
    for path, expected in ((driver, _GL_DRIVER_SHA256), (executable, _GL_EXECUTABLE_SHA256)):
        if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"OpenGL authority has an unexpected build identity at {path}")
    if not os.access(executable, os.X_OK):
        raise ValueError(f"OpenGL authority executable has no execute permission: {executable}")
    return {"renderer": str(driver), "executable": str(executable)}


def deploy_gl_control() -> dict[str, str]:
    """Build and publish the reviewed UUID-bearing selected-draw artifacts."""
    parent = ROOT / ".local/native-builds/gl-control"
    if parent.resolve() != parent:
        raise ValueError(f"OpenGL authority deployment parent contains a symlink: {parent}")
    destination = parent / _GL_DRIVER_SHA256
    if destination.is_symlink():
        raise ValueError(f"OpenGL authority deployment is a symlink: {destination}")
    if destination.exists():
        return _gl_control_paths(destination)

    def xcrun(*args: str) -> str:
        return subprocess.check_output(
            ["xcrun", "--sdk", "macosx", *args], text=True, timeout=30,
            stderr=subprocess.PIPE).strip()

    source = ROOT / "renderer/gl/eu4_gfx_draw_control.c"
    if sha256_file(source) != _GL_SOURCE_SHA256:
        raise ValueError("OpenGL authority native source does not match the reviewed digest")
    clang = xcrun("--find", "clang")
    version = subprocess.check_output([clang, "--version"], text=True, timeout=30)
    sdk_build = xcrun("--show-sdk-build-version")
    if not version.startswith(_GL_CLANG + "\n") or sdk_build != _GL_SDK_BUILD:
        raise ValueError(f"OpenGL authority requires {_GL_CLANG} and SDK build {_GL_SDK_BUILD}")
    sdk = xcrun("--show-sdk-path")
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".build-", dir=parent) as temporary:
        stage = Path(temporary) / "install"
        stage.mkdir()
        (stage / _GL_DRIVER).write_bytes(
            (ROOT / "automation" / _GL_DRIVER).read_bytes() + _GL_DRIVER_SUFFIX)
        build([clang, "-target", "arm64-apple-macos27.0", "-isysroot", sdk,
               "-O2", "-g0", "-w", "-o", stage / _GL_EXECUTABLE, source,
               "-framework", "OpenGL"], label="gl-control-authority")
        _gl_control_paths(stage)
        try:
            stage.rename(destination)
        except OSError:
            # A concurrent deployment can only be reused after verification.
            if destination.is_symlink() or not destination.is_dir():
                raise
        return _gl_control_paths(destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy a pinned native build")
    parser.add_argument("target", choices=("gl-control",))
    parser.parse_args(argv)
    try:
        print(json.dumps(deploy_gl_control(), indent=2))
    except (AssertionError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"OpenGL authority deployment refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
