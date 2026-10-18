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
  recorder's *accounting*;
* ``content`` - the P4.9 content-capture driver, whose versioned observation export
  is the deterministic golden output of the recorder's *content* accounting and the
  contract the offline materializer consumes.

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
                    NATIVE_DIR / "policy.c", SYNTHETIC_DIR / "synthetic.c"),
        "headers": (NATIVE_DIR / "recorder.h", NATIVE_DIR / "policy.h",
                    NATIVE_DIR / "spin_barrier.h", NATIVE_DIR / "synth_context.h",
                    SYNTHETIC_DIR / "synth.h"),
        "golden": NATIVE_DIR / "golden.txt",
        "cflags": ("-pthread",),
    },
    "content": {
        "exe": "content",
        "sources": (NATIVE_DIR / "content_driver.c", NATIVE_DIR / "recorder.c",
                    NATIVE_DIR / "policy.c", SYNTHETIC_DIR / "synthetic.c"),
        "headers": (NATIVE_DIR / "recorder.h", NATIVE_DIR / "policy.h",
                    NATIVE_DIR / "spin_barrier.h", NATIVE_DIR / "synth_context.h",
                    SYNTHETIC_DIR / "synth.h"),
        "golden": NATIVE_DIR / "content_golden.txt",
        "cflags": ("-pthread",),
    },
}


class HarnessError(RuntimeError):
    """A target could not be built or run, or spoke a broken transcript."""


def _spec(target: str) -> dict[str, Any]:
    try:
        return TARGETS[target]
    except KeyError as exc:
        raise HarnessError(f"unknown target {target!r}; known: {sorted(TARGETS)}") from exc


def build_spec(spec: dict[str, Any], build_dir: Path, *, cc: str = "cc",
               timeout_s: float = 120.0) -> Path:
    """Compile a trusted target spec into ``build_dir`` and return the executable path."""
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    for path in tuple(spec["sources"]) + tuple(spec["headers"]):
        shutil.copyfile(path, build_dir / Path(path).name)
    exe = build_dir / spec["exe"]
    command = [cc, *COMPILE_FLAGS, *[Path(p).name for p in spec["sources"]],
               *spec.get("cflags", ()), "-o", exe.name]
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


def build(target: str, build_dir: Path, *, cc: str = "cc", timeout_s: float = 120.0) -> Path:
    """Compile the named trusted target into ``build_dir`` and return the executable."""
    return build_spec(_spec(target), build_dir, cc=cc, timeout_s=timeout_s)


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


def _sync(parts: list[str]) -> dict[str, Any]:
    fields = _fields(parts)
    participants = [int(part) for part in fields["participants"].split(",") if part]
    before, _, after = fields["phase"].partition("->")
    return {"barrier": int(fields["barrier"]), "participants": participants,
            "phase_before": int(before), "phase_after": int(after)}


def _event(parts: list[str]) -> dict[str, Any]:
    fields = _fields(parts)
    context = ([int(word) for word in fields["context"].split(",")]
               if fields.get("context") else [])
    return {"thread_id": int(fields["thread"]), "sequence": int(fields["seq"]),
            "phase": int(fields["phase"]), "hook_id": int(fields["hook"]),
            "result": int(fields["result"]), "complete": int(fields["complete"]),
            "context": context}


def parse_recorder_transcript(text: str) -> dict[str, Any]:
    """Parse a native-recorder transcript into scenarios and their counters.

    Scenario counters stay as strings (callers coerce); captured events and declared
    sync edges are typed, since they describe the observed order. Each scenario
    carries its own ``events``, ``event_count`` and ``syncs``.

    Keys: ``version``, ``policies``, ``scenarios``, ``syncs``, ``events``, ``end``.
    """
    parsed: dict[str, Any] = {"version": None, "policies": [], "scenarios": [],
                              "syncs": [], "events": [], "end": None}
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        tag = parts[0]
        if tag == "RECORDER":
            parsed["version"] = parts[1] if len(parts) > 1 else ""
        elif tag == "POLICY":
            policy = _fields(parts[2:])
            policy["name"] = parts[1] if len(parts) > 1 else ""
            parsed["policies"].append(policy)
        elif tag == "SCENARIO":
            current = {"name": parts[1] if len(parts) > 1 else "", "counters": {},
                       "accounting": None, "events": [], "event_count": None, "syncs": []}
            parsed["scenarios"].append(current)
        elif tag == "COUNTERS":
            if current is None:
                raise HarnessError("COUNTERS before any SCENARIO")
            current["counters"] = _fields(parts[1:])
        elif tag == "ACCOUNTING":
            if current is None:
                raise HarnessError("ACCOUNTING before any SCENARIO")
            current["accounting"] = parts[1] if len(parts) > 1 else ""
        elif tag == "SYNC":
            if current is None:
                raise HarnessError("SYNC before any SCENARIO")
            sync = _sync(parts[1:])
            current["syncs"].append(sync)
            parsed["syncs"].append(sync)
        elif tag == "EVENTS":
            if current is None:
                raise HarnessError("EVENTS before any SCENARIO")
            current["event_count"] = int(_fields(parts[1:])["count"])
        elif tag == "EV":
            if current is None:
                raise HarnessError("EV before any SCENARIO")
            event = _event(parts[1:])
            current["events"].append(event)
            parsed["events"].append(event)
        elif tag == "END":
            parsed["end"] = _fields(parts[1:])
        else:
            raise HarnessError(f"unrecognized recorder transcript line: {raw!r}")
    return parsed


# --- the versioned observation export (P4.9) -----------------------------------


def _span_list(value: str) -> list[dict[str, Any]]:
    """Decode a content inventory: `c:<length>:<hex>` captured, `l:<length>` lost."""
    spans: list[dict[str, Any]] = []
    for item in value.split(","):
        if not item:
            continue
        parts = item.split(":")
        if parts[0] == "c" and len(parts) == 3:
            length = int(parts[1])
            hexed = parts[2]
            if len(hexed) != length * 2:
                raise HarnessError(f"content span length disagrees with its bytes: {item!r}")
            spans.append({"status": "captured", "length": length,
                          "bytes": bytes.fromhex(hexed)})
        elif parts[0] == "l" and len(parts) == 2:
            spans.append({"status": "lost", "length": int(parts[1]), "bytes": None})
        else:
            raise HarnessError(f"malformed content span: {item!r}")
    return spans


def _observation(parts: list[str]) -> dict[str, Any]:
    fields = _fields(parts)
    args = [int(word) for word in fields["args"].split(",") if word]
    context = [int(word) for word in fields["context"].split(",") if word]
    return {"thread_id": int(fields["thread"]), "sequence": int(fields["seq"]),
            "phase": int(fields["phase"]), "hook_id": int(fields["hook"]),
            "point": int(fields["point"]), "result": int(fields["result"]),
            "complete": int(fields["complete"]), "nargs": int(fields["nargs"]),
            "args": args, "ncontext": int(fields["ncontext"]), "context": context,
            "content": _span_list(fields["content"])}


def parse_export_transcript(text: str) -> dict[str, Any]:
    """Parse a versioned observation export into scenarios and observations.

    Unlike the accounting transcript, this export carries each observation's ABI
    arguments, its declared capture point, and its declared content. Counters stay as
    strings (callers coerce); observations, arena reports and CONFIG lines are typed.

    Keys: ``version``, ``scenarios``, ``observations``, ``config``, ``end``.
    """
    parsed: dict[str, Any] = {"version": None, "scenarios": [], "observations": [],
                              "config": [], "end": None}
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        tag = parts[0]
        if tag == "RECORDER-EXPORT":
            parsed["version"] = parts[1] if len(parts) > 1 else ""
        elif tag == "SCENARIO":
            current = {"name": parts[1] if len(parts) > 1 else "", "counters": {},
                       "accounting": None, "arena": None, "observations": [],
                       "observation_count": None}
            parsed["scenarios"].append(current)
        elif tag == "COUNTERS":
            if current is None:
                raise HarnessError("COUNTERS before any SCENARIO")
            current["counters"] = _fields(parts[1:])
        elif tag == "ACCOUNTING":
            if current is None:
                raise HarnessError("ACCOUNTING before any SCENARIO")
            current["accounting"] = parts[1] if len(parts) > 1 else ""
        elif tag == "ARENA":
            if current is None:
                raise HarnessError("ARENA before any SCENARIO")
            current["arena"] = {key: int(value) for key, value in _fields(parts[1:]).items()}
        elif tag == "OBSERVATIONS":
            if current is None:
                raise HarnessError("OBSERVATIONS before any SCENARIO")
            current["observation_count"] = int(_fields(parts[1:])["count"])
        elif tag == "OBS":
            if current is None:
                raise HarnessError("OBS before any SCENARIO")
            observation = _observation(parts[1:])
            current["observations"].append(observation)
            parsed["observations"].append(observation)
        elif tag == "CONFIG":
            parsed["config"].append(_fields(parts[1:]))
        elif tag == "END":
            parsed["end"] = _fields(parts[1:])
        else:
            raise HarnessError(f"unrecognized observation export line: {raw!r}")
    return parsed
