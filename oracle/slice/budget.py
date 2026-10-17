"""Run-scoped, restart-safe tool-call budget ledger.

The controller creates a run with a fixed call budget. The trusted MCP bridge
reserves against that budget *before* executing a tool, so the limit is
authoritative rather than advisory. Guarantees:

* Budget identity is an immutable ``run_id``; a run's limit is written once.
* Admission and reservation are atomic (a single SQLite ``BEGIN IMMEDIATE``
  transaction), so concurrent calls cannot over-admit at the boundary.
* A reservation is consumed even if the tool then fails.
* Restarting the MCP server cannot reset counters: state lives in the ledger
  file, not in process memory.
* Missing or corrupted ledger state denies; it never resets.

The same transaction that reserves a call also appends its journal row, so a
call can never be *reserved but unrecorded* - the accounting evidence and the
counter move together or not at all. Pre-admission rejections get their own
rows and never touch the counter. Pending rows (an admitted call whose outcome
was never finalised) are read as ``incomplete``: the failure mode is explicit
rather than silently lost.

SQLite is the simplest implementation meeting those invariants.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LEDGER_FILE = "budget.sqlite3"
#: Name of the sanitised journal export written into the run directory.
JOURNAL_EXPORT_FILE = "tool-calls.json"
#: Schema tag for the journal export, published alongside the run.
JOURNAL_SCHEMA_VERSION = "eu4-oracle-tool-calls/v1"
#: Busy timeout for a single ``BEGIN IMMEDIATE`` attempt. Kept short so the retry
#: loop - not a blocking SQLite call - enforces the run deadline.
_BUSY_TIMEOUT_S = 0.5
#: Upper bound on the lock-wait retry loop when no run deadline is supplied.
_MAX_LOCK_WAIT_S = 30.0
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS runs ("
    " run_id TEXT PRIMARY KEY,"
    " limit_calls INTEGER NOT NULL,"
    " granted_calls INTEGER NOT NULL,"
    " created TEXT NOT NULL)"
)
#: The tool-call journal. One row per bridge-reached invocation, written in the
#: same transaction as (or in lieu of) its reservation.
_JOURNAL_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS tool_calls ("
    " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " run_id TEXT NOT NULL,"
    " invocation TEXT,"
    " tool TEXT NOT NULL,"
    " decision TEXT NOT NULL,"
    " reason TEXT NOT NULL,"
    " outcome TEXT NOT NULL,"
    " created TEXT NOT NULL)"
)

# --- journal vocabulary -------------------------------------------------------
#: The call was admitted: its reservation succeeded and the handler ran (or a
#: failure was discovered only *after* admission, which keeps the reservation).
DECISION_ADMITTED = "admitted"
#: The call was refused before admission, so no reservation was consumed.
DECISION_REJECTED = "rejected"

REASON_ADMITTED = "admitted"
REASON_UNKNOWN_TOOL = "unknown_tool"
REASON_BAD_ARGUMENTS = "bad_arguments"
REASON_DEADLINE_EXPIRED = "deadline_expired"
REASON_BUDGET_EXHAUSTED = "budget_exhausted"
REASON_BUDGET_DENIED = "budget_denied"
#: A ``tools/call`` message carried no request id, so no response - and hence no
#: agent-visible invocation - could ever exist for it.
REASON_UNANSWERABLE = "missing_request_id"
#: Rejections that *do* correspond to an agent-visible invocation and therefore
#: must be reconciled against the OpenCode event stream.
RECONCILABLE_REJECTIONS = (
    REASON_UNKNOWN_TOOL,
    REASON_BAD_ARGUMENTS,
    REASON_DEADLINE_EXPIRED,
    REASON_BUDGET_EXHAUSTED,
    REASON_BUDGET_DENIED,
)

OUTCOME_PENDING = "pending"
OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"
OUTCOME_INCOMPLETE = "incomplete"
OUTCOME_NOT_APPLICABLE = "n/a"


class BudgetError(RuntimeError):
    """Base class for budget failures."""


class BudgetDenied(BudgetError):
    """Ledger state is missing, corrupted or unreadable: deny, never reset."""


class BudgetConflict(BudgetError):
    """A run id already exists with a different limit."""


class BudgetExceeded(BudgetError):
    """The reservation would exceed the run budget."""

    def __init__(self, granted: int, limit: int):
        super().__init__(f"tool-call budget exhausted: {granted}/{limit}")
        self.granted = granted
        self.limit = limit


@dataclass(frozen=True)
class Admission:
    """The trusted record of one admitted call."""

    #: Journal row id, used to finalise the call's outcome.
    seq: int
    #: The granted total *after* this reservation.
    granted: int
    limit: int


def invocation_token(value: Any) -> str | None:
    """Normalise a client-supplied invocation id into a journal-safe token.

    The value never reaches the journal verbatim: anything outside a conservative
    token alphabet is replaced by its digest, so an agent cannot smuggle text
    through the trusted-side accounting record.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if 0 < len(value) <= 128 and all(c.isalnum() or c in ":_-." for c in value):
            return value
        return "sha256:" + hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]
    return None


def call_label(value: Any) -> str:
    """A journal-safe label for a tool name that may be missing or malformed."""
    if isinstance(value, str) and 0 < len(value) <= 64:
        return "".join(c if (c.isalnum() or c in "_-.") else "_" for c in value)
    return "<invalid>"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class RunBudgetLedger:
    """A handle on one run's budget row inside a shared ledger file."""

    def __init__(self, path: Path, run_id: str):
        self.path = Path(path)
        self.run_id = str(run_id)
        self._conn = self._connect()

    # -- construction ---------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(str(self.path), timeout=_BUSY_TIMEOUT_S, isolation_level=None)
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            raise BudgetDenied(f"cannot open budget ledger: {exc}") from exc
        try:
            check = conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise BudgetDenied(f"budget ledger is corrupted: {exc}") from exc
        if not check or check[0] != "ok":
            conn.close()
            raise BudgetDenied("budget ledger failed its integrity check")
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)
            conn.execute(_JOURNAL_SCHEMA)
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise BudgetDenied(f"cannot initialise budget ledger: {exc}") from exc
        return conn

    @classmethod
    def create(cls, run_dir: Path, run_id: str, limit: int) -> "RunBudgetLedger":
        """Create (or assert) the run row. Controller-only."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        ledger = cls(Path(run_dir) / LEDGER_FILE, run_id)
        row = ledger._row()
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        if row is None:
            try:
                ledger._conn.execute(
                    "INSERT INTO runs(run_id, limit_calls, granted_calls, created) VALUES(?,?,0,?)",
                    (ledger.run_id, int(limit), now),
                )
            except sqlite3.IntegrityError as exc:  # pragma: no cover - race
                raise BudgetConflict(f"run {run_id!r} was created concurrently") from exc
        elif row[0] != int(limit):
            ledger.close()
            raise BudgetConflict(
                f"run {run_id!r} exists with limit {row[0]}, refusing {int(limit)}"
            )
        return ledger

    @classmethod
    def attach(cls, path: Path, run_id: str) -> "RunBudgetLedger":
        """Attach to an existing run row. The bridge must not create runs."""
        ledger = cls(path, run_id)
        if ledger._row() is None:
            ledger.close()
            raise BudgetDenied(f"no ledger row for run {run_id!r}")
        return ledger

    # -- accounting -----------------------------------------------------------
    def _row(self) -> tuple[int, int] | None:
        try:
            row = self._conn.execute(
                "SELECT limit_calls, granted_calls FROM runs WHERE run_id=?", (self.run_id,)
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise BudgetDenied(f"cannot read budget ledger: {exc}") from exc
        return (int(row[0]), int(row[1])) if row else None

    def reserve(self, cost: int = 1, *, deadline: float | None = None) -> int:
        """Atomically reserve ``cost`` calls; return the new granted total.

        ``deadline`` is an absolute monotonic bound on how long the caller may
        wait for the write lock. Waiting past it denies rather than blocks, so a
        contended ledger cannot outlive the run it belongs to.
        """
        if cost <= 0:
            raise ValueError("cost must be positive")
        stop = time.monotonic() + _MAX_LOCK_WAIT_S
        if deadline is not None:
            stop = min(stop, deadline)
        while True:
            if time.monotonic() >= stop:
                raise BudgetDenied("timed out waiting for the budget lock")
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and time.monotonic() < stop:
                    time.sleep(0.01)
                    continue
                raise BudgetDenied(f"cannot acquire budget lock: {exc}") from exc
            try:
                row = self._conn.execute(
                    "SELECT limit_calls, granted_calls FROM runs WHERE run_id=?", (self.run_id,)
                ).fetchone()
                if row is None:
                    self._conn.execute("ROLLBACK")
                    raise BudgetDenied(f"no ledger row for run {self.run_id!r}")
                limit_calls, granted = int(row[0]), int(row[1])
                if granted + cost > limit_calls:
                    self._conn.execute("ROLLBACK")
                    raise BudgetExceeded(granted, limit_calls)
                self._conn.execute(
                    "UPDATE runs SET granted_calls=granted_calls+? WHERE run_id=?",
                    (cost, self.run_id),
                )
                self._conn.execute("COMMIT")
                return granted + cost
            except sqlite3.DatabaseError as exc:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:  # pragma: no cover - defensive
                    pass
                raise BudgetDenied(f"budget ledger write failed: {exc}") from exc

    # -- journal --------------------------------------------------------------
    def reserve_call(self, *, tool: str, invocation: Any = None, cost: int = 1,
                     deadline: float | None = None) -> Admission:
        """Atomically reserve ``cost`` calls **and** journal the admission.

        The counter increment and the journal row are written in the same
        ``BEGIN IMMEDIATE`` transaction: there is no state in which a call is
        reserved but unrecorded. A journalling failure rolls the reservation
        back, so the caller refuses the call before running its handler. The row
        starts ``pending`` and must be finalised with :meth:`finish_call`.
        """
        if cost <= 0:
            raise ValueError("cost must be positive")
        token = invocation_token(invocation)
        stop = time.monotonic() + _MAX_LOCK_WAIT_S
        if deadline is not None:
            stop = min(stop, deadline)
        while True:
            if time.monotonic() >= stop:
                raise BudgetDenied("timed out waiting for the budget lock")
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and time.monotonic() < stop:
                    time.sleep(0.01)
                    continue
                raise BudgetDenied(f"cannot acquire budget lock: {exc}") from exc
            try:
                row = self._conn.execute(
                    "SELECT limit_calls, granted_calls FROM runs WHERE run_id=?", (self.run_id,)
                ).fetchone()
                if row is None:
                    self._conn.execute("ROLLBACK")
                    raise BudgetDenied(f"no ledger row for run {self.run_id!r}")
                limit_calls, granted = int(row[0]), int(row[1])
                if granted + cost > limit_calls:
                    self._conn.execute("ROLLBACK")
                    raise BudgetExceeded(granted, limit_calls)
                self._conn.execute(
                    "UPDATE runs SET granted_calls=granted_calls+? WHERE run_id=?",
                    (cost, self.run_id),
                )
                self._conn.execute(
                    "INSERT INTO tool_calls"
                    "(run_id, invocation, tool, decision, reason, outcome, created)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (self.run_id, token, call_label(tool), DECISION_ADMITTED,
                     REASON_ADMITTED, OUTCOME_PENDING, _now()),
                )
                seq = int(self._conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                self._conn.execute("COMMIT")
                return Admission(seq=seq, granted=granted + cost, limit=limit_calls)
            except sqlite3.DatabaseError as exc:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:  # pragma: no cover - defensive
                    pass
                raise BudgetDenied(f"budget ledger write failed: {exc}") from exc

    def record_rejection(self, *, tool: Any, reason: str, invocation: Any = None) -> int:
        """Journal a pre-admission rejection. Never touches the granted counter.

        Raises :class:`BudgetDenied` if the journal write fails, so a caller can
        surface the failure instead of silently losing accounting evidence.
        """
        token = invocation_token(invocation)
        try:
            self._conn.execute(
                "INSERT INTO tool_calls"
                "(run_id, invocation, tool, decision, reason, outcome, created)"
                " VALUES(?,?,?,?,?,?,?)",
                (self.run_id, token, call_label(tool), DECISION_REJECTED,
                 reason, OUTCOME_NOT_APPLICABLE, _now()),
            )
            return int(self._conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            raise BudgetDenied(f"cannot journal tool call: {exc}") from exc

    def finish_call(self, seq: int, outcome: str) -> None:
        """Finalise an admitted call's outcome.

        Best-effort by contract: a failure leaves the row ``pending``, which the
        reader reports as ``incomplete``. That is the safe direction - an
        unfinalised call is never presented as completed.
        """
        try:
            self._conn.execute(
                "UPDATE tool_calls SET outcome=? WHERE seq=? AND run_id=? AND decision=? AND outcome=?",
                (outcome, int(seq), self.run_id, DECISION_ADMITTED, OUTCOME_PENDING),
            )
        except sqlite3.DatabaseError:  # pragma: no cover - defensive
            pass

    def seal_pending(self) -> int:
        """Stamp every still-``pending`` admitted call as ``incomplete``.

        Called once at run finalisation so a completed run's journal has no
        ambiguous rows; the reader maps ``pending`` to ``incomplete`` anyway, so
        a run that dies before sealing is not misreported.
        """
        try:
            cur = self._conn.execute(
                "UPDATE tool_calls SET outcome=? WHERE run_id=? AND decision=? AND outcome=?",
                (OUTCOME_INCOMPLETE, self.run_id, DECISION_ADMITTED, OUTCOME_PENDING),
            )
            return int(cur.rowcount)
        except sqlite3.DatabaseError as exc:  # pragma: no cover - defensive
            raise BudgetDenied(f"cannot seal tool-call journal: {exc}") from exc

    def tool_calls(self) -> list[dict[str, Any]]:
        """Read the run's journal in order, mapping ``pending`` to ``incomplete``."""
        rows = self._conn.execute(
            "SELECT seq, invocation, tool, decision, reason, outcome, created"
            " FROM tool_calls WHERE run_id=? ORDER BY seq",
            (self.run_id,),
        ).fetchall()
        return [journal_row(row) for row in rows]

    def granted(self) -> int:
        row = self._row()
        if row is None:
            raise BudgetDenied(f"no ledger row for run {self.run_id!r}")
        return row[1]

    def limit(self) -> int:
        row = self._row()
        if row is None:
            raise BudgetDenied(f"no ledger row for run {self.run_id!r}")
        return row[0]

    def remaining(self) -> int:
        row = self._row()
        if row is None:
            raise BudgetDenied(f"no ledger row for run {self.run_id!r}")
        return row[0] - row[1]

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:  # pragma: no cover - defensive
            pass


# --- journal readers ----------------------------------------------------------


def journal_row(row: tuple[Any, ...]) -> dict[str, Any]:
    """Normalise one ``tool_calls`` row into a journal record.

    A ``pending`` admitted row is reported as ``incomplete``: an unfinalised call
    is evidence of a lost outcome, never of success.
    """
    seq, invocation, tool, decision, reason, outcome, created = row
    if decision == DECISION_ADMITTED and outcome == OUTCOME_PENDING:
        outcome = OUTCOME_INCOMPLETE
    return {
        "seq": int(seq),
        "invocation": invocation,
        "tool": tool,
        "decision": decision,
        "reason": reason,
        "outcome": outcome,
        "created": created,
    }


def summarize_calls(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a journal into the reconciliation census used by the receipt."""
    admitted = [r for r in rows if r["decision"] == DECISION_ADMITTED]
    rejected = [r for r in rows if r["decision"] == DECISION_REJECTED]
    answerable = [r for r in rejected if r["reason"] != REASON_UNANSWERABLE]

    def _tally(records: list[dict[str, Any]], key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in records:
            label = str(record[key])
            counts[label] = counts.get(label, 0) + 1
        return dict(sorted(counts.items()))

    return {
        "schema": JOURNAL_SCHEMA_VERSION,
        "records": len(rows),
        "admitted": len(admitted),
        "rejected": len(rejected),
        #: Rejections that correspond to an agent-visible invocation, i.e. the
        #: ones the OpenCode event stream must also show.
        "rejected_answerable": len(answerable),
        "admitted_by_tool": _tally(admitted, "tool"),
        "rejected_by_tool": _tally(answerable, "tool"),
        "rejected_by_reason": _tally(rejected, "reason"),
        "admitted_outcomes": _tally(admitted, "outcome"),
        "incomplete": sum(1 for r in admitted if r["outcome"] == OUTCOME_INCOMPLETE),
    }


def export_calls(rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Serialise a journal for publication; return ``(text, summary)``.

    The export is deterministic (sorted keys, fixed indentation) so its digest is
    stable, and it contains no tool arguments - only the decision record.
    """
    payload = {"schema": JOURNAL_SCHEMA_VERSION, "calls": rows}
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    summary = summarize_calls(rows)
    summary["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, summary
