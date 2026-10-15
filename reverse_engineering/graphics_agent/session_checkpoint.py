"""Model session checkpoints (not architectural truth)."""

from __future__ import annotations

import hashlib
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


def save_message_history_json(conn: sqlite3.Connection, payload: bytes) -> None:
    set_meta(conn, "pydantic_message_history", payload.decode("utf-8"))


def load_message_history_json(conn: sqlite3.Connection) -> bytes | None:
    raw = get_meta(conn, "pydantic_message_history")
    return raw.encode("utf-8") if raw else None


def parse_message_history(conn: sqlite3.Connection) -> list[Any]:
    raw = load_message_history_json(conn)
    if not raw:
        return []
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    return ModelMessagesTypeAdapter.validate_json(raw)


def persist_run_messages(conn: sqlite3.Connection, result: Any) -> None:
    save_message_history_json(conn, result.all_messages_json())


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


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load_session_meta(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM checkpoint_meta").fetchall()
    return {r["key"]: r["value"] for r in rows}


def validate_resume_meta(conn: sqlite3.Connection, expected: dict[str, str]) -> list[str]:
    errors: list[str] = []
    stored = load_session_meta(conn)
    for key, value in expected.items():
        if stored.get(key) != value:
            errors.append(f"checkpoint meta mismatch for {key}")
    return errors


def write_session_meta(conn: sqlite3.Connection, meta: dict[str, str]) -> None:
    for key, value in meta.items():
        set_meta(conn, key, value)
