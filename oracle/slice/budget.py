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

SQLite is the simplest implementation meeting those invariants.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import time
from pathlib import Path

LEDGER_FILE = "budget.sqlite3"
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
