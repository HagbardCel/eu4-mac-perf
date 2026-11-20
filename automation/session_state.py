"""Per-session writable roots and the campaign-wide claim ledger.

This module owns two deliberately different authorities.

**The session root** is writable state that belongs to one agent session: its
goal/milestone binding, its validation-attempt log, its qualification receipt
and its finalization record.  It is derived from a validated session id::

    <run-root>/sessions/<session-id>/

``<run-root>`` itself stays the campaign-wide root.  Two sessions on one
campaign therefore cannot overwrite each other's bindings or logs, and a
reconnect that presents the same session id resumes exactly the same directory.
The derivation rejects anything that is not a plain lowercase identifier:
path separators, ``.``/``..`` components, whitespace, control characters and
over-long names.  After creation the directory is resolved again and must still
be inside the campaign root, so a symlink at the session path cannot redirect
writes outside it.  Symlinked session directories are refused outright.

A caller with **no** session id keeps the pre-existing shared layout at the run
root itself.  That choice is explicit: :class:`SessionLayout` reports
``kind="legacy-shared"`` and a human-readable ``reason``, which every bridge
result carries.  It is not silently presented as an isolated session.

**The campaign claim ledger** is the opposite: one append-only file for the
whole campaign, ::

    <run-root>/campaign/claims.jsonl

guarded by ``flock(2)`` on ``claims.lock``.  A checker execution is claimed
with :meth:`CampaignClaims.claim` *before* it starts; the limit check and the
append happen in the same critical section, so two sessions cannot both spend
the last validation attempt.  Claims are never deleted (an interrupted claim
keeps its charge).  Per-session validation logs remain the detailed attempt
audit trail; the ledger is the allowance counter.

The host-wide EU4 process lease is untouched by this module: it lives in
``automation.launch_lease`` and stays one host-wide lease regardless of how many
sessions or roots exist.  This module does not add a scheduler, database or
second budget authority.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

LAYOUT_SCHEMA = "eu4-dsh-session-layout/v1"
SESSION_RECORD = "session.json"
SESSIONS_DIRNAME = "sessions"

CLAIM_SCHEMA = "eu4-dsh-campaign-claim/v1"
CLAIMS_DIRNAME = "campaign"
CLAIMS_LOGNAME = "claims.jsonl"
CLAIMS_LOCKNAME = "claims.lock"

MAX_SESSION_ID = 128
# A DSH session id is a UUID.  This accepts the readable ids used by tests and
# headless runs too, while excluding every path separator and dot component.
_SESSION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class SessionRootError(ValueError):
    """A session id or session root cannot be used safely."""


class ClaimLedgerError(ValueError):
    """The campaign claim ledger is corrupt, truncated or unreadable.

    The ledger is the campaign-wide budget authority.  A reader that skipped a
    bad row (or returned zero rows for an unreadable file) would silently reset
    the allowance and let a caller spend attempts that were already used.  The
    only safe response is to refuse until the operator inspects the file.
    """

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def validate_session_id(value) -> str:
    """Return *value* as a safe directory component or raise.

    Only a lowercase ASCII identifier is accepted.  Case-insensitive
    filesystems (the default on macOS) cannot distinguish ``A`` from ``a``, so
    rejecting uppercase is what actually guarantees one root per id there.
    """
    if not isinstance(value, str):
        raise SessionRootError("session id must be a string")
    if not value:
        raise SessionRootError("session id must not be empty")
    if value != value.strip():
        raise SessionRootError("session id must not contain leading or trailing whitespace")
    if len(value) > MAX_SESSION_ID:
        raise SessionRootError(f"session id must be at most {MAX_SESSION_ID} characters")
    if value != value.lower():
        raise SessionRootError(
            "session id must be lowercase ASCII (the filesystem may be case-insensitive)")
    if not _SESSION_ID_RE.fullmatch(value):
        raise SessionRootError(
            "session id must be a plain identifier of letters, digits, '.', '_' "
            "and '-' (no path separators, no '..' component)")
    return value


@dataclass(frozen=True)
class SessionLayout:
    """Where one session's writable state lives, and why.

    ``kind`` is ``"session"`` for an isolated root or ``"legacy-shared"`` for a
    caller that supplied no session id.  ``reason`` is safe to show an operator
    or an agent.
    """

    campaign_root: Path
    session_id: str | None
    root: Path
    kind: str
    reason: str

    @property
    def isolated(self) -> bool:
        return self.kind == "session"

    @property
    def binding_path(self) -> Path:
        return self.root / "plugin-binding.json"

    @property
    def record_path(self) -> Path:
        return self.root / SESSION_RECORD

    @property
    def validations_path(self) -> Path:
        return self.root / "validations.jsonl"

    @property
    def qualification_path(self) -> Path:
        return self.root / "qualification.json"

    @property
    def finalization_path(self) -> Path:
        return self.root / "finalization.json"

    def as_dict(self) -> dict:
        return {
            "schema": LAYOUT_SCHEMA,
            "session_id": self.session_id,
            "campaign_root": str(self.campaign_root),
            "root": str(self.root),
            "kind": self.kind,
            "isolated": self.isolated,
            "reason": self.reason,
            "binding": str(self.binding_path),
            "validations": str(self.validations_path),
            "qualification": str(self.qualification_path),
            "finalization": str(self.finalization_path),
        }


def _campaign_root(run_root: Path | str) -> Path:
    return Path(run_root).expanduser().resolve(strict=False)


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _refuse_symlinked(path: Path, what: str) -> None:
    if path.is_symlink():
        raise SessionRootError(f"{what} must not be a symlink: {path}")


def layout(run_root: Path | str, session_id: str | None = None, *,
           create: bool = False) -> SessionLayout:
    """Resolve the writable root for *session_id* beneath *run_root*.

    ``session_id=None`` selects the legacy shared layout explicitly.  An empty
    string is not "no session": it is refused, because an empty id is a caller
    bug that would otherwise collide with the legacy root.

    ``create=True`` creates ``<run-root>/sessions/<id>`` (mode ``0700``) and
    re-resolves it before returning.  Every path component between the campaign
    root and the session root is checked for a symlink, and the final resolved
    directory must still be inside the campaign root.
    """
    campaign_root = _campaign_root(run_root)
    if session_id is None:
        return SessionLayout(
            campaign_root=campaign_root,
            session_id=None,
            root=campaign_root,
            kind="legacy-shared",
            reason=(
                "no session id was supplied, so this session uses the campaign's "
                "shared run root explicitly (legacy layout); supply a session id "
                "to get an isolated writable root"
            ),
        )

    validated = validate_session_id(session_id)
    sessions_dir = campaign_root / SESSIONS_DIRNAME
    _refuse_symlinked(sessions_dir, "the sessions directory")
    if sessions_dir.exists() and not sessions_dir.is_dir():
        raise SessionRootError(f"sessions path is not a directory: {sessions_dir}")

    target = sessions_dir / validated
    _refuse_symlinked(target, "the session root")
    if target.exists() and not target.is_dir():
        raise SessionRootError(f"session root exists but is not a directory: {target}")
    if not _inside(target, campaign_root):
        raise SessionRootError(
            "the session root would escape the campaign root; refusing to use it")

    if create:
        sessions_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.mkdir(mode=0o700, exist_ok=True)
        try:
            target.chmod(0o700)
        except OSError:
            pass
        _refuse_symlinked(target, "the session root")
        resolved = target.resolve(strict=True)
        if not _inside(resolved, campaign_root) or resolved == campaign_root:
            raise SessionRootError(
                "the session root resolves outside the campaign root; refusing "
                "a symlink escape")
        target = resolved
    elif not target.is_dir():
        raise SessionRootError(
            f"session root does not exist; prepare must run first: {target}")

    return SessionLayout(
        campaign_root=campaign_root,
        session_id=validated,
        root=target,
        kind="session",
        reason=f"isolated per-session root derived from the validated session id {validated!r}",
    )


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def write_session_record(layout_value: SessionLayout, *, campaign_id: str) -> tuple[Path | None, bool]:
    """Record (or verify) the binding between a session id and its root.

    Returns ``(path, created)``.  A reconnect finds the existing record and does
    not rewrite it; a record that names a different campaign or session is
    refused instead of being overwritten, so a reused directory cannot absorb
    another campaign's identity.
    """
    if not layout_value.isolated:
        return None, False
    path = layout_value.record_path
    existing = None
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SessionRootError(f"session record is unreadable: {path}: {exc}") from exc
        if not isinstance(existing, dict):
            raise SessionRootError(f"session record is not a JSON object: {path}")
        if existing.get("campaign_id") != campaign_id \
                or existing.get("session_id") != layout_value.session_id:
            raise SessionRootError(
                "the session root belongs to another campaign/session "
                f"({existing.get('campaign_id')!r}/{existing.get('session_id')!r}); "
                "refusing to reuse it")
        return path, False
    _write_json(path, {
        "schema": LAYOUT_SCHEMA,
        "campaign_id": campaign_id,
        "session_id": layout_value.session_id,
        "root": str(layout_value.root),
        "campaign_root": str(layout_value.campaign_root),
        "created_at": time.time(),
    })
    return path, True


class CampaignClaims:
    """The campaign-wide, atomic validation-attempt ledger.

    One row per claimed checker execution.  The file is append-only: a claim is
    written before the checker starts and is never removed, so an interrupted
    execution keeps its charge and a reconnect cannot reset it.
    """

    def __init__(self, campaign_root: Path | str):
        self.root = _campaign_root(campaign_root)
        self.directory = self.root / CLAIMS_DIRNAME
        self.path = self.directory / CLAIMS_LOGNAME
        self.lock_path = self.directory / CLAIMS_LOCKNAME

    @contextlib.contextmanager
    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass
        try:
            handle = self.lock_path.open("a+")
        except OSError as exc:
            raise ClaimLedgerError(
                "unreadable_lock", f"{self.lock_path}: {exc}") from exc
        with handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def rows(self) -> list[dict]:
        """Return every claim row, or refuse the whole ledger.

        A missing ledger is the legitimate zero-attempt state.  Every other
        read failure -- permissions, a directory in place of the file, invalid
        UTF-8, a partial final record, a blank line, or a row that is not a
        claim object -- is an integrity error.  Skipping such a row would make
        the corrupted state look like fewer attempts, which is exactly the
        budget-reset failure this ledger exists to prevent.
        """
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise ClaimLedgerError(
                "unreadable", f"{self.path}: {exc}") from exc
        if not raw:
            return []
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ClaimLedgerError(
                "invalid_encoding", f"{self.path}: {exc}") from exc
        if not text.endswith("\n"):
            raise ClaimLedgerError(
                "truncated",
                f"{self.path} ends with an incomplete record (no final newline)")
        rows: list[dict] = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                raise ClaimLedgerError(
                    "malformed_record",
                    f"{self.path}: line {number} is blank")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ClaimLedgerError(
                    "malformed_record",
                    f"{self.path}: line {number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ClaimLedgerError(
                    "malformed_record",
                    f"{self.path}: line {number} is not a JSON object")
            if value.get("schema") != CLAIM_SCHEMA:
                raise ClaimLedgerError(
                    "malformed_record",
                    f"{self.path}: line {number} has schema "
                    f"{value.get('schema')!r}, expected {CLAIM_SCHEMA!r}")
            attempt_id = value.get("attempt_id")
            if not isinstance(attempt_id, str) or not attempt_id:
                raise ClaimLedgerError(
                    "malformed_record",
                    f"{self.path}: line {number} has no non-empty attempt_id")
            rows.append(value)
        return rows

    def claim(self, *, operation: str, attempt_id: str,
              session_id: str | None, limit: int | None,
              at: float | None = None) -> dict:
        """Atomically claim one checker execution.

        ``limit`` of ``None`` or ``0`` means no external cap.  Returns
        ``{"ok": bool, "used": int, ...}``; on refusal nothing is appended.  A
        duplicate ``attempt_id`` is idempotent and is not charged twice.
        """
        at = time.time() if at is None else float(at)
        cap = None if limit in (None, 0) else int(limit)
        with self._locked():
            rows = self.rows()
            used = len(rows)
            if any(row.get("attempt_id") == attempt_id for row in rows):
                return {"ok": True, "used": used, "limit": cap,
                        "path": str(self.path), "duplicate": True}
            if cap is not None and used >= cap:
                return {"ok": False, "kind": "budget_exhausted", "used": used,
                        "limit": cap, "path": str(self.path)}
            row = {
                "schema": CLAIM_SCHEMA,
                "attempt_id": attempt_id,
                "operation": operation,
                "session_id": session_id,
                "at": at,
            }
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return {"ok": True, "used": used + 1, "limit": cap,
                    "path": str(self.path), "row": row}
