"""Deterministic next-blocker selection from an existing Metal-only run.

This reads the same evidence as :mod:`eu4_metal_triage`; it does not launch
EU4, trace, or modify the run directory. A different terminal is not treated
as progress: the result says whether the evidence supports an actionable
reconstruction, a bounded original capture, a later milestone, or nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_metal_boot as boot  # noqa: E402
import eu4_metal_triage as triage  # noqa: E402

EVIDENCE_FILES = (
    "metadata.json",
    "init.json",
    "stop.json",
    "blocked_at.json",
    "create.json",
    "draw_invocation.json",
    "draw_executed.json",
    "checkpoints.jsonl",
)


def _evidence(root: Path) -> dict[str, str]:
    return {
        name: str(root / name)
        for name in EVIDENCE_FILES
        if (root / name).is_file()
    }


def _load(root: Path, name: str) -> dict | None:
    path = root / name
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _checkpoints(root: Path) -> list[dict]:
    path = root / "checkpoints.jsonl"
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeError):
        return []
    for line in lines[:256]:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _owner(rva: int | None) -> dict | None:
    """Name the replaced body containing the call byte, never a guessed symbol."""
    if rva is None:
        return None
    call = rva - 1
    for name, entry, end, _fn, _prefix in boot.GFX_ENTRIES:
        if entry <= call < end:
            return {
                "name": name,
                "entry_rva": f"0x{entry:x}",
                "end_rva": f"0x{end:x}",
            }
    return None


def _attribution(diag: dict) -> dict:
    return {
        "process": diag.get("process"),
        "pinned_image": diag.get("pinned_image"),
        "pinned_sha256": diag.get("pinned_sha256"),
        "threads_agree": diag.get("threads_agree"),
        "inside_replaced_body": diag.get("inside_replaced_body"),
        "init_success": diag.get("init_success"),
        "d1": diag.get("d1"),
    }


def _result(root: Path, diag: dict, status: str, kind: str, **fields) -> dict:
    result = {
        "schema": "gfx-next-blocker/v0",
        "status": status,
        "kind": kind,
        "run": str(root),
        "evidence": _evidence(root),
        "attribution": _attribution(diag),
    }
    result.update(fields)
    checkpoints = _checkpoints(root)
    if checkpoints:
        result["checkpoints"] = checkpoints[-16:]
    return result


def _provenance_ok(diag: dict) -> bool:
    return (
        type(diag.get("process")) is int
        and diag["process"] > 0
        and diag.get("pinned_image") is True
        and diag.get("pinned_sha256") is True
    )


def classify(run_dir: Path | str) -> dict:
    root = Path(run_dir).expanduser().resolve()
    if not root.is_dir():
        return {
            "schema": "gfx-next-blocker/v0",
            "status": "unavailable",
            "kind": "missing_run",
            "reason": "run directory is absent",
            "run": str(root),
            "evidence": {},
        }
    try:
        diag = triage.diagnose(root)
    except Exception as exc:  # noqa: BLE001 - a diagnostic must not become a guess
        return {
            "schema": "gfx-next-blocker/v0",
            "status": "unavailable",
            "kind": "diagnosis_failed",
            "reason": str(exc),
            "run": str(root),
            "evidence": _evidence(root),
        }

    kind = diag.get("kind")
    symbol = diag.get("stop_symbol")
    caller_rva = diag.get("return_rva")
    provenance = _provenance_ok(diag)

    if kind == "missing":
        return _result(root, diag, "unavailable", "missing_evidence",
                       reason="metadata/init/stop are absent", next_step="stop")
    if kind in ("incomplete", "error"):
        return _result(root, diag, "unavailable", "incomplete_run",
                       reason="diagnostic identity is incomplete", next_step="stop")
    if kind in ("timeout", "crash"):
        return _result(
            root,
            diag,
            "needs_evidence",
            "attribution_ambiguous",
            symbol=symbol,
            caller_rva=None if caller_rva is None else f"0x{caller_rva:x}",
            reason=f"{kind} is not attributed to a renderer operation",
            next_step="ghidra_research",
        )
    if kind in ("blocked_at", "gl_diagnostic", "unsupported"):
        if not provenance:
            return _result(
                root,
                diag,
                "unavailable",
                "provenance_unavailable",
                symbol=symbol,
                caller_rva=None if caller_rva is None else f"0x{caller_rva:x}",
                reason="pid/image/sha provenance is not established",
                next_step="stop",
            )
        if not symbol or caller_rva is None:
            return _result(
                root,
                diag,
                "needs_evidence",
                "attribution_ambiguous",
                reason="stop has no symbol or caller RVA",
                next_step="ghidra_research",
            )
        owner = _owner(caller_rva)
        if owner is None:
            return _result(
                root,
                diag,
                "needs_evidence",
                "caller_outside_replaced_body",
                symbol=symbol,
                caller_rva=f"0x{caller_rva:x}",
                reason="stop caller is outside every current replaced body",
                next_step="ghidra_research",
            )
        action_kind = (
            "missing_metal_operation"
            if symbol.startswith("_Gfx")
            else "unsupported_gl_dependency"
        )
        return _result(
            root,
            diag,
            "actionable",
            action_kind,
            symbol=symbol,
            caller_rva=f"0x{caller_rva:x}",
            owner=owner,
            next_step="reconstruct",
        )
    executed = _load(root, "draw_executed.json")
    if isinstance(executed, dict) and executed.get("draw_executed") is True:
        return _result(
            root,
            diag,
            "advanced",
            "draw_executed",
            symbol=executed.get("symbol", "_GfxDraw"),
            caller_rva=executed.get("caller_rva"),
            next_step="continue",
        )
    if kind == "draw_reached":
        draw = _load(root, "draw_invocation.json") or {}
        count = draw.get("effective_count")
        if type(count) is int and count <= 2:
            return _result(
                root,
                diag,
                "needs_evidence",
                "draw_nonvisible",
                symbol="_GfxDraw",
                caller_rva=draw.get("caller_rva"),
                reason="the selected draw has no triangle-strip primitives",
                next_step="investigate",
            )
        return _result(
            root,
            diag,
            "needs_evidence",
            "draw_packet_missing",
            symbol="_GfxDraw",
            caller_rva=draw.get("caller_rva"),
            reason="the draw was reached but its packet is not captured",
            next_step="capture_original",
        )
    if kind == "clean_exit":
        return _result(
            root,
            diag,
            "needs_evidence",
            "no_rendering_milestone",
            reason="the process exited without a qualified draw milestone",
            next_step="investigate",
        )
    return _result(
        root,
        diag,
        "unavailable",
        "unclassified",
        reason=f"triage kind {kind!r} has no safe action",
        next_step="stop",
    )
