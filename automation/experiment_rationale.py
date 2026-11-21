"""A concise rationale for agent-initiated expensive replays.

The harness verifies only structure and that referenced evidence paths exist.
Scientific merit stays the agent's judgment; a weak rationale is visible in the
output directory instead of being scored by another approval system.  There is
no cache, identity, or reuse lifecycle here: whether to repeat an experiment is
the agent's decision.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

SCHEMA = "eu4-experiment-rationale/v1"
RECORD_NAME = "experiment-rationale.json"
REQUIRED_FIELDS = (
    "question",
    "evidence",
    "gap",
    "hypothesis",
    "action",
    "expected_information",
    "cheaper_alternatives_checked",
)


class RationaleError(ValueError):
    """A rationale is missing, unreadable or structurally incomplete."""


def load(value: str | None) -> dict:
    """Parse a rationale supplied as a JSON file path or inline JSON."""
    if not value or not str(value).strip():
        raise RationaleError(
            "an expensive replay requires --rationale <json file or inline "
            "JSON> with question, evidence, gap, hypothesis, action, "
            "expected_information and cheaper_alternatives_checked")
    text = str(value).strip()
    path = Path(text).expanduser()
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RationaleError(f"unreadable rationale: {exc}") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RationaleError(f"rationale is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RationaleError("rationale must be a JSON object")
    return value


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def validate(rationale) -> list[str]:
    problems: list[str] = []
    if not isinstance(rationale, dict):
        return ["the rationale must be an object"]
    for field in REQUIRED_FIELDS:
        value = rationale.get(field)
        if field == "cheaper_alternatives_checked":
            if value is not True:
                problems.append(
                    "cheaper_alternatives_checked must be true after existing "
                    "logs, static analysis and narrower tests were consulted")
        elif field == "evidence":
            if not isinstance(value, list) or not value:
                problems.append("evidence must be a non-empty list of records")
        elif not _text(value):
            problems.append(f"{field} must be a non-empty string")
    return problems


def _resolves(reference: str, run_root: Path) -> bool:
    text = reference.strip()
    if not text:
        return False
    if text.startswith("replay:"):
        candidate = Path(text[len("replay:"):])
        return any(
            (path / "replay.json").is_file()
            or (path / "producer-receipt.json").is_file()
            for path in (candidate, run_root / candidate,
                         Path.cwd() / candidate))
    candidate = Path(text)
    return any(path.exists() for path in
               (candidate, run_root / candidate, Path.cwd() / candidate))


def evidence_problems(rationale: dict, *, run_root: Path | str) -> list[str]:
    root = Path(run_root).expanduser().resolve()
    problems = []
    for reference in rationale.get("evidence") or []:
        if not isinstance(reference, str) or not reference.strip():
            problems.append("an evidence reference is not a non-empty string")
        elif not _resolves(reference, root):
            problems.append(
                f"evidence {reference!r} does not resolve to an existing record")
    return problems


def require(value: str | None, *, run_root: Path | str) -> dict:
    rationale = load(value)
    problems = validate(rationale) or evidence_problems(
        rationale, run_root=run_root)
    if problems:
        raise RationaleError("; ".join(problems))
    return rationale


def record(out_dir: Path | str, rationale: dict, *,
           result: dict | None = None) -> dict:
    """Write the rationale beside the result it explains, in the same directory."""
    path = Path(out_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    value = {
        "schema": SCHEMA,
        "recorded_at": time.time(),
        "rationale": rationale,
        "result": {"status": (result or {}).get("status")},
        # A rationale is an audit note, never rendering evidence.
        "authoritative": False,
    }
    (path / RECORD_NAME).write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return value
