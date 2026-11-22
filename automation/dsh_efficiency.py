"""Measure an existing DSH session export; no monitoring service, no dashboard.

The M7.0 canary session is a measurable case study for this infrastructure PR.
This utility reads one DeepSeek Harness JSONL session export (plain or
``.jsonl.zstd``) and reports the inefficiencies the PR is meant to reduce:

* tool calls by name and failure rate;
* repeated identical calls (including duplicate test invocations);
* oversized/truncated tool results;
* long-running shell jobs and total tool wait;
* model token usage with cache-read usage separated;
* EU4 launch count;
* human permission/intervention events;
* the goal outcome.

It is read-only and bounded: results are aggregated, long texts are replaced by
lengths and short excerpts, and no private path or secret is copied into the
summary.  Committed fixtures must be sanitized; the report can cite a local
export that is not committed.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCHEMA = "dsh-efficiency/v1"
OVERSIZED_CHARS = 12000
LONG_JOB_SECONDS = 20.0
TOP = 10

_TEST_COMMAND = re.compile(
    r"(?:python[\d.]*\s+-m\s+unittest|python[\d.]*\s+-m\s+pytest|"
    r"\bpytest\b|unittest\s+discover)", re.IGNORECASE)
_FULL_SUITE = re.compile(r"discover\s+-s\s+tests\b")
# A launch is an invocation at command position, not a mention of the word in
# a heredoc or a grep. This keeps report edits out of the launch count.
_COMMAND_BOUNDARY = r"(?:^|[\n;&|]\s*)"
_EU4_COMMAND = re.compile(
    _COMMAND_BOUNDARY
    + r"(?:(?:[A-Za-z0-9_./-]*/)?(?:python[\d.]*\s+-m\s+)?"
      r"(?:automation\.)?(?:eu4_diff_run|gfx_dual_run|live_gfx)\b"
      r"|(?:open\s+-a\s+[\"']?[^\s\"']*(?:EU4|Europa))"
      r"|(?:[A-Za-z0-9_./ -]*Europa Universalis[^\s\"']*\.app)"
      r"|(?:[A-Za-z0-9_./ -]*eu4\.[A-Za-z0-9_]+\.exe))",
    re.IGNORECASE | re.MULTILINE)
_CAPTURE_COMMAND = re.compile(
    _COMMAND_BOUNDARY
    + r"(?:[A-Za-z0-9_./-]*/)?"
      r"(?:python[\d.]*\s+-m\s+(?:automation\.)?m6_frame\s+capture)\b",
    re.IGNORECASE | re.MULTILINE)
_RUNNER_COMMAND = re.compile(
    _COMMAND_BOUNDARY
    + r"(?:(?:[A-Za-z0-9_./-]*/)?python[\d.]*\s+-m\s+)?"
      r"(?:automation\.)?test_runner\s+run\b",
    re.IGNORECASE | re.MULTILINE)
_SPILL = ("dsh-spill", "use read with offset/limit", "[truncated", "(truncated")
_LAUNCH_OUTPUT = re.compile(
    r"--output(?:=|\s+)(['\"]?)([^\s'\";]+)\1")
_STATE_PATH = re.compile(
    r"(?:EU4_CAMPAIGN_STATE=(['\"]?)([^\s'\";]+)\1|"
    r"--state(?:=|\s+)(['\"]?)([^\s'\";]+)\3)")
_PS_EU4 = re.compile(
    r"(?m)^\s*(\d{2,7})\s+(?:\d{1,7}\s+)?(?:\d+-)?\d{1,3}:\d{2}"
    r"(?::\d{2})?\s+\.?/?eu4\b")
_PID_JSON = re.compile(r"""['"]pid['"]\s*:\s*['"]?(\d{1,7})""")
_TERM_TAG = re.compile(
    r"(?m)(?:^|[\s/])([A-Za-z][A-Za-z0-9_.-]*)\s+terminated\s+"
    r"(?:True|true|1)\b")
_RUNNER_MODULE = re.compile(r"--module(?:=|\s+)([^\s;&|]+)")
_RUNNER_PATTERN = re.compile(r"--pattern(?:=|\s+)([^\s;&|]+)")
_RUNNER_LABEL = re.compile(r"--label(?:=|\s+)([^\s;&|]+)")
_RUNNER_PROFILE = re.compile(r"--profile(?:=|\s+)([^\s;&|]+)")
_SESSION_JSON = re.compile(
    r"""['"]session['"]\s*:\s*['"]([0-9a-fA-F-]{36})['"]""")
_SESSION_FIELD = re.compile(
    r"""\bsession['"]?\s*[:= ]\s*['"]?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"""
    r"""[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})""")
_GENERIC_TAGS = {"capture", "launch", "run", "frame", "process", "game"}


def _iter_json_objects(text: str):
    """Yield complete JSON objects embedded in arbitrary tool output."""
    decoder = json.JSONDecoder()
    index = 0
    while True:
        index = text.find("{", index)
        if index < 0:
            return
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError:
            index += 1
            continue
        if isinstance(value, dict):
            yield value
        index = max(end, index + 1)


def _launch_output(command: str) -> str | None:
    match = _LAUNCH_OUTPUT.search(command)
    return _normalize_evidence_path(match.group(2)) if match else None


def _state_path(command: str) -> str | None:
    match = _STATE_PATH.search(command)
    if not match:
        return None
    return _normalize_evidence_path(match.group(2) or match.group(4))


def _normalize_evidence_path(value) -> str:
    text = str(value or "").strip().strip("'\"")
    text = text.replace("\\", "/")
    if ".local/" in text:
        text = text[text.index(".local/"):]
    return text.rstrip("/")


def _evidence_key(value) -> str:
    return _normalize_evidence_path(value)


def _launch_tag(value) -> str | None:
    key = _evidence_key(value)
    if not key:
        return None
    return key.rsplit("/", 1)[-1]


def _tag_in_text(tag: str | None, text: str) -> bool:
    if not tag:
        return False
    return re.search(r"(?<![\w.-])" + re.escape(tag) + r"(?![\w.-])",
                     text) is not None


def _valid_command_segment(command: str, start: int, length: int = 500) -> str:
    segment = command[start:start + length].lstrip(" \t\r\n;&|")
    for separator in ("\n", ";", "&&", "||", "|"):
        cut = segment.find(separator)
        if cut >= 0:
            segment = segment[:cut]
    return segment.strip()


def _runner_selection(command: str, start: int) -> dict | None:
    segment = _valid_command_segment(command, start, 700)
    if re.search(r"(?:^|\s)(?:--help|-h)\b", segment):
        return None
    modules = _RUNNER_MODULE.findall(segment)
    pattern = _RUNNER_PATTERN.search(segment)
    label = _RUNNER_LABEL.search(segment)
    profile = _RUNNER_PROFILE.search(segment)
    if modules or pattern:
        kind = "focused" if modules else "pattern"
    else:
        kind = "full"
    signature = _normalize_command(segment)
    return {
        "kind": kind,
        "modules": sorted(set(modules)),
        "pattern": pattern.group(1) if pattern else None,
        "label": label.group(1) if label else None,
        "profile": profile.group(1) if profile else None,
        "signature": signature,
        "excerpt": _sanitize(_excerpt(segment, limit=240)),
    }


def _runner_evidence(command: str) -> list[dict]:
    rows = []
    for match in _RUNNER_COMMAND.finditer(command):
        selection = _runner_selection(command, match.start())
        if selection is not None:
            rows.append(selection)
    return rows


def _runner_counted_tests(text: str) -> int | None:
    values = []
    match = re.search(r"(?m)^Ran (\d+) tests? in ", text)
    if match:
        values.append(int(match.group(1)))
    for value in _iter_json_objects(text):
        counts = value.get("counts")
        if isinstance(counts, dict):
            executed = counts.get("executed", counts.get("ran"))
            if isinstance(executed, int) and executed >= 0:
                values.append(executed)
    return max(values) if values else None


_NONZERO_EXIT = re.compile(
    r"(?:\[exit code:\s*([1-9]\d*)\]|(?:^|[\s_])exit(?: code)?[=:]\s*"
    r"([1-9]\d*)\b|capture_exit[=:]\s*([1-9]\d*))", re.MULTILINE)


def _attempt_failed(call_id: str, results: dict, jobs: dict) -> bool:
    text = str((results.get(call_id) or {}).get("evidence_text") or "")
    if _NONZERO_EXIT.search(text):
        return True
    if "capture failed" in text.lower():
        return True
    for job in jobs.values():
        if job.get("call_id") != call_id:
            continue
        if str(job.get("status") or "").lower() in (
                "failed", "killed", "timed_out"):
            return True
        detail = str(job.get("detail") or "")
        match = re.search(r"exit code:\s*([1-9]\d*)", detail)
        if match:
            return True
    return False


def _join_physical_launches(attempts: list[dict],
                            evidence: list[dict]) -> dict:
    """Join launch commands with guard claims, process IDs and terminations.

    Every launch command is an attempt.  A *confirmed* game launch needs three
    independent pieces of evidence for the same attempt: a guard claim (an
    explicit ``claim_launch`` record or a guard status count covering it), a
    process/session identity, and a termination record.  Missing links stay
    visible as ambiguous or unclaimed attempts instead of inflating the count.
    """
    claims: list[dict] = []
    counts: list[dict] = []
    processes: list[dict] = []
    terminations: list[dict] = []
    for unit in evidence:
        text = str(unit.get("text") or "")
        command = str(unit.get("command") or "")
        state = _state_path(command)
        for value in _iter_json_objects(text):
            action = str(value.get("action") or "")
            outcome = str(value.get("outcome") or "")
            payload = value.get("evidence")
            if (action == "claim_launch"
                    and outcome in ("claimed", "ok", "reserved")):
                output = (payload or {}).get("output") if isinstance(
                    payload, dict) else None
                if output:
                    claims.append({"output": _evidence_key(output),
                                   "time": unit.get("time"),
                                   "source": "claim_launch"})
            counters = value.get("counts")
            if isinstance(counters, dict) and "eu4_launches" in counters:
                count = counters.get("eu4_launches")
                if isinstance(count, int):
                    counts.append({"count": count, "state": state,
                                   "time": unit.get("time")})
            terminated = value.get("terminated")
            pid = value.get("pid")
            if terminated is True:
                terminations.append({
                    "pid": _as_pid(pid), "tag": None,
                    "time": unit.get("time"),
                    "source": "json termination"})
            termination = value.get("termination")
            if isinstance(termination, dict) and termination.get("terminated"):
                terminations.append({
                    "pid": _as_pid(termination.get("pid")),
                    "tag": None, "time": unit.get("time"),
                    "source": "launcher termination"})
            executable = str(value.get("executable") or "")
            if _as_pid(pid) and ("Europa" in executable
                                 or "eu4.app" in executable):
                processes.append({"pid": _as_pid(pid), "session": None,
                                  "time": unit.get("time"),
                                  "source": "launcher pid"})
            session = value.get("session")
            if (isinstance(session, str) and len(session) == 36
                    and "capture" in str(value.get("schema") or "").lower()):
                processes.append({"pid": None, "session": session,
                                  "time": unit.get("time"),
                                  "source": "capture session"})
        for match in _PS_EU4.finditer(text):
            processes.append({"pid": int(match.group(1)), "session": None,
                              "time": unit.get("time"), "source": "ps eu4"})
        for match in _PID_JSON.finditer(text):
            if re.search(r"launcher|termination|\./eu4\b|/eu4\b", text,
                         re.IGNORECASE):
                processes.append({"pid": int(match.group(1)), "session": None,
                                  "time": unit.get("time"),
                                  "source": "pid field"})
        for match in _TERM_TAG.finditer(text):
            terminations.append({"pid": None, "tag": match.group(1),
                                 "time": unit.get("time"),
                                 "source": "terminated summary"})
        for match in list(_SESSION_JSON.finditer(text)) + list(
                _SESSION_FIELD.finditer(text)):
            if re.search(r"capture|launcher|diagnostic", text,
                         re.IGNORECASE):
                processes.append({"pid": None, "session": match.group(1),
                                  "time": unit.get("time"),
                                  "source": "session field"})
        counter_match = re.search(r"counts\s*\{([^{}]*)\}", text)
        if counter_match and "eu4_launches" in counter_match.group(1):
            number = re.search(r"['\"]eu4_launches['\"]\s*:\s*(\d+)",
                               counter_match.group(1))
            if number:
                counts.append({"count": int(number.group(1)), "state": state,
                               "time": unit.get("time")})
    for attempt in attempts:
        attempt.setdefault("claim", None)
        attempt.setdefault("process", None)
        attempt.setdefault("termination", None)
        attempt["status"] = "unclaimed"
        attempt["missing"] = []

    # Explicit claims are the strongest evidence: match the output path.
    for attempt in attempts:
        key = _evidence_key(attempt.get("output"))
        if not key:
            continue
        for claim in claims:
            if claim["output"] == key or claim["output"].endswith(key) \
                    or key.endswith(claim["output"]):
                attempt["claim"] = {"kind": "claim_launch",
                                    "reservation": claim.get("source"),
                                    "source": claim.get("source")}
                break

    # A guard status count covers that state's earliest launch attempts.  It is
    # weaker than an explicit claim but still records consumed entitlement.
    by_state: dict[str, list[dict]] = {}
    for attempt in attempts:
        state = attempt.get("state")
        if state:
            by_state.setdefault(state, []).append(attempt)
    for state, group in by_state.items():
        relevant = [row for row in counts if row.get("state") == state]
        if not relevant:
            continue
        available = max(row["count"] for row in relevant)
        for attempt in sorted(group, key=lambda row: row.get("time") or 0
                              )[:max(0, available)]:
            if attempt["claim"] is None:
                attempt["claim"] = {"kind": "guard_count",
                                    "source": "guard status counts"}

    # Process/session identity: prefer an attempt tag in the evidence unit,
    # fall back to the most recent preceding attempt.
    for record in processes:
        target = _associate_attempt(record, attempts, evidence)
        if target is not None and target["process"] is None:
            target["process"] = record

    # Termination: PID first, then the printed capture tag.
    for record in terminations:
        target = None
        if record.get("pid") is not None:
            for attempt in attempts:
                process = attempt.get("process") or {}
                if process.get("pid") == record["pid"]:
                    target = attempt
                    break
        if target is None and record.get("tag"):
            for attempt in attempts:
                if _tag_in_text(record["tag"], str(attempt.get("output") or "")):
                    target = attempt
                    break
        if target is not None and target["termination"] is None:
            target["termination"] = record

    confirmed = []
    ambiguous = []
    unclaimed = []
    failed = []
    for attempt in attempts:
        row = {
            "command": attempt["command"],
            "output": attempt.get("output"),
            "time": attempt.get("time"),
            "claim": attempt.get("claim"),
            "process": attempt.get("process"),
            "termination": attempt.get("termination"),
            "failed": bool(attempt.get("failed")),
        }
        missing = [name for name in ("claim", "process", "termination")
                   if not attempt.get(name)]
        row["missing"] = missing
        row["corroborated"] = not missing
        if attempt.get("failed"):
            row["status"] = "failed"
            failed.append(row)
            continue
        if not missing:
            row["status"] = "confirmed"
            confirmed.append(row)
        elif attempt.get("claim") or attempt.get("process") \
                or attempt.get("termination"):
            row["status"] = "ambiguous"
            if attempt.get("claim") and not attempt.get("termination"):
                row["reason"] = "claim_without_termination"
            elif attempt.get("claim") and not attempt.get("process"):
                row["reason"] = "claim_without_process_identity"
            elif attempt.get("process") and not attempt.get("claim"):
                row["reason"] = "launch_without_guard_claim"
            else:
                row["reason"] = "incomplete_launch_chain"
            ambiguous.append(row)
        else:
            row["status"] = "unclaimed"
            row["reason"] = "launch_command_without_guard_claim"
            unclaimed.append(row)
    return {
        "confirmed": confirmed,
        "ambiguous": ambiguous,
        "unclaimed": unclaimed,
        "failed": failed,
        "claims": claims,
        "counts": counts,
    }


def _as_pid(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _associate_attempt(record: dict, attempts: list[dict],
                       evidence: list[dict]) -> dict | None:
    """Attach a process record to a launch attempt by tag, else by time."""
    for unit in evidence:
        if unit.get("time") != record.get("time"):
            continue
        haystack = str(unit.get("text") or "") + " " + str(
            unit.get("command") or "")
        matches = []
        for attempt in attempts:
            tag = _launch_tag(attempt.get("output"))
            if not tag or tag.lower() in _GENERIC_TAGS:
                continue
            if _tag_in_text(tag, haystack):
                matches.append(attempt)
        if len(matches) == 1:
            return matches[0]
    moment = record.get("time")
    if moment is None:
        return None
    prior = [attempt for attempt in attempts
             if attempt.get("time") is not None
             and attempt["time"] <= moment]
    if not prior:
        return None
    return max(prior, key=lambda row: row["time"])


def read_events(path: str | Path) -> list[dict]:
    """Read a session export, transparently handling a zstd-compressed one."""
    path = Path(path).expanduser()
    if path.is_dir():
        matches = sorted(path.glob("session*.jsonl.zstd")) or sorted(
            path.glob("session*.jsonl"))
        if not matches:
            raise ValueError(f"no session JSONL export in {path}")
        path = matches[0]
    if path.suffix == ".zstd":
        completed = subprocess.run(
            ["zstd", "-dc", str(path)], capture_output=True, text=True,
            check=False, timeout=300)
        if completed.returncode != 0:
            raise ValueError(f"cannot decompress {path}: "
                             f"{completed.stderr.strip()[-500:]}")
        text = completed.stdout
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _time(event: dict) -> float | None:
    value = event.get("time")
    return float(value) / 1000.0 if isinstance(value, (int, float)) else None


def _iso(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return dt.datetime.fromtimestamp(seconds, tz=dt.timezone.utc).isoformat()


def _arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    return value if isinstance(value, dict) else {"_value": value}


def _result_text(data: dict) -> tuple[str, bool]:
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    chunks = []
    for item in message.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            chunks.append(str(item.get("text") or ""))
    text = "".join(chunks)
    lowered = text.lower()
    truncated = any(marker in lowered for marker in _SPILL)
    if data.get("error"):
        truncated = False
    return text, truncated


def _sanitize(text: str) -> str:
    """Remove machine-specific paths before a summary can be committed."""
    value = str(text)
    home = os.environ.get("HOME")
    if home:
        value = value.replace(home, "<home>")
    value = re.sub(r"/Users/[^/\s]+", "<home>", value)
    value = re.sub(r"/private/var/folders/[^\s\"']+", "<tmp>", value)
    value = re.sub(r"/var/folders/[^\s\"']+", "<tmp>", value)
    return value


def _normalize_command(command: str) -> str:
    value = re.sub(r"\s+", " ", command).strip()
    value = re.sub(r"/[^\s'\"]+", "<path>", value)
    return value


_HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")


def _command_without_heredoc(command: str) -> str:
    """Drop heredoc bodies so edited source text is not counted as a launch."""
    output = []
    marker = None
    for line in command.splitlines():
        if marker is None:
            match = _HEREDOC.search(line)
            if match:
                marker = match.group(1)
            output.append(line)
            continue
        if line.strip() == marker:
            marker = None
            output.append(line)
    return "\n".join(output)


def _test_signature(command: str) -> str | None:
    """Canonical identity of one unittest/pytest invocation.

    Redirects, pipes, tails and shell wrappers are removed so the same suite
    spelled three different ways is counted once as a repeat.
    """
    if _FULL_SUITE.search(command):
        return "unittest discover -s tests"
    match = re.search(r"-m\s+unittest\s+([^|;&>]+)", command)
    if match:
        targets = [token for token in match.group(1).split()
                   if token.startswith(("tests.", "test_", "benchmark."))]
        if targets:
            return "unittest " + " ".join(sorted(set(targets)))
        return "unittest " + " ".join(sorted(set(match.group(1).split())))
    match = re.search(r"-m\s+pytest\s+([^|;&>]+)", command)
    if match:
        return "pytest " + " ".join(sorted(set(match.group(1).split())))
    return None


def filter_turn(events: list[dict], turn: int | None) -> list[dict]:
    """Keep one turn, including jobs and session-level events in its window."""
    if turn is None:
        return events
    start = end = None
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if data.get("turn") != turn:
            continue
        moment = _time(event)
        if moment is None:
            continue
        if event.get("type") == "turn/start" and start is None:
            start = moment
        elif event.get("type") == "turn/end":
            end = moment
    if start is None:
        return [
            event for event in events
            if event.get("type") == "session"
            or (isinstance(event.get("data"), dict)
                and event["data"].get("turn") == turn)
        ]
    end = end if end is not None else max(
        (moment for event in events
         if (moment := _time(event)) is not None), default=start)
    selected = []
    for event in events:
        if event.get("type") == "session":
            selected.append(event)
            continue
        moment = _time(event)
        if moment is not None and start <= moment <= end:
            selected.append(event)
    return selected


def analyze(
    events: list[dict],
    *,
    oversized_chars: int = OVERSIZED_CHARS,
    long_job_seconds: float = LONG_JOB_SECONDS,
    top: int = TOP,
) -> dict:
    """Aggregate one export into the bounded efficiency metrics."""
    calls: dict[str, dict] = {}
    call_order: list[dict] = []
    results: dict[str, dict] = {}
    jobs: dict[str, dict] = {}
    tokens = collections.Counter()
    model_calls = 0
    goal_events = []
    human = collections.Counter()
    turn_end_reasons = []
    timestamps: list[float] = []
    session = {}
    tool_wait = collections.Counter()
    tool_wait_calls = collections.Counter()
    wait_intervals: list[tuple[float, float]] = []

    for event in events:
        kind = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        moment = _time(event)
        if moment is not None:
            timestamps.append(moment)
        if kind == "session":
            session = {
                "id": event.get("id"),
                "cwd": _sanitize(str(event.get("cwd") or "")),
                "created_utc": _iso(
                    float(event["createdAt"]) / 1000.0
                    if isinstance(event.get("createdAt"), (int, float)) else None),
            }
        elif kind == "tool/call":
            call_id = str(data.get("callId") or "")
            name = str(data.get("name") or "unknown")
            arguments = _arguments(data.get("arguments"))
            calls[call_id] = {"name": name, "arguments": arguments,
                              "time": moment}
            call_order.append({"id": call_id, "name": name,
                               "arguments": arguments, "time": moment})
        elif kind == "tool/result":
            call_id = str(data.get("toolCallId") or "")
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            call_id = call_id or str((message.get("source") or {}).get("callId") or "")
            text, truncated = _result_text(data)
            name = (calls.get(call_id) or {}).get("name")
            results[call_id] = {
                "is_error": bool(message.get("isError")) or bool(data.get("error")),
                "chars": len(text),
                "truncated": truncated,
                "time": moment,
                "text": text[-2000:] if name == "bash" else "",
                # Evidence joins need the complete result, not the bounded
                # display excerpt.  The full text is never serialized.
                "evidence_text": text,
            }
        elif kind == "job/registered":
            jobs[str(data.get("jobId"))] = {
                "label": str(data.get("label") or ""),
                "kind": data.get("kind"),
                "started": data.get("startedAt"),
                "status": None,
                "call_id": str((data.get("toolCall") or {}).get("callId") or ""),
            }
        elif kind == "job/settled":
            job = jobs.setdefault(str(data.get("jobId")), {})
            job.update({
                "label": str(data.get("label") or job.get("label") or ""),
                "started": data.get("startedAt"),
                "finished": data.get("finishedAt"),
                "status": data.get("status"),
                "detail": str(data.get("detail") or ""),
                "call_id": str((data.get("toolCall") or {}).get(
                    "callId") or job.get("call_id") or ""),
            })
        elif kind == "assistant/message":
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            if usage:
                model_calls += 1
                for key in ("inputTokens", "outputTokens", "totalTokens",
                            "cacheReadTokens", "cacheWriteTokens"):
                    value = usage.get(key)
                    if isinstance(value, (int, float)):
                        tokens[key] += int(value)
        elif kind == "goal/change":
            goal = data.get("goal") if isinstance(data.get("goal"), dict) else {}
            cleared = (data.get("cleared")
                       if isinstance(data.get("cleared"), dict) else {})
            operation = data.get("operation")
            phase = goal.get("phase")
            if phase is None and operation == "clear":
                # DSH clear tombstones carry the identity under ``cleared``
                # and no goal snapshot.
                phase = "cleared"
            goal_events.append({
                "goal_id": goal.get("id") or cleared.get("id"),
                "revision": goal.get("revision") or cleared.get("revision"),
                "operation": operation,
                "phase": phase,
                "time": moment,
            })
        elif kind == "command/run":
            if data.get("name") == "permission":
                human["permission_commands"] += 1
        elif kind == "approval/policy":
            human["approval_policy_events"] += 1
        elif kind == "user/message":
            source = data.get("source") or {}
            text = "".join(
                str(item.get("text") or "")
                for item in data.get("content") or [] if isinstance(item, dict))
            if source.get("kind") == "user" and "system-reminder" not in text \
                    and "runtime context" not in text:
                human["user_messages"] += 1
        elif kind == "agent/inbox/spliced":
            for item in data.get("inserted") or []:
                if isinstance(item, dict) and (item.get("source") or {}).get(
                        "kind") == "user-approval":
                    human["approval_interventions"] += 1
        elif kind == "turn/end":
            reason = (data.get("reason") or {}).get("kind")
            if reason:
                turn_end_reasons.append(str(reason))
        elif kind == "turn/start":
            human["turns"] += 1

    # Per-tool wait: the gap between a call and its result. Parallel calls make
    # this an attribution, not a serial wall-clock number; it is reported as
    # "tool_wait_seconds" and never as elapsed session time.
    for call_id, result in results.items():
        call = calls.get(call_id)
        if not call or call.get("time") is None or result.get("time") is None:
            continue
        delta = max(0.0, result["time"] - call["time"])
        tool_wait[call["name"]] += delta
        tool_wait_calls[call["name"]] += 1
        wait_intervals.append((call["time"], result["time"]))

    by_tool = collections.Counter(call["name"] for call in call_order)
    failures = [call for call in call_order
                if (results.get(call["id"]) or {}).get("is_error")]
    failures_by_tool = collections.Counter(call["name"] for call in failures)
    shell_failures = []
    shell_commands = 0
    _exit_marker = re.compile(
        r"(?:\[exit code:\s*([1-9]\d*)\]|(?:^|[\s_])exit(?: code)?[=:]\s*"
        r"([1-9]\d*)\b)", re.MULTILINE)
    for call in call_order:
        if call["name"] != "bash":
            continue
        shell_commands += 1
        result = results.get(call["id"]) or {}
        text = str(result.get("text") or "")
        matches = list(_exit_marker.finditer(text))
        if not matches:
            continue
        match = matches[-1]
        code = int(match.group(1) or match.group(2))
        shell_failures.append({
            "exit_code": code,
            "command_excerpt": _sanitize(_excerpt(call["arguments"])),
        })
    repeated = collections.Counter()
    repeated_examples: dict[str, dict] = {}
    for call in call_order:
        signature = call["name"] + "|" + json.dumps(
            call["arguments"], sort_keys=True, separators=(",", ":"))
        repeated[signature] += 1
        repeated_examples.setdefault(signature, call)

    oversized = []
    truncated_results = []
    for call in call_order:
        result = results.get(call["id"])
        if not result:
            continue
        if result["chars"] > oversized_chars:
            oversized.append({
                "tool": call["name"], "chars": result["chars"],
                "truncated": result["truncated"],
                "argument_excerpt": _sanitize(_excerpt(call["arguments"])),
            })
        if result["truncated"]:
            truncated_results.append({
                "tool": call["name"], "chars": result["chars"],
                "argument_excerpt": _sanitize(_excerpt(call["arguments"])),
            })
    oversized.sort(key=lambda row: row["chars"], reverse=True)

    long_jobs = []
    for job in jobs.values():
        started, finished = job.get("started"), job.get("finished")
        if not isinstance(started, (int, float)):
            continue
        seconds = ((finished - started) / 1000.0
                   if isinstance(finished, (int, float)) else None)
        if seconds is not None and seconds < long_job_seconds:
            continue
        long_jobs.append({
            "label": _sanitize(job.get("label", ""))[:160],
            "seconds": round(seconds, 3) if seconds is not None else None,
            "status": job.get("status"),
        })
    long_jobs.sort(key=lambda row: row.get("seconds") or 0, reverse=True)

    test_commands = collections.Counter()
    test_spellings: dict[str, str] = {}
    full_suite = 0
    for call in call_order:
        command = _command_without_heredoc(
            str(call["arguments"].get("command") or ""))
        if not _TEST_COMMAND.search(command):
            continue
        if _FULL_SUITE.search(command):
            full_suite += 1
        signature = _test_signature(command)
        if signature is None:
            continue
        test_commands[signature] += 1
        test_spellings.setdefault(signature, _sanitize(_normalize_command(command)))
    duplicate_tests = [
        {"command": test_spellings.get(signature, signature),
         "signature": signature, "count": count}
        for signature, count in test_commands.items() if count > 1
    ]
    duplicate_tests.sort(key=lambda row: row["count"], reverse=True)

    runner_rows = []
    for call in call_order:
        command = _command_without_heredoc(
            str(call["arguments"].get("command") or ""))
        if not command:
            continue
        for row in _runner_evidence(command):
            row["call_id"] = call["id"]
            row["time"] = call["time"]
            runner_rows.append(row)
    runner_unique = []
    runner_seen: set[str] = set()
    runner_repeated = 0
    for row in runner_rows:
        if row["signature"] in runner_seen:
            runner_repeated += 1
            continue
        runner_seen.add(row["signature"])
        runner_unique.append(row)
    runner_focused = [row for row in runner_unique
                      if row["kind"] in ("focused", "pattern")]
    runner_full = [row for row in runner_unique if row["kind"] == "full"]
    # Count retained test totals once per tool call.  Re-reading the same
    # result for every command in one shell call double-counted it; when one
    # call contains several runner commands, only an unambiguous one-Ran-line
    # per command is summed, otherwise the total is unknown.
    rows_by_call: dict[str, list[dict]] = {}
    for row in runner_unique:
        rows_by_call.setdefault(row["call_id"], []).append(row)
    runner_tests_executed = None
    runner_test_values = []
    for call_id, rows in rows_by_call.items():
        text = str((results.get(call_id) or {}).get("evidence_text") or "")
        if len(rows) == 1:
            executed = _runner_counted_tests(text)
            if executed is not None:
                runner_test_values.append(executed)
            continue
        matches = [int(value) for value in
                   re.findall(r"(?m)^Ran (\d+) tests? in ", text)]
        if len(matches) == len(rows):
            runner_test_values.append(sum(matches))
        # Otherwise leave this call unknown rather than attributing output to
        # the wrong command.
    if runner_test_values:
        runner_tests_executed = sum(runner_test_values)

    attempts = []
    launches = []
    launch_tool_calls = 0
    bash_launches = 0
    for call in call_order:
        command = str(call["arguments"].get("command") or "")
        if call["name"] in ("eu4_diff_run", "eu4_capture"):
            launched = call["name"] == "eu4_diff_run" or (
                call["name"] == "eu4_capture"
                and call["arguments"].get("op") == "probe")
            if launched:
                launch_tool_calls += 1
                launches.append({"kind": "tool", "tool": call["name"],
                                 "excerpt": _sanitize(_excerpt(call["arguments"]))})
                attempts.append({
                    "kind": "tool", "tool": call["name"], "call_id": call["id"],
                    "time": call["time"], "output": None, "state": None,
                    "command": _sanitize(_excerpt(call["arguments"])),
                    "failed": False,
                })
            continue
        command = _command_without_heredoc(command) if command else command
        if not command:
            continue
        for match in _CAPTURE_COMMAND.finditer(command):
            segment = _valid_command_segment(command, match.start(), 800)
            if re.search(r"(?:^|\s)(?:--help|-h)\b", segment):
                continue
            bash_launches += 1
            launches.append({
                "kind": "bash",
                "excerpt": _sanitize(segment[:240]),
            })
            attempts.append({
                "kind": "capture", "call_id": call["id"], "time": call["time"],
                "output": _launch_output(segment),
                "state": _state_path(command),
                "command": _sanitize(segment[:240]),
                "failed": _attempt_failed(call["id"], results, jobs),
            })
        for match in _EU4_COMMAND.finditer(command):
            segment = _valid_command_segment(command, match.start(), 800)
            if re.search(r"(?:^|\s)(?:--help|-h)\b", segment):
                continue
            bash_launches += 1
            launches.append({"kind": "bash", "excerpt": _sanitize(segment[:240])})
            attempts.append({
                "kind": "launch", "call_id": call["id"], "time": call["time"],
                "output": _launch_output(segment),
                "state": _state_path(command),
                "command": _sanitize(segment[:240]),
                "failed": _attempt_failed(call["id"], results, jobs),
            })

    evidence_units = []
    for call in call_order:
        result = results.get(call["id"]) or {}
        text = str(result.get("evidence_text") or "")
        command = _command_without_heredoc(
            str(call["arguments"].get("command") or ""))
        if text or command:
            evidence_units.append({
                "call_id": call["id"],
                "time": result.get("time", call.get("time")),
                "text": text,
                "command": command,
            })
    launch_join = _join_physical_launches(attempts, evidence_units)
    confirmed_launches = len(launch_join["confirmed"])

    def _union_seconds(intervals):
        if not intervals:
            return 0.0
        ordered = sorted((start, end) for start, end in intervals
                         if end >= start)
        total = 0.0
        current_start = current_end = None
        for start, end in ordered:
            if current_start is None:
                current_start, current_end = start, end
            elif start <= current_end:
                current_end = max(current_end, end)
            else:
                total += current_end - current_start
                current_start, current_end = start, end
        if current_start is not None:
            total += current_end - current_start
        return total

    wall_seconds = (max(timestamps) - min(timestamps)) if timestamps else 0.0
    wait_sum_seconds = sum(tool_wait.values())
    wait_union_seconds = _union_seconds(wait_intervals)
    job_intervals = []
    for job in jobs.values():
        started, finished = job.get("started"), job.get("finished")
        if not isinstance(started, (int, float)) \
                or not isinstance(finished, (int, float)):
            continue
        start, end = started / 1000.0, finished / 1000.0
        if end >= start:
            job_intervals.append((start, end))
    job_service_sum_seconds = sum(end - start for start, end in job_intervals)
    job_service_union_seconds = _union_seconds(job_intervals)
    combined_service_union_seconds = _union_seconds(
        wait_intervals + job_intervals)
    input_tokens = tokens["inputTokens"]
    cache_read = tokens["cacheReadTokens"]
    total_tokens = tokens["totalTokens"] or (input_tokens + cache_read + tokens["outputTokens"])
    goal_operations = [row["operation"] for row in goal_events]
    terminal_phases = {
        "complete": "completed",
        "completed": "completed",
        "blocked": "blocked",
        "cancelled": "cancelled",
        "cleared": "cleared",
    }
    goal_ids: list[str] = []
    for row in goal_events:
        goal_id = row.get("goal_id")
        if goal_id is not None and goal_id not in goal_ids:
            goal_ids.append(goal_id)
    identity_missing = bool(goal_events) and any(
        row.get("goal_id") is None for row in goal_events)
    selected_id = goal_ids[-1] if goal_ids else None
    if selected_id is not None:
        selected_events = [row for row in goal_events
                           if row.get("goal_id") == selected_id]
    elif identity_missing:
        # Without ids we cannot separate sequential goals.  Use the only
        # grouping available and state the limitation instead of silently
        # attributing one goal's start to another.
        selected_events = list(goal_events)
    else:
        selected_events = []
    last_goal = selected_events[-1] if selected_events else None
    last_phase = last_goal["phase"] if last_goal else None
    # A clear tombstone may follow a substantive terminal phase (complete or
    # blocked).  Keep that outcome and end time; the clear is recorded
    # separately rather than replacing the result.
    cleared_event = last_goal if last_phase == "cleared" else None
    substantive_phase = last_phase
    if cleared_event is not None:
        for row in reversed(selected_events[:-1]):
            phase = row.get("phase")
            if (phase not in (None, "cleared")
                    and row.get("operation") != "edit"):
                substantive_phase = phase
                break
        else:
            # Issue #232: a window can contain only an edit snapshot of an
            # already-complete/blocked goal plus its clear tombstone.  Use the
            # edit's phase as the substantive outcome, but never its timestamp
            # as the end: the original terminal transition is not in-window.
            for row in reversed(selected_events[:-1]):
                phase = row.get("phase")
                if (phase not in (None, "cleared", "active", "paused")
                        and row.get("operation") == "edit"):
                    substantive_phase = phase
                    break
    terminal_event = None
    terminal_operations = {"complete", "block", "clear", "cancel",
                           "cancelled"}
    if substantive_phase in terminal_phases:
        # The transition that produced the current phase, not an arbitrary
        # historical terminal phase or a later edit of the same phase.
        for row in reversed(selected_events):
            if (row.get("phase") == substantive_phase
                    and row.get("operation") in terminal_operations):
                terminal_event = row
                break
        if terminal_event is None:
            for row in reversed(selected_events):
                if (row.get("phase") == substantive_phase
                        and row.get("operation") != "edit"):
                    terminal_event = row
                    break
    elif cleared_event is not None:
        terminal_event = cleared_event
    start_event = next(
        (row for row in selected_events if row.get("operation") == "create"),
        None)
    goal_start = start_event["time"] if start_event else None
    goal_end = terminal_event["time"] if terminal_event else None
    window_end = max(timestamps) if timestamps else None
    duration_kind = "no_goal" if not selected_events else (
        "terminal" if terminal_event is not None else "ongoing")
    duration_invalid = False
    goal_elapsed = None
    if substantive_phase in terminal_phases and terminal_event is None:
        duration_kind = "unknown_end"
    elif terminal_event is not None:
        if goal_start is None:
            duration_kind = "unknown_start"
        elif goal_end is None:
            duration_kind = "unknown_timestamp"
        else:
            goal_elapsed = goal_end - goal_start
    elif selected_events:
        # A non-terminal goal (active or paused) has no end event.  Report
        # observed elapsed time through the observation window, explicitly
        # labelled ongoing.
        if goal_start is None:
            # The window may start after the goal was created (for example
            # ``--turn 2``); a zero-length fallback would fabricate a lifetime.
            duration_kind = "unknown_start"
        elif window_end is not None:
            goal_elapsed = window_end - goal_start
        else:
            duration_kind = "unknown_timestamp"
    if goal_elapsed is not None and goal_elapsed < 0:
        # Out-of-order events cannot yield a negative duration.
        goal_elapsed = None
        duration_invalid = True
        duration_kind = "invalid_order"
    if last_goal is None:
        # No goal record exists: turn completion is reported separately and is
        # never promoted to a goal outcome.
        goal_outcome = "no_goal"
    elif substantive_phase in terminal_phases:
        goal_outcome = terminal_phases[substantive_phase]
    elif last_phase == "active":
        goal_outcome = "active"
    else:
        goal_outcome = str(last_phase or "unknown")
    value = {
        "schema": SCHEMA,
        "session": session,
        "window": {
            "start_utc": _iso(min(timestamps)) if timestamps else None,
            "end_utc": _iso(max(timestamps)) if timestamps else None,
            "elapsed_seconds": round(wall_seconds, 3),
            "turns": human.get("turns", 0),
            "model_calls": model_calls,
        },
        "calls": {
            "total": len(call_order),
            "by_tool": dict(by_tool.most_common()),
            "failed": len(failures),
            "failure_rate": round(len(failures) / len(call_order), 4) if call_order else 0.0,
            "failed_by_tool": dict(failures_by_tool.most_common()),
            "shell_commands": shell_commands,
            "shell_failures": len(shell_failures),
            "shell_failure_examples": shell_failures[:top],
            "note": (
                "failed counts tool-level errors; shell_failures counts bash "
                "commands whose result text reports a nonzero exit code"),
        },
        "repeated_calls": [
            {
                "tool": repeated_examples[signature]["name"],
                "count": count,
                "argument_excerpt": _sanitize(_excerpt(
                    repeated_examples[signature]["arguments"])),
            }
            for signature, count in repeated.most_common(top) if count > 1
        ],
        "output": {
            "total_chars": sum(row["chars"] for row in results.values()),
            "max_chars": max((row["chars"] for row in results.values()), default=0),
            "oversized": oversized[:top],
            "oversized_count": len(oversized),
            "truncated": truncated_results[:top],
            "truncated_count": len(truncated_results),
            "threshold_chars": oversized_chars,
        },
        "wait": {
            "tool_wait_seconds": round(sum(tool_wait.values()), 3),
            "by_tool": {name: round(value, 3)
                        for name, value in tool_wait.most_common()},
            "calls_measured": dict(tool_wait_calls),
            "share_of_elapsed": (round(sum(tool_wait.values()) / wall_seconds, 4)
                                 if wall_seconds else None),
        },
        "long_jobs": {
            "threshold_seconds": long_job_seconds,
            "count": len(long_jobs),
            "jobs": long_jobs[:top],
        },
        "tests": {
            "test_invocations": (sum(test_commands.values())
                                 + len(runner_unique)),
            "unittest_invocations": sum(test_commands.values()),
            "runner_invocations": len(runner_unique),
            "runner_focused_invocations": len(runner_focused),
            "runner_full_invocations": len(runner_full),
            "runner_repeated_invocations": runner_repeated,
            "runner_tests_executed": runner_tests_executed,
            "runner_evidence": runner_unique[:top],
            "runner_tests_note": (
                "executed tests are read from retained runner output; None "
                "means the result text did not include a count"),
            "full_suite_invocations": full_suite + len(runner_full),
            "duplicate_invocations": (
                sum(count - 1 for count in test_commands.values()
                    if count > 1) + runner_repeated),
            "duplicates": duplicate_tests[:top],
            "distinct_commands": len(test_commands) + len(runner_unique),
        },
        "physical_work": {
            "runner_invocations": len(runner_focused),
            "runner_focused_invocations": len(runner_focused),
            "runner_full_invocations": len(runner_full),
            "runner_repeated_invocations": runner_repeated,
            "runner_tests_executed": runner_tests_executed,
            "runner_evidence": runner_focused[:top],
            "game_launch_commands": launch_tool_calls + bash_launches,
            "confirmed_game_launches": confirmed_launches,
            "confirmed_launch_evidence": launch_join["confirmed"][:top],
            "ambiguous_launch_attempts": launch_join["ambiguous"][:top],
            "unclaimed_launch_attempts": launch_join["unclaimed"][:top],
            "failed_launch_attempts": launch_join["failed"][:top],
            "confirmed_game_launches_note": (
                "confirmed only when a guard claim, a process/session identity "
                "and a termination record join for the same attempt; a command "
                "match alone is a request"),
            "confirmation_rule": (
                "guard claim + (launcher/ps PID or capture session) + "
                "termination record for the same attempt"),
            "gpu_service_seconds": None,
            "gpu_service_note": (
                "GPU execution is not separable from CPU/filesystem wait in "
                "this session trace; it is never inferred from wall time"),
        },
        "service": {
            "client_wall_seconds": round(wall_seconds, 3),
            "tool_wait_sum_seconds": round(wait_sum_seconds, 3),
            "tool_wait_union_seconds": round(wait_union_seconds, 3),
            "overlapping_wait_seconds": round(
                max(0.0, wait_sum_seconds - wait_union_seconds), 3),
            "job_service_sum_seconds": round(job_service_sum_seconds, 3),
            "job_service_union_seconds": round(job_service_union_seconds, 3),
            "combined_service_union_seconds": round(
                combined_service_union_seconds, 3),
            "goal_service_seconds": (
                round(goal_elapsed, 3) if goal_elapsed is not None else None),
            "gpu_service_seconds": None,
            "unknown": ["gpu_service_seconds"],
            "semantics": (
                "tool waits and job service are unioned separately from "
                "user-visible elapsed time and from the goal lifetime"),
        },
        "eu4_launches": {
            "count": launch_tool_calls + bash_launches,
            "confirmed_count": confirmed_launches,
            "launch_tool_calls": launch_tool_calls,
            "bash_launches": bash_launches,
            "command_evidence": launches[:top],
            "count_semantics": (
                "command invocations detected in the session; confirmed_count "
                "is the joined guard/PID/termination result"),
            "process_starts_estimate": (launch_tool_calls + bash_launches) * 2,
            "process_starts_note": (
                "legacy heuristic retained for readers; it is not a measurement "
                "and must not be used as a launch count"),
        },
        "human": {
            "user_messages": human.get("user_messages", 0),
            "approval_policy_events": human.get("approval_policy_events", 0),
            "approval_interventions": human.get("approval_interventions", 0),
            "permission_commands": human.get("permission_commands", 0),
            "ask_user_question_calls": by_tool.get("ask_user_question", 0),
        },
        "tokens": {
            # The DSH usage record separates uncached input from cache reads;
            # total_tokens is input + cache_read + output.
            "input": input_tokens,
            "cache_read": cache_read,
            "cache_write": tokens["cacheWriteTokens"],
            "output": tokens["outputTokens"],
            "total": total_tokens,
            "effective_input": input_tokens + cache_read,
            "cache_read_share_of_total": (round(cache_read / total_tokens, 4)
                                          if total_tokens else None),
        },
        "goal": {
            "goal_schema": "dsh-goal-timeline/v2",
            "operations": goal_operations,
            "goal_ids": goal_ids,
            "goal_count": len(goal_ids) + (1 if identity_missing else 0),
            "selected_goal_id": selected_id,
            "identity_missing": identity_missing,
            "selected_operations": [row["operation"]
                                    for row in selected_events],
            "last_operation": last_goal["operation"] if last_goal else None,
            "last_phase": last_phase,
            "active": bool(last_goal and last_phase == "active"),
            "paused": bool(last_goal and last_phase == "paused"),
            "cleared": bool(cleared_event is not None),
            "cleared_utc": (_iso(cleared_event["time"])
                            if cleared_event is not None else None),
            "substantive_phase": substantive_phase,
            "terminal": bool(last_goal and (
                substantive_phase in terminal_phases
                or cleared_event is not None)),
            "started_utc": _iso(goal_start),
            "ended_utc": (_iso(goal_end)
                          if terminal_event is not None
                          and not duration_invalid else None),
            "unknown_end": duration_kind == "unknown_end",
            "duration_kind": duration_kind,
            "duration_invalid": duration_invalid,
            "duration_seconds": (round(goal_elapsed, 3)
                                 if goal_elapsed is not None else None),
            "observed_until_utc": (_iso(window_end)
                                   if duration_kind == "ongoing" else None),
            "turn_end_reasons": turn_end_reasons,
            "last_turn_reason": (turn_end_reasons[-1]
                                 if turn_end_reasons else None),
            # The final goal/change record for the selected goal owns the
            # outcome.  A completed turn after a blocked goal must not read as
            # a completed migration.
            "outcome": goal_outcome,
            "limitations": (
                ["goal events have no stable goal id; sequential goals cannot "
                 "be separated"] if identity_missing else []
            ) + (
                ["goal event timestamps are missing or out of order"] if
                duration_kind in ("unknown_timestamp", "invalid_order") else []
            ) + (
                [("the selected goal has no create event in this window; its "
                  "start is unknown") if start_event is None else
                 ("the selected goal's create event has no timestamp; its "
                  "start is unknown")] if duration_kind == "unknown_start"
                else []
            ) + (
                ["the selected goal's terminal transition is outside this "
                 "window; its end is unknown"] if duration_kind == "unknown_end"
                else []),
        },
    }
    value["summary"] = execution_summary(value)
    return value


def execution_summary(value: dict) -> dict:
    """Compact P2 execution table derived from the existing measurement.

    Values that the session export cannot support (for example GL-versus-Metal
    replay counts or GPU checkpoint overhead) are explicit ``None`` with the
    reason, never a measured-looking zero.  Token telemetry is deliberately
    excluded from the summary until its measurement method is validated.
    """
    calls = value.get("calls") or {}
    by_tool = calls.get("by_tool") or {}
    try:
        from automation.agent_tools import TOOLS as _native

        native_names = set(_native)
    except Exception:  # noqa: BLE001 - the metric is still usable
        native_names = {
            "eu4_re_query", "eu4_research", "eu4_session", "eu4_capture",
            "eu4_diff_run", "eu4_diff_inspect", "eu4_replay",
            "eu4_case_inspect",
        }
    native_calls = sum(count for name, count in by_tool.items()
                       if name in native_names)
    shell_calls = by_tool.get("bash", 0)
    tests = value.get("tests") or {}
    launches = value.get("eu4_launches") or {}
    physical = value.get("physical_work") or {}
    service = value.get("service") or {}
    output = value.get("output") or {}
    goal = value.get("goal") or {}
    failures = calls.get("failed") or 0
    launch_count = launches.get("count")
    goal_outcome = str(goal.get("outcome") or "unknown")
    if goal.get("terminal"):
        # A bounded diagnosis that ends blocked stays blocked even when the
        # turn itself completed normally.
        outcome = goal_outcome
    elif launch_count is None:
        outcome = "unknown"
    elif launch_count:
        outcome = "launch_attempt"
    elif failures:
        outcome = "failed_tool_calls"
    elif goal_outcome == "active":
        outcome = "in_progress"
    else:
        outcome = goal_outcome
    actions_attempted = [
        name for name, performed in (
            ("launch", bool(launch_count)),
            ("replay", bool(by_tool.get("eu4_replay"))),
            ("capture", bool(by_tool.get("eu4_capture"))),
            ("test", bool(tests.get("test_invocations"))),
        ) if performed
    ]
    if len(actions_attempted) > 1:
        action_attempted = "multiple"
    elif actions_attempted:
        action_attempted = actions_attempted[0]
    elif launch_count is None:
        action_attempted = "unknown"
    else:
        action_attempted = "none"
    return {
        "schema": "dsh-execution-summary/v1",
        "tool_calls": {
            "total": calls.get("total"),
            "native": native_calls,
            "shell": shell_calls,
            "by_tool": dict(sorted(by_tool.items())),
        },
        "execution": {
            "wall_seconds": (value.get("window") or {}).get("elapsed_seconds"),
            "turns": (value.get("window") or {}).get("turns"),
            "model_calls": (value.get("window") or {}).get("model_calls"),
            "goal_seconds": goal.get("duration_seconds"),
            "goal_duration_kind": goal.get("duration_kind"),
            "goal_duration_invalid": goal.get("duration_invalid"),
        },
        "eu4_launches": launch_count,
        "confirmed_game_launches": physical.get("confirmed_game_launches"),
        "physical_work": {
            "runner_focused_invocations": physical.get(
                "runner_focused_invocations"),
            "runner_tests_executed": physical.get("runner_tests_executed"),
            "game_launch_commands": physical.get("game_launch_commands"),
            "confirmed_game_launches": physical.get(
                "confirmed_game_launches"),
            "ambiguous_launch_attempts": len(
                physical.get("ambiguous_launch_attempts") or []),
            "failed_launch_attempts": len(
                physical.get("failed_launch_attempts") or []),
        },
        "service": {
            "client_wall_seconds": service.get("client_wall_seconds"),
            "tool_wait_union_seconds": service.get(
                "tool_wait_union_seconds"),
            "job_service_union_seconds": service.get(
                "job_service_union_seconds"),
            "combined_service_union_seconds": service.get(
                "combined_service_union_seconds"),
            "goal_service_seconds": service.get("goal_service_seconds"),
            "gpu_service_seconds": service.get("gpu_service_seconds"),
        },
        "replays": {
            "total_tool_invocations": by_tool.get("eu4_replay", 0),
            "gl": None,
            "metal": None,
            "note": "per-backend replay counts need a run-event field; the "
                    "tool invocation count is reported instead",
        },
        "tests": {
            "executions": tests.get("test_invocations"),
            "unittest_executions": tests.get("unittest_invocations"),
            "runner_focused_invocations": tests.get(
                "runner_focused_invocations"),
            "runner_tests_executed": tests.get("runner_tests_executed"),
            "repetitions": tests.get("duplicate_invocations"),
            "skips": tests.get("skipped", None),
            "failures": tests.get("failures", None),
            "note": "skips/failures are not carried in the session export; "
                    "read the preserved test-run record for those counts",
        },
        "diagnostics": {
            "returned_chars": output.get("total_chars"),
            "oversized_results": output.get("oversized_count"),
            "truncated_results": output.get("truncated_count"),
            "artifact_bytes": None,
            "note": "artifact sizes are recorded on disk, not in the export",
        },
        "gpu_work": {
            "checkpoint_readbacks": None,
            "measured_overhead_seconds": None,
            "gpu_service_seconds": None,
            "note": "checkpoint and readback cost is in the replay report; GPU "
                    "duration is unknown here and is never inferred from wall "
                    "time",
        },
        "outcome": outcome,
        "goal_outcome": goal_outcome,
        "turn_outcome": goal.get("last_turn_reason"),
        "launch_attempted": bool(launch_count),
        "actions_attempted": actions_attempted,
        "action_attempted": action_attempted,
        "token_telemetry": "excluded until the measurement method is validated",
    }


def _excerpt(value, *, limit: int = 160) -> str:
    if isinstance(value, dict):
        text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    analyze_parser = sub.add_parser("analyze")
    analyze_parser.add_argument("path", help="session JSONL, .jsonl.zstd or session directory")
    analyze_parser.add_argument("--top", type=int, default=TOP)
    analyze_parser.add_argument("--oversized-chars", type=int,
                                default=OVERSIZED_CHARS)
    analyze_parser.add_argument("--long-job-seconds", type=float,
                                default=LONG_JOB_SECONDS)
    analyze_parser.add_argument("--turn", type=int, default=None,
                                help="restrict to one turn (the canary's investigation is turn 1)")
    analyze_parser.add_argument("--max-seq", type=int, default=None,
                                help="drop records after this export seq (a pinned window)")
    analyze_parser.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        events = read_events(args.path)
        if args.max_seq is not None:
            events = [event for event in events
                      if not isinstance(event.get("seq"), int)
                      or event["seq"] <= args.max_seq]
        value = analyze(
            filter_turn(events, args.turn),
            oversized_chars=args.oversized_chars,
            long_job_seconds=args.long_job_seconds, top=args.top)
        value["turn"] = args.turn
        value["max_seq"] = args.max_seq
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema": SCHEMA, "ok": False,
                          "reason": f"{type(exc).__name__}: {exc}"}))
        return 2
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
