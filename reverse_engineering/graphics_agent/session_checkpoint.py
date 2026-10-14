"""Model session checkpoints (not architectural truth)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def connect(checkpoint_path: Path) -> sqlite3.Connection:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(checkpoint_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS checkpoint_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          role TEXT NOT NULL,
          content_json TEXT NOT NULL,
          created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending_tools (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          tool_name TEXT NOT NULL,
          args_json TEXT NOT NULL,
          created_at REAL NOT NULL
        );
        """
    )
    return conn


def save_messages(conn: sqlite3.Connection, messages: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM messages")
    now = time.time()
    for msg in messages:
        conn.execute(
            "INSERT INTO messages (role, content_json, created_at) VALUES (?, ?, ?)",
            (msg.get("role", "unknown"), json.dumps(msg), now),
        )
    conn.commit()


def load_messages(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT content_json FROM messages ORDER BY id").fetchall()
    return [json.loads(r["content_json"]) for r in rows]


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO checkpoint_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM checkpoint_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None
