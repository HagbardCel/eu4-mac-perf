"""Build-and-run harness for the P4 native targets.

The harness - never the subject - assembles each build. It copies the trusted
sources into a private directory and compiles them there, so the observation
protocol and the ABI cannot be altered by what is being observed. It then runs the
target and returns its canonical transcript.

Two targets exist:

* ``synthetic`` - the P4.2 subject and its fixed scenario driver, whose transcript
  is the deterministic golden output of the *subject*;
* ``recorder`` - the P4.3 native recorder core linked against the same subject
  through a hook layer, whose transcript is the deterministic golden output of the
  recorder's *accounting*.

This is a scaffold for golden-output gates, not the P3 controller: there is no
process-group escalation, no budget ledger and no candidate workspace, because the
subjects here are trusted repository code and the transcripts are bounded. It never
launches EU IV.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SYNTHETIC_DIR = Path(__file__).resolve().parent / "synthetic"
NATIVE_DIR = Path(__file__).resolve().parent / "native"

COMPILE_FLAGS = ("-std=c11", "-O0", "-fno-strict-aliasing")

TARGETS: dict[str, dict[str, Any]] = {
    "synthetic": {
        "exe": "synth",
        "sources": (SYNTHETIC_DIR / "driver.c", SYNTHETIC_DIR / "synthetic.c"),
        "headers": (SYNTHETIC_DIR / "synth.h",),
        "golden": SYNTHETIC_DIR / "golden.txt",
    },
    "recorder": {
        "exe": "recorder",
        "sources": (NATIVE_DIR / "driver.c", NATIVE_DIR / "recorder.c",
                    SYNTHETIC_DIR / "synthetic.c"),
        "headers": (NATIVE_DIR / "recorder.h", SYNTHETIC_DIR / "synth.h"),
        "golden": NATIVE_DIR / "golden.txt",
    },
}


class HarnessError(RuntimeError):
    """A target could not be built or run, or spoke a broken transcript."""


def _spec(target: str) -> dict[str, Any]:
    try:
        return TARGETS[target]
    except KeyError as exc:
        raise HarnessError(f"unknown target {target!r}; known: {sorted(TARGETS)}") from exc


def build(target: str, build_dir: Path, *, cc: str = "cc", timeout_s: float = 120.0) -> Path:
    """Compile the trusted target into ``build_dir`` and return the executable path."""
    spec = _spec(target)
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    for path in tuple(spec["sources"]) + tuple(spec["headers"]):
        shutil.copyfile(path, build_dir / Path(path).name)
    exe = build_dir / spec["exe"]
    command = [cc, *COMPILE_FLAGS, *[Path(p).name for p in spec["sources"]], "-o", exe.name]
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


def run_transcript(exe: Path, *, timeout_s: float = 30.0) -> str:
    """Run a built target and return its transcript."""
    try:
        proc = subprocess.run([str(exe)], capture_output=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(f"target timed out after {timeout_s}s") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise HarnessError(f"target exited {proc.returncode}: {detail}")
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HarnessError("transcript is not valid UTF-8") from exc


def golden_transcript(target: str) -> str:
    """The committed golden transcript for a target."""
    return Path(_spec(target)["golden"]).read_text(encoding="utf-8")


def reproduce(target: str, *, cc: str = "cc") -> str:
    """Build in a scratch directory and return the target's transcript."""
    with tempfile.TemporaryDirectory(prefix=f"oracle-p4-{target}-") as tmp:
        exe = build(target, Path(tmp), cc=cc)
        return run_transcript(exe)


# --- transcript parsing --------------------------------------------------------

def _fields(parts: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in parts:
        key, sep, value = part.partition("=")
        if not sep:
            raise HarnessError(f"transcript field is not key=value: {part!r}")
        fields[key] = value
    return fields


def parse_transcript(text: str) -> dict[str, Any]:
    """Parse a synthetic-subject transcript into structured events for invariant checks.

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


def parse_recorder_transcript(text: str) -> dict[str, Any]:
    """Parse a native-recorder transcript into scenarios and their counters.

    Keys: ``version``, ``scenarios`` (each with ``name``, ``counters`` and
    ``accounting``), and ``end``.
    """
    parsed: dict[str, Any] = {"version": None, "scenarios": [], "end": None}
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        tag = parts[0]
        if tag == "RECORDER":
            parsed["version"] = parts[1] if len(parts) > 1 else ""
        elif tag == "SCENARIO":
            current = {"name": parts[1] if len(parts) > 1 else "", "counters": {},
                       "accounting": None}
            parsed["scenarios"].append(current)
        elif tag == "COUNTERS":
            if current is None:
                raise HarnessError("COUNTERS before any SCENARIO")
            current["counters"] = _fields(parts[1:])
        elif tag == "ACCOUNTING":
            if current is None:
                raise HarnessError("ACCOUNTING before any SCENARIO")
            current["accounting"] = parts[1] if len(parts) > 1 else ""
        elif tag == "END":
            parsed["end"] = _fields(parts[1:])
        else:
            raise HarnessError(f"unrecognized recorder transcript line: {raw!r}")
    return parsed
