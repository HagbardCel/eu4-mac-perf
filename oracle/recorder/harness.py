"""Build-and-run harness for the P4 synthetic target.

The harness - never the subject - assembles the build. It copies the trusted
sources into a private directory and compiles them there, so the observation
protocol and the ABI cannot be altered by what is being observed. It then runs
the target and returns its canonical transcript.

This is a P4.2 scaffold for a golden-output gate, not the P3 controller: there is
no process-group escalation, no budget ledger and no candidate workspace, because
the subject here is trusted repository code and the transcript is bounded. It
never launches EU IV.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SYNTHETIC_DIR = Path(__file__).resolve().parent / "synthetic"
GOLDEN_PATH = SYNTHETIC_DIR / "golden.txt"

SOURCES = ("driver.c", "synthetic.c")
HEADERS = ("synth.h",)
COMPILE_FLAGS = ("-std=c11", "-O0", "-fno-strict-aliasing")


class HarnessError(RuntimeError):
    """The synthetic target could not be built or run, or spoke a broken transcript."""


def build(build_dir: Path, *, cc: str = "cc", timeout_s: float = 120.0) -> Path:
    """Compile the trusted target into ``build_dir`` and return the executable path."""
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    for name in SOURCES + HEADERS:
        shutil.copyfile(SYNTHETIC_DIR / name, build_dir / name)
    exe = build_dir / "synth"
    command = [cc, *COMPILE_FLAGS, *SOURCES, "-o", exe.name]
    try:
        proc = subprocess.run(command, cwd=str(build_dir), capture_output=True, timeout=timeout_s)
    except FileNotFoundError as exc:
        raise HarnessError(f"no usable C compiler {cc!r}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(f"compilation timed out after {timeout_s}s") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise HarnessError(f"compilation failed ({proc.returncode}): {detail}")
    return exe


def run_golden(exe: Path, *, timeout_s: float = 30.0) -> str:
    """Run a built target and return its transcript."""
    try:
        proc = subprocess.run([str(exe)], capture_output=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(f"synthetic target timed out after {timeout_s}s") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise HarnessError(f"synthetic target exited {proc.returncode}: {detail}")
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HarnessError("transcript is not valid UTF-8") from exc


def golden_transcript() -> str:
    """The committed golden transcript."""
    return GOLDEN_PATH.read_text(encoding="utf-8")


def reproduce(*, cc: str = "cc") -> str:
    """Build in a scratch directory and return the target's transcript."""
    with tempfile.TemporaryDirectory(prefix="oracle-p4-synth-") as tmp:
        exe = build(Path(tmp), cc=cc)
        return run_golden(exe)


def _fields(parts: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in parts:
        key, sep, value = part.partition("=")
        if not sep:
            raise HarnessError(f"transcript field is not key=value: {part!r}")
        fields[key] = value
    return fields


def parse_transcript(text: str) -> dict[str, Any]:
    """Parse a transcript into structured events for invariant checks.

    Keys: ``version``, ``calls`` (each with an ``op``), ``data``, ``effects``,
    ``end`` and the raw ``lines``.
    """
    parsed: dict[str, Any] = {"version": None, "calls": [], "data": [], "effects": [],
                              "end": None, "lines": []}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        tag = parts[0]
        if tag == "SYNTH":
            parsed["version"] = parts[1] if len(parts) > 1 else ""
        elif tag == "EFF":
            parsed["effects"].append(_fields(parts[1:]))
        elif tag == "DATA":
            parsed["data"].append(_fields(parts[1:]))
        elif tag == "CALL":
            call = _fields(parts[2:])
            call["op"] = parts[1]
            parsed["calls"].append(call)
        elif tag == "END":
            parsed["end"] = _fields(parts[1:])
        else:
            raise HarnessError(f"unrecognized transcript line: {raw!r}")
        parsed["lines"].append(line)
    return parsed
