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

import datetime as _datetime
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

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
