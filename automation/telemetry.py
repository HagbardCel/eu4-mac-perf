"""Append-only JSONL. Measurements are stored only when the caller has them."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SPILL_BYTES = 4096
_SECRET = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|KEY))\s*[:=]\s*\S+"
)
# ``tokens`` is a usage count, not a credential. Match token only as its own component.
_SENSITIVE_KEY = re.compile(
    r"(?i)(^|_)(password|secret|token|api_key|authorization)($|_)"
)


def append(path: Path, records: list[dict], secrets: list[str] | None = None) -> None:
    seen = _existing_ids(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    blobs = path.parent / "blobs"
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            event_id = record["event_id"]
            if event_id in seen:
                continue
            cleaned = _omit_empty(redact(record, secrets or []))
            cleaned = _spill(cleaned, blobs)
            handle.write(json.dumps(cleaned, sort_keys=True) + "\n")
            seen.add(event_id)


def report(path: Path) -> dict:
    counts = {
        "tool_calls": 0,
        "tool_failures": 0,
        "validations": 0,
        "validation_passes": 0,
        "files": [],
        "tokens": {},
        "cost": None,
        "elapsed_seconds": None,
        "rounds": None,
    }
    files: list[str] = []
    calls: set[str] = set()
    if not path.exists():
        counts["files"] = files
        return counts
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        kind = event.get("type")
        if kind in ("tool.started", "tool.completed"):
            calls.add(_tool_identity(event))
        if kind == "tool.completed" and event.get("status") == "error":
            counts["tool_failures"] += 1
        if kind == "validation":
            counts["validations"] += 1
            if event.get("verdict") == "pass":
                counts["validation_passes"] += 1
            if event.get("rounds") is not None:
                counts["rounds"] = event["rounds"]
        if event.get("path") and event["path"] not in files:
            files.append(event["path"])
        if isinstance(event.get("tokens"), dict):
            for key, value in event["tokens"].items():
                if isinstance(value, (int, float)):
                    counts["tokens"][key] = counts["tokens"].get(key, 0) + value
        if isinstance(event.get("cost"), (int, float)):
            counts["cost"] = (counts["cost"] or 0) + event["cost"]
        if isinstance(event.get("elapsed_seconds"), (int, float)):
            counts["elapsed_seconds"] = event["elapsed_seconds"]
    counts["tool_calls"] = len(calls)
    counts["files"] = files
    if not counts["tokens"]:
        counts["tokens"] = None
    return counts


def _existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    found = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        found.add(json.loads(line)["event_id"])
    return found


def _tool_identity(event: dict) -> str:
    event_id = str(event.get("event_id") or "")
    for suffix in ("/tool.started", "/tool.completed"):
        if event_id.endswith(suffix):
            event_id = event_id[: -len(suffix)]
            break
    return f"{event.get('session_id') or ''}/{event_id}"


def redact(value, secrets: list[str]):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if isinstance(key, str) and _SENSITIVE_KEY.search(key):
                cleaned[key] = "[redacted]"
            else:
                cleaned[key] = redact(item, secrets)
        return cleaned
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[redacted]")
    return _SECRET.sub(r"\1=[redacted]", value)


def _omit_empty(record: dict) -> dict:
    return {key: value for key, value in record.items() if value is not None}


def _spill(record: dict, blobs: Path) -> dict:
    text = record.get("text")
    if not isinstance(text, str) or len(text.encode("utf-8")) <= SPILL_BYTES:
        return record
    blobs.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    target = blobs / f"{digest}.txt"
    if not target.exists():
        target.write_text(text, encoding="utf-8")
    spilled = dict(record)
    spilled.pop("text", None)
    spilled["blob"] = {"path": str(target), "size": len(text.encode("utf-8")), "sha256": digest}
    return spilled
