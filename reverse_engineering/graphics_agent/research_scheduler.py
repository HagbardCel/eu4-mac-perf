"""Investigation state machine transitions."""

from __future__ import annotations

import sqlite3

STATES = frozenset(
    {
        "PENDING",
        "INVESTIGATING",
        "AWAITING_EVIDENCE",
        "CLAIMS_PROPOSED",
        "VERIFYING",
        "COMPLETED",
        "BLOCKED",
    }
)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"INVESTIGATING", "BLOCKED"}),
    "INVESTIGATING": frozenset({"AWAITING_EVIDENCE", "CLAIMS_PROPOSED", "BLOCKED", "COMPLETED"}),
    "AWAITING_EVIDENCE": frozenset({"INVESTIGATING", "BLOCKED"}),
    "CLAIMS_PROPOSED": frozenset({"VERIFYING", "INVESTIGATING", "BLOCKED"}),
    "VERIFYING": frozenset({"COMPLETED", "INVESTIGATING", "BLOCKED"}),
    "COMPLETED": frozenset(),
    "BLOCKED": frozenset({"INVESTIGATING", "PENDING"}),
}


class InvalidTransition(Exception):
    pass


def get_state(conn: sqlite3.Connection, investigation_id: str) -> str | None:
    row = conn.execute("SELECT state FROM investigations WHERE id = ?", (investigation_id,)).fetchone()
    return row["state"] if row else None


def transition(conn: sqlite3.Connection, investigation_id: str, new_state: str) -> None:
    if new_state not in STATES:
        raise InvalidTransition(f"unknown state {new_state}")
    current = get_state(conn, investigation_id)
    if current is None:
        raise InvalidTransition(f"unknown investigation {investigation_id}")
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if new_state not in allowed:
        raise InvalidTransition(f"cannot go from {current} to {new_state}")
    from reverse_engineering.graphics_agent.research_store import set_investigation_state

    set_investigation_state(conn, investigation_id, new_state)
