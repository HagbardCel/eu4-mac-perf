"""Shared types and helpers for versioned draw-evidence contracts.

A contract validator is a callable ``validator(value, ctx) -> str | None``.
``None`` means the value is accepted. A non-empty string is a human-readable
error. The checker calls validators only after the generic evidence checks have
passed; a validator must not trust candidate-authored sidecars without checking
them against the original capture.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ContractError(RuntimeError):
    """A trusted contract module is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class ContractContext:
    """Everything a validator may inspect about the selected draw.

    ``evidence_paths`` maps a packet evidence path to the resolved file under
    the packet directory or the original capture directory. ``manifest_members``
    maps repo-relative paths to their pinned SHA-256 values.
    """

    artifact: Any
    target: Any
    master: dict
    packet_dir: Path
    capture_dir: Path
    config: dict
    repo: Path
    target_metadata: dict
    evidence_paths: dict[str, Path] = field(default_factory=dict)
    frozen_evidence: dict[str, Any] = field(default_factory=dict)
    manifest_members: dict[str, str] = field(default_factory=dict)
    field_name: str | None = None
    field_evidence: list[dict] = field(default_factory=list)
    field_evidence_paths: dict[str, Path] = field(default_factory=dict)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_packet_payload(packet_dir: Path, text: str) -> Path | None:
    """Resolve a relative packet payload, refusing escapes and missing files."""
    candidate = Path(str(text or ""))
    if not str(candidate) or candidate.is_absolute() or ".." in candidate.parts:
        return None
    base = Path(packet_dir).resolve()
    path = (base / candidate).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path if path.is_file() else None


def require(condition: bool, message: str) -> str | None:
    return None if condition else message


def record_for(ctx: ContractContext, call_id: Any):
    if type(call_id) is not int:
        return None
    return next(
        (row for row in ctx.artifact.operations if row.call_id == call_id),
        None,
    )


def require_record(
    ctx: ContractContext,
    call_id: Any,
    *,
    symbol: str | None = None,
    resource_id: str | None = None,
) -> tuple[Any, str | None]:
    record = record_for(ctx, call_id)
    if record is None:
        return None, f"no original operation with call id {call_id!r}"
    if symbol is not None and record.symbol != symbol:
        return None, f"call {call_id} is {record.symbol!r}, not {symbol!r}"
    if resource_id is not None and record.resource_id != resource_id:
        return None, "resource id differs from the original operation"
    if not record.fields_ok:
        return None, "original operation is not structurally valid"
    return record, None


def require_precedes(
    record: Any, ctx: ContractContext, *, label: str = "evidence"
) -> str | None:
    if record is None:
        return f"{label} record is missing"
    if record.call_id >= ctx.target.call_id:
        return f"{label} call does not precede the selected draw"
    return None


def _rva_value(text: Any) -> int | None:
    if type(text) is int:
        return text if text > 0 else None
    if isinstance(text, str):
        try:
            value = int(text, 0)
        except ValueError:
            return None
        return value if value > 0 else None
    return None


def _pinned_executable(ctx: ContractContext) -> tuple[Path | None, str | None]:
    metadata = ctx.target_metadata or {}
    path_text = metadata.get("default_executable")
    expected = metadata.get("sha256")
    if not path_text or not isinstance(expected, str):
        return None, "target metadata lacks a pinned executable identity"
    path = Path(str(path_text))
    if not path.is_file():
        return None, "pinned executable is not available for static verification"
    if sha256_file(path) != expected:
        return None, "pinned executable hash does not match target metadata"
    return path, None


def verify_static_span(span: dict, ctx: ContractContext) -> str | None:
    """Verify an instruction span against the pinned executable.

    A candidate-authored span plus its own hash is not evidence. The executable
    identity, RVA and bytes are all checked. When a frozen span file is named,
    its entry must agree as well.
    """
    if not isinstance(span, dict):
        return "static evidence is not an object"
    executable_sha = span.get("executable_sha256")
    expected_sha = (ctx.target_metadata or {}).get("sha256")
    if not isinstance(executable_sha, str) or executable_sha != expected_sha:
        return "static evidence executable identity differs from the pinned target"
    rva = _rva_value(span.get("rva", span.get("entry_rva")))
    if rva is None:
        return "static evidence has no valid RVA"
    hex_text = span.get("instruction_span")
    if not isinstance(hex_text, str):
        return "static evidence has no instruction span"
    try:
        raw = bytes.fromhex(hex_text)
    except ValueError:
        return "static evidence span is not hex"
    if not raw:
        return "static evidence span is empty"
    digest = span.get("span_sha256")
    if not isinstance(digest, str) or sha256_bytes(raw) != digest:
        return "static evidence span hash does not match its bytes"

    path, error = _pinned_executable(ctx)
    if error:
        return error
    try:
        from automation.macho_map import read_rva

        actual = read_rva(path, rva, len(raw))
    except (OSError, ValueError) as exc:
        return f"static evidence cannot be read from the pinned executable: {exc}"
    if actual != raw:
        return "static evidence span differs from the pinned executable"

    frozen_path = span.get("frozen_path")
    if frozen_path:
        candidate = Path(str(frozen_path))
        if candidate.is_absolute() or ".." in candidate.parts:
            return "static evidence frozen path is invalid"
        resolved = (ctx.repo / candidate).resolve()
        try:
            resolved.relative_to(ctx.repo.resolve())
        except ValueError:
            return "static evidence frozen path escapes the repository"
        if not resolved.is_file():
            return "static evidence frozen span file is missing"
        expected = ctx.manifest_members.get(candidate.as_posix())
        if expected is not None and sha256_file(resolved) != expected:
            return "static evidence frozen span file differs from the manifest"
        try:
            payload = read_json(resolved)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return f"static evidence frozen span file is unreadable: {exc}"
        entries = payload.get("spans") if isinstance(payload, dict) else None
        match = None
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if _rva_value(entry.get("rva")) == rva:
                    match = entry
                    break
        if match is None:
            return "static evidence RVA is not in the frozen span file"
        if match.get("span_sha256") != digest:
            return "static evidence frozen span digest differs"
    return None
