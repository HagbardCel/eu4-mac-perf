"""Deterministic next-blocker selection from an existing Metal-only run.

This reads the same evidence as :mod:`eu4_metal_triage`; it does not launch
EU4, trace, or modify the run directory. A different terminal is not treated
as progress: the result says whether the evidence supports an actionable
reconstruction, a bounded original capture, a later milestone, or nothing.
"""

from __future__ import annotations

import hashlib
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


DRAW_SYMBOLS = {"_GfxDraw", "_GfxDrawIndexed", "_GfxDrawIndexedInstanced"}
_PIXEL_GATES = (
    "draw_reached",
    "packet_ready",
    "draw_executed",
    "metal_completed",
    "engine_path_complete",
    "non_clear",
)


def _result(
    root: Path,
    diag: dict,
    status: str,
    kind: str,
    *,
    session: dict | None = None,
    **fields,
) -> dict:
    result = {
        "schema": "gfx-next-blocker/v0",
        "status": status,
        "kind": kind,
        "run": str(root),
        "evidence": _evidence(root),
        "attribution": _attribution(diag),
    }
    if session is not None:
        result["attribution"].update(session)
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


def _session_attribution(
    meta: dict, caller_rva: int | None, owner: dict | None
) -> dict:
    ranges = triage.session_ranges(meta)
    result: dict = {
        "session_ranges_known": ranges is not None,
        "installed_in_session": None,
    }
    if ranges is None or caller_rva is None:
        return result
    call = caller_rva - 1
    containing = next(
        ((start, end) for start, end in ranges if start <= call < end), None
    )
    if containing is None:
        result["installed_in_session"] = False
        return result
    result["installed_in_session"] = True
    result["session_range"] = {
        "entry_rva": f"0x{containing[0]:x}",
        "end_rva": f"0x{containing[1]:x}",
    }
    if owner is not None:
        owner_range = (int(owner["entry_rva"], 16), int(owner["end_rva"], 16))
        result["matches_current_owner"] = owner_range == containing
    else:
        result["matches_current_owner"] = False
    return result


def _terminal_blocker(
    root: Path,
    diag: dict,
    meta: dict,
    symbol: str | None,
    caller_rva: int | None,
) -> dict:
    if not _provenance_ok(diag):
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
    session = _session_attribution(meta, caller_rva, owner)
    if not session["session_ranges_known"]:
        return _result(
            root,
            diag,
            "needs_evidence",
            "replacement_inventory_unknown",
            symbol=symbol,
            caller_rva=f"0x{caller_rva:x}",
            owner=owner,
            session=session,
            reason="the run's installed replacement intervals are unknown",
            next_step="ghidra_research",
        )
    if session.get("installed_in_session") and not session.get(
        "matches_current_owner", False
    ):
        return _result(
            root,
            diag,
            "needs_evidence",
            "stale_replacement_inventory",
            symbol=symbol,
            caller_rva=f"0x{caller_rva:x}",
            owner=owner,
            session=session,
            reason="the session interval does not match the current containing body",
            next_step="ghidra_research",
        )
    if owner is None:
        return _result(
            root,
            diag,
            "needs_evidence",
            "caller_outside_replaced_body",
            symbol=symbol,
            caller_rva=f"0x{caller_rva:x}",
            session=session,
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
        session=session,
        next_step="reconstruct",
    )


def _draw_symbol(diag: dict, draw: dict) -> str | None:
    symbol = draw.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        symbol = diag.get("stop_symbol")
    return symbol if symbol in DRAW_SYMBOLS else None


def _valid_draw_receipt(
    root: Path, diag: dict, meta: dict, draw: dict
) -> tuple[dict | None, dict]:
    receipt = _load(root, "draw_executed.json")
    if not isinstance(receipt, dict):
        return None, {"reason": "draw_executed.json is absent or malformed"}
    errors: list[str] = []
    pid = receipt.get("pid")
    if type(pid) is not int or pid <= 0 or pid != diag.get("process"):
        errors.append("pid")
    image = receipt.get("image")
    if not boot.same_pinned_executable(image):
        errors.append("image")
    pinned = (meta.get("gog") or {}).get("executable_sha256")
    allowed_sha = {x for x in (boot.PINNED_SHA256, pinned) if isinstance(x, str)}
    if receipt.get("executable_sha256") not in allowed_sha:
        errors.append("executable_sha256")
    if receipt.get("provenance") != "game":
        errors.append("provenance")
    symbol = receipt.get("symbol")
    if symbol not in DRAW_SYMBOLS:
        errors.append("symbol")
    if not draw or draw.get("symbol") != symbol:
        errors.append("symbol_invocation")
    if boot.parse_rva(receipt.get("caller_rva")) != boot.parse_rva(
        draw.get("caller_rva")
    ):
        errors.append("caller_rva")
    if receipt.get("thread") != draw.get("thread"):
        errors.append("thread")
    if (
        receipt.get("invocation_id") is not None
        and draw.get("invocation_id") is not None
        and receipt.get("invocation_id") != draw.get("invocation_id")
    ):
        errors.append("invocation_id")
    count = receipt.get("effective_count")
    if type(count) is not int or count < 3:
        errors.append("effective_count")
    if draw.get("effective_count") is not None and draw.get(
        "effective_count"
    ) != count:
        errors.append("effective_count_invocation")
    if receipt.get("mode") != 5:
        errors.append("mode")
    if receipt.get("shader_non_null") is not True:
        errors.append("shader")
    for key in _PIXEL_GATES:
        if receipt.get(key) is not True:
            errors.append(key)
    size = receipt.get("pixels_size")
    digest = receipt.get("pixels_sha256")
    if type(size) is not int or size <= 0:
        errors.append("pixels_size")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(ch not in "0123456789abcdef" for ch in digest.lower())
    ):
        errors.append("pixels_sha256")
    pixel_file = receipt.get("pixels_file")
    if isinstance(pixel_file, str) and pixel_file:
        relative = Path(pixel_file)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not pixel_file.startswith("pixels/")
        ):
            errors.append("pixels_file_path")
        else:
            path = (root / relative).resolve()
            if not str(path).startswith(str(root) + "/") or not path.is_file():
                errors.append("pixels_file_missing")
            else:
                data = path.read_bytes()
                if type(size) is int and len(data) != size:
                    errors.append("pixels_file_size")
                if isinstance(digest, str) and hashlib.sha256(data).hexdigest() != digest.lower():
                    errors.append("pixels_file_sha256")
    if errors:
        return None, {"reason": "receipt validation failed", "fields": errors}
    return receipt, {}


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

    meta = _load(root, "metadata.json") or {}
    draw = _load(root, "draw_invocation.json") or {}
    kind = diag.get("kind")
    symbol = diag.get("stop_symbol")
    caller_rva = diag.get("return_rva")
    receipt, receipt_error = _valid_draw_receipt(root, diag, meta, draw)
    receipt_exists = (root / "draw_executed.json").is_file()

    if receipt is not None:
        next_blocker = None
        if kind in ("blocked_at", "gl_diagnostic", "unsupported"):
            next_blocker = _terminal_blocker(
                root, diag, meta, symbol, caller_rva
            )
        return _result(
            root,
            diag,
            "advanced",
            "draw_executed",
            symbol=receipt["symbol"],
            caller_rva=receipt.get("caller_rva"),
            milestone="draw_executed",
            draw={
                "symbol": draw.get("symbol"),
                "invocation_id": draw.get("invocation_id"),
                "caller_rva": draw.get("caller_rva"),
                "pid": draw.get("pid"),
                "thread": draw.get("thread"),
                "effective_count": draw.get("effective_count"),
            },
            draw_receipt={
                "effective_count": receipt.get("effective_count"),
                "mode": receipt.get("mode"),
                "non_clear": receipt.get("non_clear"),
                "pixels_size": receipt.get("pixels_size"),
                "pixels_sha256": receipt.get("pixels_sha256"),
            },
            next_blocker=next_blocker,
            next_step="continue",
        )

    if kind == "missing":
        return _result(root, diag, "unavailable", "missing_evidence",
                       reason="metadata/init/stop are absent", next_step="stop")
    if kind == "incomplete":
        if symbol and caller_rva is not None and _provenance_ok(diag):
            blocker = _terminal_blocker(root, diag, meta, symbol, caller_rva)
            if receipt_exists:
                blocker["draw_receipt_error"] = receipt_error
            return blocker
        return _result(root, diag, "unavailable", "incomplete_run",
                       reason="diagnostic identity is incomplete", next_step="stop")
    if kind == "error":
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
            draw_receipt_error=receipt_error if receipt_exists else None,
        )
    if kind in ("blocked_at", "gl_diagnostic", "unsupported"):
        blocker = _terminal_blocker(root, diag, meta, symbol, caller_rva)
        if receipt_exists:
            blocker["draw_receipt_error"] = receipt_error
        return blocker
    if kind == "draw_reached":
        draw_symbol = _draw_symbol(diag, draw)
        if draw_symbol is None:
            return _result(
                root,
                diag,
                "needs_evidence",
                "draw_symbol_unknown",
                reason="the draw symbol is not one of the recorded draw entries",
                draw_receipt_error=receipt_error if receipt_exists else None,
                next_step="ghidra_research",
            )
        if receipt_exists:
            return _result(
                root,
                diag,
                "needs_evidence",
                "invalid_draw_receipt",
                symbol=draw_symbol,
                caller_rva=draw.get("caller_rva"),
                draw_receipt_error=receipt_error,
                reason="a draw receipt is present but does not validate",
                next_step="investigate",
            )
        count = draw.get("effective_count")
        if type(count) is int and count <= 2:
            return _result(
                root,
                diag,
                "needs_evidence",
                "draw_nonvisible",
                symbol=draw_symbol,
                caller_rva=draw.get("caller_rva"),
                reason="the selected draw has no triangle-strip primitives",
                next_step="investigate",
            )
        return _result(
            root,
            diag,
            "needs_evidence",
            "draw_packet_missing",
            symbol=draw_symbol,
            caller_rva=draw.get("caller_rva"),
            reason="the draw was reached but its packet is not captured",
            next_step="capture_original",
        )
    if kind == "clean_exit":
        if receipt_exists:
            return _result(
                root,
                diag,
                "needs_evidence",
                "invalid_draw_receipt",
                draw_receipt_error=receipt_error,
                reason="a draw receipt is present but does not validate",
                next_step="investigate",
            )
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
