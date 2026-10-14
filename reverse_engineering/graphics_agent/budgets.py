"""Persistent budget enforcement (shared by runner and MCP processes)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


class BudgetExceeded(Exception):
    pass


@dataclass
class BudgetLimits:
    max_tool_calls: int = 150
    max_new_decompilations: int = 20
    max_graph_nodes: int = 250
    max_ghidra_queries: int = 80
    max_output_bytes: int = 512_000
    max_concurrent_ghidra: int = 1


def ensure_budget_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS budget_counters (
          investigation_id TEXT NOT NULL,
          counter_key TEXT NOT NULL,
          value INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (investigation_id, counter_key)
        );
        """
    )


def increment_counter(
    conn: sqlite3.Connection,
    investigation_id: str,
    counter_key: str,
    delta: int = 1,
) -> int:
    ensure_budget_tables(conn)
    conn.execute(
        """
        INSERT INTO budget_counters (investigation_id, counter_key, value)
        VALUES (?, ?, ?)
        ON CONFLICT(investigation_id, counter_key) DO UPDATE SET
          value = value + excluded.value
        """,
        (investigation_id, counter_key, delta),
    )
    row = conn.execute(
        "SELECT value FROM budget_counters WHERE investigation_id = ? AND counter_key = ?",
        (investigation_id, counter_key),
    ).fetchone()
    return int(row[0]) if row else 0


def get_counter(conn: sqlite3.Connection, investigation_id: str, counter_key: str) -> int:
    ensure_budget_tables(conn)
    row = conn.execute(
        "SELECT value FROM budget_counters WHERE investigation_id = ? AND counter_key = ?",
        (investigation_id, counter_key),
    ).fetchone()
    return int(row[0]) if row else 0


def check_and_increment(
    conn: sqlite3.Connection,
    investigation_id: str,
    counter_key: str,
    limit: int,
    delta: int = 1,
) -> int:
    current = get_counter(conn, investigation_id, counter_key)
    if current + delta > limit:
        raise BudgetExceeded(f"{counter_key} would exceed limit {limit} (current {current})")
    return increment_counter(conn, investigation_id, counter_key, delta)


def limits_from_config(config: dict[str, Any]) -> BudgetLimits:
    b = config.get("budgets", config)
    return BudgetLimits(
        max_tool_calls=int(b.get("max_tool_calls", 150)),
        max_new_decompilations=int(b.get("max_new_decompilations", 20)),
        max_graph_nodes=int(b.get("max_graph_nodes", 250)),
        max_ghidra_queries=int(b.get("max_ghidra_queries", 80)),
        max_output_bytes=int(b.get("max_output_bytes", 512_000)),
        max_concurrent_ghidra=int(b.get("max_concurrent_ghidra", 1)),
    )
