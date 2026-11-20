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
  suite on a nonzero exit, reporting argv, status, bounded stderr and the full
  log path.

It is deliberately small and shared.  It does not probe for capabilities a
suite does not use, and it never turns a compile failure into a skip.
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

ROOT = Path(__file__).resolve().parents[1]
LOG_ROOT = ROOT / ".local" / "native-builds"


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


def _write_log(label: str, argv, completed: subprocess.CompletedProcess) -> Path:
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
            handle.write("argv: " + repr([str(item) for item in argv]) + "\n")
            handle.write(f"exit: {completed.returncode}\n")
            handle.write("stdout:\n" + (completed.stdout or "") + "\n")
            handle.write("stderr:\n" + (completed.stderr or "") + "\n")
        return Path(handle.name)
    except OSError:
        # Reporting must never hide the build failure itself.
        return LOG_ROOT


def build(argv, *, label: str, timeout: float = 300.0
          ) -> subprocess.CompletedProcess:
    """Run one native build and fail on a nonzero exit.

    The suite has already established its prerequisites.  A failure here is a
    real, actionable regression: it is raised as ``AssertionError`` so the
    suite is reported failed (never skipped), with bounded compiler output.
    """
    completed = subprocess.run(
        [str(item) for item in argv], capture_output=True, text=True,
        check=False, timeout=timeout)
    if completed.returncode != 0:
        log = _write_log(label, argv, completed)
        stderr = (completed.stderr or "").strip()
        if len(stderr) > 4000:
            stderr = stderr[:4000] + " …[truncated]"
        raise AssertionError(
            f"eligible native build failed: {label} (exit "
            f"{completed.returncode}); argv={[str(item) for item in argv]}; "
            f"stderr={stderr or '<empty>'}; log={log}")
    return completed
