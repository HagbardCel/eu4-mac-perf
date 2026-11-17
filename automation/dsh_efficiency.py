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
_EU4_COMMAND = re.compile(
    r"(?:^|[;&|]\s*)(?:[A-Za-z0-9_./-]*/)?(?:python[\d.]*\s+-m\s+)?"
    r"(?:automation\.)?(eu4_diff_run|gfx_dual_run|live_gfx)\b"
    r"|(?:open\s+-a\s+[\"']?[^\s\"']*(?:EU4|Europa))"
    r"|(?:[A-Za-z0-9_./ -]*Europa Universalis[^\s\"']*\.app)"
    r"|(?:[A-Za-z0-9_./ -]*eu4\.[A-Za-z0-9_]+\.exe)"
    r"|(?:[A-Za-z0-9_./-]*python[\d.]*\s+-m\s+(?:automation\.)?"
    r"m6_frame\s+capture)",
    re.IGNORECASE)
_SPILL = ("dsh-spill", "use read with offset/limit", "[truncated", "(truncated")


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
            }
        elif kind == "job/registered":
            jobs[str(data.get("jobId"))] = {
                "label": str(data.get("label") or ""),
                "kind": data.get("kind"),
                "started": data.get("startedAt"),
                "status": None,
            }
        elif kind == "job/settled":
            job = jobs.setdefault(str(data.get("jobId")), {})
            job.update({
                "label": str(data.get("label") or job.get("label") or ""),
                "started": data.get("startedAt"),
                "finished": data.get("finishedAt"),
                "status": data.get("status"),
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
            goal_events.append({
                "operation": data.get("operation"),
                "phase": (data.get("goal") or {}).get("phase"),
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
            continue
        command = _command_without_heredoc(command) if command else command
        if command and _EU4_COMMAND.search(command):
            bash_launches += 1
            launches.append({"kind": "bash",
                             "excerpt": _sanitize(command[:200])})

    wall_seconds = (max(timestamps) - min(timestamps)) if timestamps else 0.0
    input_tokens = tokens["inputTokens"]
    cache_read = tokens["cacheReadTokens"]
    total_tokens = tokens["totalTokens"] or (input_tokens + cache_read + tokens["outputTokens"])
    goal_operations = [row["operation"] for row in goal_events]
    last_goal = goal_events[-1] if goal_events else None
    return {
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
            "test_invocations": sum(test_commands.values()),
            "full_suite_invocations": full_suite,
            "duplicate_invocations": sum(
                count - 1 for count in test_commands.values() if count > 1),
            "duplicates": duplicate_tests[:top],
            "distinct_commands": len(test_commands),
        },
        "eu4_launches": {
            "count": launch_tool_calls + bash_launches,
            "launch_tool_calls": launch_tool_calls,
            "bash_launches": bash_launches,
            "command_evidence": launches[:top],
            "count_semantics": (
                "command invocations detected in the session; a command can "
                "fail before the game process starts"),
            "process_starts_estimate": (launch_tool_calls + bash_launches) * 2,
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
            "operations": goal_operations,
            "last_operation": last_goal["operation"] if last_goal else None,
            "last_phase": last_goal["phase"] if last_goal else None,
            "active": bool(last_goal and last_goal["phase"] == "active"),
            "terminal": bool(last_goal and last_goal["phase"] in (
                "complete", "completed", "blocked", "cancelled")),
            "turn_end_reasons": turn_end_reasons,
            "last_turn_reason": (turn_end_reasons[-1]
                                 if turn_end_reasons else None),
            "outcome": (
                "active" if last_goal and last_goal["phase"] == "active"
                else turn_end_reasons[-1] if turn_end_reasons
                else last_goal["phase"] if last_goal else "unknown"),
        },
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
    analyze_parser.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        events = read_events(args.path)
        value = analyze(
            filter_turn(events, args.turn),
            oversized_chars=args.oversized_chars,
            long_job_seconds=args.long_job_seconds, top=args.top)
        value["turn"] = args.turn
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
