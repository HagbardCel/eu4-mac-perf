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
import math
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


def validate_timeout(value, *, field: str = "timeout") -> float:
    """Return a finite positive timeout, or raise LedgerError.

    Booleans and non-numeric values are rejected at the ledger boundary so a
    direct Python caller cannot bypass the CLI checks with NaN or infinity.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LedgerError("timeout", f"{field} must be a finite positive number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise LedgerError("timeout", f"{field} must be a finite positive number")
    return number


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
    path: Path,
    *,
    workflow_id: str,
    limit: int,
    deadline_seconds: float | None,
    executable: dict | None = None,
    output_root: Path | str | None = None,
) -> Path:
    """Create the one authoritative ledger if it is absent.

    ``executable`` (``{"path", "sha256"}``) and ``output_root`` are optional
    operator pins. They narrow an existing contract; they do not add a second
    authority or a concurrency control. An existing ledger is returned
    unchanged, so re-running an initialization command cannot reset a budget.
    """
    path = Path(path)
    if deadline_seconds is not None:
        validate_timeout(deadline_seconds, field="deadline_seconds")
    if path.exists():
        return path
    value = {
        "schema": LEDGER_SCHEMA,
        "workflow_id": workflow_id,
        "limit": int(limit),
        "deadline_seconds": deadline_seconds,
        "used": 0,
        "launches": [],
    }
    if executable is not None:
        value["executable"] = {
            "path": str(Path(executable["path"]).expanduser().resolve(strict=False)),
            "sha256": str(executable["sha256"]),
        }
    if output_root is not None:
        value["output_root"] = str(Path(output_root).expanduser().resolve(strict=False))
    _atomic(path, value)
    return path


def _within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


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
    executable = value.get("executable")
    if executable is not None:
        if not isinstance(executable, dict):
            errors.append("executable is not an object")
        elif not isinstance(executable.get("path"), str) or not executable["path"]:
            errors.append("executable.path is missing")
        elif not isinstance(executable.get("sha256"), str) or not executable["sha256"]:
            errors.append("executable.sha256 is missing")
    output_root = value.get("output_root")
    if output_root is not None and (not isinstance(output_root, str) or not output_root):
        errors.append("output_root is invalid")
    deadline = value.get("deadline_seconds")
    if deadline is not None:
        try:
            validate_timeout(deadline, field="deadline_seconds")
        except LedgerError as exc:
            errors.append(str(exc))
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
    executable_sha256: str | None = None,
    executable_path: str | None = None,
    require_pinned: bool = False,
) -> str:
    path = Path(path)
    requested_timeout = validate_timeout(requested_timeout, field="requested_timeout")
    handle = _locked(path)
    try:
        value = load_ledger(path)
        pinned = value.get("executable")
        root_value = value.get("output_root")
        if require_pinned:
            if not isinstance(pinned, dict) or not pinned.get("path") or not pinned.get("sha256"):
                raise LedgerError(
                    "unpinned",
                    "this authorization does not pin an executable identity",
                )
            if not isinstance(root_value, str) or not root_value:
                raise LedgerError(
                    "unpinned",
                    "this authorization does not pin an experiment output root",
                )
            if value.get("deadline_seconds") is None:
                raise LedgerError(
                    "unpinned",
                    "this authorization does not pin a per-launch deadline",
                )
            if executable_path is None:
                raise LedgerError(
                    "executable",
                    "the launch must attest its canonical executable path",
                )
        if isinstance(pinned, dict):
            if executable_sha256 is None:
                raise LedgerError(
                    "executable",
                    "the workflow pins an executable; the launch must attest its sha256",
                )
            if executable_sha256 != pinned.get("sha256"):
                raise LedgerError(
                    "executable",
                    "launch executable does not match the operator-pinned identity",
                )
            if executable_path is not None and pinned.get("path"):
                pinned_path = Path(str(pinned["path"])).expanduser().resolve(strict=False)
                actual_path = Path(executable_path).expanduser().resolve(strict=False)
                if str(actual_path) != str(pinned_path):
                    raise LedgerError(
                        "executable_path",
                        "launch executable path does not match the operator-pinned path",
                    )
        if isinstance(root_value, str):
            root = Path(root_value).expanduser().resolve(strict=False)
            destination = Path(output).expanduser().resolve(strict=False)
            if not _within(destination, root):
                raise LedgerError(
                    "output_root",
                    f"experiment output escapes the authorized root: {destination}",
                )
        if value["used"] >= value["limit"]:
            raise LedgerError(
                "exhausted",
                f"{value['used']}/{value['limit']} workflow launches already used",
            )
        deadline = value.get("deadline_seconds")
        if deadline is not None:
            deadline = validate_timeout(deadline, field="deadline_seconds")
            if requested_timeout > deadline:
                raise LedgerError(
                    "deadline",
                    f"requested {requested_timeout:g}s exceeds workflow "
                    f"limit {deadline:g}s",
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


def _norm_path(value) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return str(Path(text).expanduser().resolve(strict=False))


def _norm_deadline(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _validate_reauthorization(
    value: dict,
    *,
    workflow_id: str,
    limit: int,
    deadline_seconds: float | None,
    executable: dict | None,
    output_root: Path | str | None,
) -> None:
    """An existing ledger may only be reused for the same authorization."""
    existing_pin = value.get("executable")
    existing_pin = existing_pin if isinstance(existing_pin, dict) else None
    requested_path = _norm_path(executable.get("path")) if executable else None
    requested_sha = str(executable.get("sha256")) if executable else None
    existing_path = _norm_path(existing_pin.get("path")) if existing_pin else None
    existing_sha = str(existing_pin.get("sha256")) if existing_pin else None
    comparisons = [
        ("workflow_id", value.get("workflow_id"), workflow_id),
        ("limit", int(value.get("limit")), int(limit)),
        ("deadline_seconds", _norm_deadline(value.get("deadline_seconds")),
         _norm_deadline(deadline_seconds)),
        ("executable.path", existing_path, requested_path),
        ("executable.sha256", existing_sha, requested_sha),
        ("output_root", _norm_path(value.get("output_root")), _norm_path(output_root)),
    ]
    for field, existing, requested in comparisons:
        if existing != requested:
            raise LedgerError(
                "conflict",
                f"existing workflow authorization has a different {field}",
            )


def authorize_workflow(
    ledger_path: Path,
    run_root: Path,
    *,
    workflow_id: str,
    limit: int,
    deadline_seconds: float | None,
    executable: dict | None = None,
    output_root: Path | str | None = None,
) -> dict:
    """Operator initialization: one ledger plus a pointer in the run root.

    The pointer is a discovery aid. The ledger is authoritative and may live
    outside the worktree. An existing ledger is never rewritten, so the command
    cannot be used to replenish a spent budget.
    """
    ledger_path = Path(ledger_path).expanduser().resolve()
    run_root = Path(run_root).expanduser().resolve()
    if deadline_seconds is not None:
        validate_timeout(deadline_seconds, field="deadline_seconds")
    created = not ledger_path.exists()
    if created:
        create_ledger(
            ledger_path,
            workflow_id=workflow_id,
            limit=limit,
            deadline_seconds=deadline_seconds,
            executable=executable,
            output_root=output_root,
        )
        value = load_ledger(ledger_path)
    else:
        value = load_ledger(ledger_path)
        _validate_reauthorization(
            value,
            workflow_id=workflow_id,
            limit=limit,
            deadline_seconds=deadline_seconds,
            executable=executable,
            output_root=output_root,
        )
    write_pointer(
        run_root,
        workflow_id=value["workflow_id"],
        ledger_path=ledger_path,
        limit=value["limit"],
    )
    return {
        "schema": "launch-authorization/v1",
        "created": created,
        "ledger": str(ledger_path),
        "run_root": str(run_root),
        "workflow_id": value["workflow_id"],
        "limit": value["limit"],
        "used": value["used"],
        "deadline_seconds": value.get("deadline_seconds"),
        "executable": value.get("executable"),
        "output_root": value.get("output_root"),
    }


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
