"""One authoritative workflow-level ledger for original-EU4 launches.

The ledger is an authorization and accounting control, not a sandbox: an
unrestricted local shell can still launch the game outside it. Every receipt
counts from ``reserved`` onward. There is no automatic refund; a reservation
whose process may have started is reconciled to ``abandoned``/``failed`` and
remains charged. The per-campaign ``live-launch.json`` is a compatibility
mirror, never a second authority.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from pathlib import Path

LEDGER_SCHEMA = "live-launch-ledger/v1"
POINTER_SCHEMA = "live-launch-ledger-pointer/v1"
STATUSES = frozenset({"reserved", "started", "completed", "failed", "abandoned"})


class LedgerError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def pointer_path(run_root: Path) -> Path:
    return Path(run_root) / "workflow-ledger.json"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def write_pointer(
    run_root: Path, *, workflow_id: str, ledger_path: Path, limit: int
) -> Path:
    path = pointer_path(run_root)
    _atomic(
        path,
        {
            "schema": POINTER_SCHEMA,
            "workflow_id": workflow_id,
            "ledger": str(Path(ledger_path).resolve()),
            "limit": int(limit),
        },
    )
    return path


def read_pointer(run_root: Path) -> dict | None:
    path = pointer_path(run_root)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LedgerError("malformed", f"ledger pointer unreadable: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != POINTER_SCHEMA:
        raise LedgerError("malformed", "unexpected ledger pointer schema")
    ledger = value.get("ledger")
    limit = value.get("limit")
    if not isinstance(ledger, str) or not ledger or type(limit) is not int:
        raise LedgerError("malformed", "ledger pointer lacks a path or limit")
    return value


def create_ledger(
    path: Path, *, workflow_id: str, limit: int, deadline_seconds: float | None
) -> Path:
    path = Path(path)
    if path.exists():
        return path
    _atomic(
        path,
        {
            "schema": LEDGER_SCHEMA,
            "workflow_id": workflow_id,
            "limit": int(limit),
            "deadline_seconds": deadline_seconds,
            "used": 0,
            "launches": [],
        },
    )
    return path


def load_ledger(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise LedgerError("missing", f"workflow ledger is absent: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LedgerError("malformed", str(exc)) from exc
    errors = validate_ledger(value)
    if errors:
        raise LedgerError("malformed", "; ".join(errors))
    return value


def validate_ledger(value: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["ledger is not an object"]
    if value.get("schema") != LEDGER_SCHEMA:
        errors.append(f"schema must be {LEDGER_SCHEMA}")
    if not isinstance(value.get("workflow_id"), str) or not value["workflow_id"]:
        errors.append("workflow_id is missing")
    limit = value.get("limit")
    used = value.get("used")
    receipts = value.get("launches")
    if type(limit) is not int or limit < 0:
        errors.append("limit is invalid")
    if type(used) is not int or used < 0:
        errors.append("used is invalid")
    if not isinstance(receipts, list):
        errors.append("launches is not a list")
        return errors
    if type(used) is int and used != len(receipts):
        errors.append("used does not equal the number of receipts")
    tokens: set[str] = set()
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, dict):
            errors.append(f"launches[{index}] is not an object")
            continue
        token = receipt.get("token")
        if not isinstance(token, str) or not token:
            errors.append(f"launches[{index}] has no token")
        elif token in tokens:
            errors.append(f"launches[{index}] duplicates a token")
        else:
            tokens.add(token)
        if receipt.get("status") not in STATUSES:
            errors.append(f"launches[{index}] has an invalid status")
        if not isinstance(receipt.get("campaign_id"), str) or not receipt["campaign_id"]:
            errors.append(f"launches[{index}] has no campaign id")
    if type(limit) is int and type(used) is int and used > limit:
        errors.append("used exceeds the limit")
    return errors


def _locked(path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.with_name(path.name + ".lock").open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def claim(
    path: Path,
    *,
    campaign_id: str,
    run_root: Path,
    output: Path,
    targets: list[str],
    requested_timeout: float,
) -> str:
    path = Path(path)
    handle = _locked(path)
    try:
        value = load_ledger(path)
        if value["used"] >= value["limit"]:
            raise LedgerError(
                "exhausted",
                f"{value['used']}/{value['limit']} workflow launches already used",
            )
        deadline = value.get("deadline_seconds")
        if deadline is not None and requested_timeout > float(deadline):
            raise LedgerError(
                "deadline",
                f"requested {requested_timeout:g}s exceeds workflow "
                f"limit {float(deadline):g}s",
            )
        token = f"{dt.datetime.now(dt.timezone.utc).timestamp()}-{os.getpid()}-{len(value['launches'])}"
        value["launches"].append(
            {
                "token": token,
                "campaign_id": campaign_id,
                "run_root": str(Path(run_root).resolve()),
                "output": str(Path(output).resolve()),
                "targets": list(targets),
                "requested_timeout": float(requested_timeout),
                "status": "reserved",
                "pid": None,
                "at": _now(),
                "updated_at": _now(),
            }
        )
        value["used"] = len(value["launches"])
        _atomic(path, value)
        return token
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _update(path: Path, token: str, **fields) -> bool:
    path = Path(path)
    if not path.is_file():
        return False
    handle = _locked(path)
    try:
        value = load_ledger(path)
        for receipt in value["launches"]:
            if receipt.get("token") == token:
                receipt.update(fields)
                receipt["updated_at"] = _now()
                _atomic(path, value)
                return True
        return False
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def start(path: Path, token: str, pid: int | None) -> bool:
    return _update(path, token, status="started", pid=pid)


def finish(path: Path, token: str, *, pid: int | None, status: str) -> bool:
    if status not in STATUSES:
        raise LedgerError("malformed", f"invalid terminal status: {status!r}")
    return _update(path, token, status=status, pid=pid)


def reconcile(path: Path) -> int:
    """Mark stale reservations abandoned; they remain charged."""
    path = Path(path)
    if not path.is_file():
        return 0
    handle = _locked(path)
    try:
        value = load_ledger(path)
        changed = 0
        for receipt in value["launches"]:
            if receipt.get("status") == "reserved" and receipt.get("pid") is None:
                receipt["status"] = "abandoned"
                receipt["updated_at"] = _now()
                changed += 1
        if changed:
            _atomic(path, value)
        return changed
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def campaign_receipts(
    value: dict, campaign_id: str, *, status: str | None = None
) -> list[dict]:
    rows = [
        receipt
        for receipt in value.get("launches", [])
        if isinstance(receipt, dict) and receipt.get("campaign_id") == campaign_id
    ]
    if status is not None:
        rows = [receipt for receipt in rows if receipt.get("status") == status]
    return rows
