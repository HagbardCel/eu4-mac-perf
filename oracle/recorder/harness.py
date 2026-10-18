"""Build-and-run harness for the P4 native targets.

The harness - never the subject - assembles each build. It copies the trusted
sources into a private directory and compiles them there, so the observation
protocol and the ABI cannot be altered by what is being observed. It then runs the
target and returns its canonical transcript.

Three targets exist:

* ``synthetic`` - the P4.2 subject and its fixed scenario driver, whose transcript
  is the deterministic golden output of the *subject*;
* ``recorder`` - the P4.3 native recorder core linked against the same subject
  through a hook layer, whose transcript is the deterministic golden output of the
  recorder's *accounting*;
* ``content`` - the P4.9 content-capture driver, whose versioned observation export
  is the deterministic golden output of the recorder's *content* accounting, with each
  observation's content origin and instrumentation fault, and the contract the offline
  materializer consumes.

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
    """Decode `key=value` fields, rejecting anything that could hide a second value.

    A repeated key would silently overwrite the first - the shape of defect that let a
    contradictory record pass as well formed - so it is refused here for every
    transcript this module reads.
    """
    fields: dict[str, str] = {}
    for part in parts:
        key, sep, value = part.partition("=")
        if not sep:
            raise HarnessError(f"transcript field is not key=value: {part!r}")
        if not key:
            raise HarnessError(f"transcript field has an empty key: {part!r}")
        if key in fields:
            raise HarnessError(f"transcript field is declared twice: {key!r}")
        fields[key] = value
    return fields


def _decimal(field: str, value: str) -> int:
    """A canonical non-negative decimal, as the native export writes one."""
    if not value or not value.isascii() or not value.isdigit():
        raise HarnessError(f"{field} is not a non-negative decimal: {value!r}")
    if len(value) > 1 and value[0] == "0":
        raise HarnessError(f"{field} is not canonical: {value!r}")
    return int(value)


def _require(fields: dict[str, str], *keys: str) -> None:
    """Insist a record carries every field its shape promises, rather than defaulting."""
    for key in keys:
        if key not in fields:
            raise HarnessError(f"transcript record is missing the {key!r} field")


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


#: The instrumentation faults the native export can name, in the core's precedence.
FAULTS = ("none", "args", "context", "point", "spans", "bytes", "origin")


def _span_list(value: str) -> list[dict[str, Any]]:
    """Decode a content inventory: `c:<length>:<hex>` captured, `l:<length>` lost."""
    spans: list[dict[str, Any]] = []
    for item in value.split(","):
        if not item:
            continue
        parts = item.split(":")
        if parts[0] == "c" and len(parts) == 3:
            length = _decimal("span length", parts[1])
            hexed = parts[2]
            if len(hexed) != length * 2:
                raise HarnessError(f"content span length disagrees with its bytes: {item!r}")
            if not hexed.isascii() or any(c not in "0123456789abcdef" for c in hexed):
                raise HarnessError(f"content span bytes are not lowercase hex: {item!r}")
            spans.append({"status": "captured", "length": length,
                          "bytes": bytes.fromhex(hexed)})
        elif parts[0] == "l" and len(parts) == 2:
            spans.append({"status": "lost", "length": _decimal("span length", parts[1]),
                          "bytes": None})
        else:
            raise HarnessError(f"malformed content span: {item!r}")
    if not spans and value:
        raise HarnessError(f"content inventory decoded to nothing: {value!r}")
    return spans


def _observation(parts: list[str]) -> dict[str, Any]:
    """One exported observation, with every declared cardinality enforced.

    The declared counts are what make the export a contract rather than a rendering:
    a content item deleted while `nspans` still claims it, an argument list truncated
    below `nargs`, or content that names no origin at all are each refused here,
    before any consumer can act on them.
    """
    fields = _fields(parts)
    _require(fields, "thread", "seq", "phase", "hook", "point", "result", "complete",
             "fault", "nargs", "args", "ncontext", "context", "nspans", "content",
             "norigin", "origin")
    fault = fields["fault"]
    if fault not in FAULTS:
        raise HarnessError(f"unknown observation fault: {fault!r}")
    nargs = _decimal("nargs", fields["nargs"])
    ncontext = _decimal("ncontext", fields["ncontext"])
    nspans = _decimal("nspans", fields["nspans"])
    norigin = _decimal("norigin", fields["norigin"])
    complete = _decimal("complete", fields["complete"])
    args = [_decimal("args", word) for word in fields["args"].split(",") if word]
    context = [_decimal("context", word) for word in fields["context"].split(",") if word]
    origin = [_decimal("origin", word) for word in fields["origin"].split(",") if word]
    content = _span_list(fields["content"])
    for name, declared, decoded in (("args", nargs, args), ("context", ncontext, context),
                                    ("origin", norigin, origin), ("content", nspans, content)):
        if declared != len(decoded):
            raise HarnessError(
                f"observation declares {name}={declared} but {len(decoded)} were decoded")
    if complete not in (0, 1):
        raise HarnessError(f"observation complete flag is not 0 or 1: {complete!r}")
    if fault != "none" and (complete != 0 or nspans != 0):
        raise HarnessError(
            f"a faulted observation cannot be complete or declare content: {fault!r}")
    if content and norigin == 0:
        raise HarnessError("observation declares content with no content origin")
    return {"thread_id": _decimal("thread", fields["thread"]),
            "sequence": _decimal("seq", fields["seq"]),
            "phase": _decimal("phase", fields["phase"]),
            "hook_id": _decimal("hook", fields["hook"]),
            "point": _decimal("point", fields["point"]),
            "result": _decimal("result", fields["result"]),
            "complete": complete, "fault": fault,
            "nargs": nargs, "args": args,
            "ncontext": ncontext, "context": context,
            "nspans": nspans, "content": content,
            "norigin": norigin, "origin": origin}


def parse_export_transcript(text: str) -> dict[str, Any]:
    """Parse a versioned observation export into scenarios and observations.

    Unlike the accounting transcript, this export carries each observation's ABI
    arguments, its declared capture point, its declared content and that content's
    origin. Counters stay as strings (callers coerce); observations, arena reports
    and CONFIG lines are typed.

    The shape is enforced, not merely read: a scenario must declare its counters, its
    accounting verdict, its arena report, its admission verdict and how many
    observations it has, and whether it printed them; each of those control records may
    appear at most once. When a scenario listed its observations, that count must match
    the observations that followed and be reconciled with the native event counters
    (`count == captured + incomplete`, `complete == captured`, `incomplete == incomplete`);
    when it deliberately withheld them, none may appear, though its count is still
    reconciled from the aggregates. The native counter identities must hold, and a
    faulted observation can never coexist with `ADMISSION ok`. A transcript with no
    version line, no terminal END, more than one END, or any record after END is refused
    rather than partially understood.

    Keys: ``version``, ``scenarios``, ``observations``, ``config``, ``end``.
    """
    parsed: dict[str, Any] = {"version": None, "scenarios": [], "observations": [],
                              "config": [], "end": None}
    current: dict[str, Any] | None = None
    ended = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if ended:
            raise HarnessError(f"record after the terminal END line: {raw!r}")
        parts = line.split()
        tag = parts[0]
        if tag == "RECORDER-EXPORT":
            if parsed["version"] is not None or parsed["scenarios"] or parsed["config"]:
                raise HarnessError("RECORDER-EXPORT must be the first line")
            parsed["version"] = parts[1] if len(parts) > 1 else ""
        elif tag == "SCENARIO":
            current = {"name": parts[1] if len(parts) > 1 else "", "counters": {},
                       "accounting": None, "admission": None, "arena": None,
                       "observations": [], "observation_count": None, "listed": None}
            parsed["scenarios"].append(current)
        elif tag == "COUNTERS":
            if current is None:
                raise HarnessError("COUNTERS before any SCENARIO")
            if current["counters"]:
                raise HarnessError(f"scenario {current['name']!r} declares COUNTERS twice")
            current["counters"] = _fields(parts[1:])
        elif tag == "ACCOUNTING":
            if current is None:
                raise HarnessError("ACCOUNTING before any SCENARIO")
            if current["accounting"] is not None:
                raise HarnessError(f"scenario {current['name']!r} declares ACCOUNTING twice")
            verdict = parts[1] if len(parts) > 1 else ""
            if verdict not in ("ok", "VIOLATED"):
                raise HarnessError(f"unknown accounting verdict: {verdict!r}")
            current["accounting"] = verdict
        elif tag == "ADMISSION":
            if current is None:
                raise HarnessError("ADMISSION before any SCENARIO")
            if current["admission"] is not None:
                raise HarnessError(f"scenario {current['name']!r} declares ADMISSION twice")
            verdict = parts[1] if len(parts) > 1 else ""
            if verdict not in ("ok", "REJECTED"):
                raise HarnessError(f"unknown admission verdict: {verdict!r}")
            current["admission"] = verdict
        elif tag == "ARENA":
            if current is None:
                raise HarnessError("ARENA before any SCENARIO")
            if current["arena"] is not None:
                raise HarnessError(f"scenario {current['name']!r} declares ARENA twice")
            fields = _fields(parts[1:])
            _require(fields, "capacity", "used", "intervals", "overlaps", "out_of_range")
            current["arena"] = {key: _decimal(key, value) for key, value in fields.items()}
        elif tag == "OBSERVATIONS":
            if current is None:
                raise HarnessError("OBSERVATIONS before any SCENARIO")
            if current["observation_count"] is not None:
                raise HarnessError(
                    f"scenario {current['name']!r} declares OBSERVATIONS twice")
            fields = _fields(parts[1:])
            _require(fields, "count", "listed")
            current["observation_count"] = _decimal("count", fields["count"])
            listed = _decimal("listed", fields["listed"])
            if listed not in (0, 1):
                raise HarnessError(f"observation listed flag is not 0 or 1: {listed!r}")
            current["listed"] = bool(listed)
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
            ended = True
        else:
            raise HarnessError(f"unrecognized observation export line: {raw!r}")

    if parsed["version"] is None:
        raise HarnessError("export is missing its RECORDER-EXPORT version line")
    if parsed["version"] != "v1":
        raise HarnessError(f"unknown export version: {parsed['version']!r}")
    if not parsed["scenarios"]:
        raise HarnessError("export declares no scenarios")
    if not ended:
        raise HarnessError("export is missing its terminal END line")
    for scenario in parsed["scenarios"]:
        name = scenario["name"]
        if not scenario["counters"]:
            raise HarnessError(f"scenario {name!r} is missing its COUNTERS record")
        for key in ("accounting", "admission"):
            if scenario[key] is None:
                raise HarnessError(f"scenario {name!r} is missing its {key.upper()} record")
        if scenario["arena"] is None:
            raise HarnessError(f"scenario {name!r} is missing its ARENA record")
        if scenario["observation_count"] is None or scenario["listed"] is None:
            raise HarnessError(f"scenario {name!r} is missing its OBSERVATIONS record")
        if not scenario["listed"]:
            # A scenario that withholds per-observation detail because it is a
            # property of the race, not of the capture, must not print any either.
            if scenario["observations"]:
                raise HarnessError(
                    f"scenario {name!r} printed observations it declared unlisted")
        elif scenario["observation_count"] != len(scenario["observations"]):
            raise HarnessError(
                f"scenario {name!r} declares {scenario['observation_count']} observations "
                f"but printed {len(scenario['observations'])}")
        _reconcile(name, scenario)
    return parsed


def _counter(scenario: dict[str, Any], field: str) -> int:
    """A named native counter, refused if the record does not carry it."""
    values = scenario["counters"]
    if field not in values:
        raise HarnessError(f"scenario {scenario['name']!r} is missing the {field!r} counter")
    return _decimal(field, values[field])


def _reconcile(name: str, scenario: dict[str, Any]) -> None:
    """Cross-check a scenario's declarations against each other and its observations.

    The native counters are an independent statement of what was intercepted and how
    it resolved; the records the reader parsed are a second. Where both are available
    they must agree - a capture whose two accounts disagree is not a capture. For a
    listed scenario the parsed observations are reconciled with the event counters,
    and a faulted observation can never coexist with `ADMISSION ok`.
    """
    intercepted = _counter(scenario, "intercepted")
    selected = _counter(scenario, "selected")
    unselected = _counter(scenario, "unselected")
    captured = _counter(scenario, "captured")
    incomplete = _counter(scenario, "incomplete")
    lost = _counter(scenario, "lost")
    rejected = _counter(scenario, "rejected")
    declared = _counter(scenario, "content_bytes_declared")
    bytes_captured = _counter(scenario, "content_bytes_captured")
    bytes_lost = _counter(scenario, "content_bytes_lost")

    # The native counter identities, as the reader's own consistency check.
    if intercepted != selected + unselected:
        raise HarnessError(
            f"scenario {name!r}: intercepted != selected + unselected")
    if selected != captured + incomplete + lost:
        raise HarnessError(
            f"scenario {name!r}: selected != captured + incomplete + lost")
    if declared != bytes_captured + bytes_lost:
        raise HarnessError(
            f"scenario {name!r}: content_bytes_declared != captured + lost")

    expected = "ok" if rejected == 0 else "REJECTED"
    if scenario["admission"] != expected:
        raise HarnessError(
            f"scenario {name!r} admits {scenario['admission']!r} with rejected={rejected}")

    # Every stored event is exactly one captured or incomplete observation, whatever
    # was printed: this holds for a withheld scenario too, from its aggregates alone.
    if scenario["observation_count"] != captured + incomplete:
        raise HarnessError(
            f"scenario {name!r} declares {scenario['observation_count']} observations but "
            f"its counters account for {captured + incomplete}")
    if not scenario["listed"]:
        # Detail withheld as a race outcome: the aggregates are the whole statement,
        # so no per-observation claim can be checked beyond the count.
        return

    complete = sum(1 for obs in scenario["observations"] if obs["complete"] == 1)
    incomplete_listed = len(scenario["observations"]) - complete
    faulted = sum(1 for obs in scenario["observations"] if obs["fault"] != "none")
    if complete != captured:
        raise HarnessError(
            f"scenario {name!r} printed {complete} complete observations but captured={captured}")
    if incomplete_listed != incomplete:
        raise HarnessError(
            f"scenario {name!r} printed {incomplete_listed} incomplete observations but "
            f"incomplete={incomplete}")
    if faulted > rejected:
        raise HarnessError(
            f"scenario {name!r} printed {faulted} faulted observations but rejected={rejected}")
    if scenario["admission"] == "ok" and faulted:
        raise HarnessError(
            f"scenario {name!r} admits ok with {faulted} faulted observations")
