"""Independent-review plumbing: process invocation and output extraction.

The reviewer is a separate DSH process, not a role switch and not a subagent of
the implementer. It has no goal, no completion authority, and no write access to
protected state; the host reads its output, validates it and writes the receipt
itself, so the reviewer never supplies its own identity.

The extraction is deliberately tolerant of the event stream's shape: the
headless app's ``--json`` output is newline-delimited and its exact event schema
is not part of this contract. What matters is that a single ``eu4-review/v1``
object is recovered, and that the session identity comes from the stream rather
than from the reviewer's own claim.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

REVIEW_SCHEMA = "eu4-review/v1"
SESSION_KEYS = ("sessionId", "session_id", "sessionID")


class ReviewError(RuntimeError):
    """The reviewer could not produce a reviewable result."""


def build_task(candidate_commit: str, oracle_digest: str | None, contract: str) -> str:
    oracle = oracle_digest or "none"
    return (
        "You are an independent reviewer for an EU4 OpenGL-to-Metal migration "
        "candidate. You did not write this candidate and you must not modify it.\n\n"
        f"Candidate commit: {candidate_commit}\n"
        f"Oracle manifest digest: {oracle}\n\n"
        f"{contract.strip()}\n\n"
        "Reply with exactly one JSON object and nothing else. It must match "
        f'{REVIEW_SCHEMA}: '
        '{"schema":"eu4-review/v1","candidate_commit":"<40 hex>",'
        '"oracle_manifest_sha256":"<64 hex>","outcome":'
        '"qualified|rejected|unresolved","findings":[]} . '
        "Every finding needs id, severity (P0-P3), kind, and for a behavioural "
        "counterexample a reproducer and expected_provenance. Report what the "
        "candidate actually establishes; do not accept its claims."
    )


def _strings(value, sink: list[str]) -> None:
    if isinstance(value, str):
        sink.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _strings(item, sink)
    elif isinstance(value, list):
        for item in value:
            _strings(item, sink)


def _json_strings(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            _strings(json.loads(line), found)
        except json.JSONDecodeError:
            found.append(line)
    return found


def _balanced_objects(text: str) -> list[str]:
    """Every balanced {...} slice, innermost-last, tolerant of nesting."""
    objects: list[str] = []
    stack: list[int] = []
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            stack.append(index)
        elif character == "}" and stack:
            start = stack.pop()
            objects.append(text[start:index + 1])
    return objects


def final_text(text: str) -> str | None:
    """The ``final`` event's text, which is where the answer is stated.

    Verified against the installed dsh-headless implementation: the stream
    writes ``{"type":"session","sessionId":...}`` first, then one
    ``{"type":"text","text":...}`` per committed assistant text block, and
    ``{"type":"final","text":...}`` when the run finishes.
    """
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "final":
            found = event.get("text")
            return found if isinstance(found, str) else None
    return None


def extract_review_payload(text: str) -> dict | None:
    """Recover the eu4-review/v1 object from the run's output.

    The ``final`` event is authoritative when present, so a review object merely
    quoted in a thinking block or a tool result cannot be picked up ahead of the
    answer. Falling back to the whole stream keeps the extractor usable for
    other transports and for truncated runs.
    """
    authoritative = final_text(text)
    if authoritative is not None:
        for candidate in reversed(_balanced_objects(authoritative)):
            if REVIEW_SCHEMA not in candidate:
                continue
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("schema") == REVIEW_SCHEMA:
                return value
    haystack = "\n".join(_json_strings(text))
    for candidate in reversed(_balanced_objects(haystack)):
        if REVIEW_SCHEMA not in candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("schema") == REVIEW_SCHEMA:
            return value
    return None


def session_id_from(text: str) -> str | None:
    """The host-observed session identity, never the reviewer's own claim."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            for key in SESSION_KEYS:
                match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', line)
                if match:
                    return match.group(1)
            continue
        stack = [value]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for key in SESSION_KEYS:
                    if isinstance(current.get(key), str) and current[key]:
                        return current[key]
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
    return None


def run_reviewer(
    command: list[str], *, cwd: Path, timeout: float, env: dict | None = None
) -> dict:
    """Run the reviewer process and capture its raw output."""
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        raise ReviewError(f"reviewer command not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ReviewError(f"reviewer timed out after {timeout:g}s") from exc
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr[-4000:],
    }


def prepare_worktree(repo: Path, path: Path, commit: str) -> None:
    """A detached worktree at exactly the frozen candidate commit."""
    path = Path(path)
    if path.exists():
        raise ReviewError(f"reviewer worktree already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(path), commit],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
        raise ReviewError(
            completed.stderr.strip() or "reviewer worktree creation failed"
        )


def reviewer_command(reviewer_cmd: str | None, task: str) -> list[str]:
    """The default is the shipped one-shot profile, reached over the CLI."""
    if reviewer_cmd:
        return [reviewer_cmd, task] if reviewer_cmd.endswith(("sh", "bash")) else [
            *reviewer_cmd.split(), task
        ]
    return ["dsh", "--profile", "headless", "--json", task]
