"""Derived architecture map and research backlog snapshots from research.sqlite."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

DEFAULT_BACKLOG_DOMAINS: list[dict[str, str]] = [
    {"domain": "frame_lifecycle", "focus": "Frame entry, scheduling, sync, presentation"},
    {"domain": "rendering_passes", "focus": "Map, borders, terrain, meshes, text, UI"},
    {"domain": "gfx_abstraction", "focus": "Draw, state, shader, texture, buffer, RT APIs"},
    {"domain": "shader_state", "focus": "Programs, uniforms, samplers, pipeline"},
    {"domain": "geometry", "focus": "VBO/IBO, layouts, upload, draw submit"},
    {"domain": "resource_lifecycle", "focus": "Create, cache, invalidate, destroy"},
    {"domain": "framebuffers", "focus": "Offscreen, attachments, swap"},
    {"domain": "platform_backend", "focus": "GL/CGL, context, window, Cocoa integration"},
]


def seed_backlog_items(conn: sqlite3.Connection, investigation_id: str) -> None:
    for item in DEFAULT_BACKLOG_DOMAINS:
        exists = conn.execute(
            "SELECT 1 FROM open_questions WHERE investigation_id = ? AND domain = ?",
            (investigation_id, item["domain"]),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO open_questions (investigation_id, domain, question, status) VALUES (?, ?, ?, 'open')",
            (
                investigation_id,
                item["domain"],
                f"Characterize {item['domain']}: {item['focus']}",
            ),
        )
    conn.commit()


def build_backlog_snapshot(
    conn: sqlite3.Connection,
    investigation_id: str,
    research_revision: int,
    input_digests: dict[str, str],
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT domain, question, status FROM open_questions
        WHERE investigation_id = ? ORDER BY id
        """,
        (investigation_id,),
    ).fetchall()
    return {
        "schema": 1,
        "generated_at": time.time(),
        "research_revision": research_revision,
        "input_digests": input_digests,
        "items": [dict(r) for r in rows],
    }


def build_architecture_map_snapshot(
    conn: sqlite3.Connection,
    investigation_id: str,
    research_revision: int,
    input_digests: dict[str, str],
    briefing_subsystems: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    claims = conn.execute(
        """
        SELECT claim_id, statement, scientific_status, review_status, evidence_ids_json
        FROM claims WHERE investigation_id = ? AND superseded_by IS NULL
        """,
        (investigation_id,),
    ).fetchall()
    dossiers = conn.execute(
        "SELECT entry_rva, subsystem, role, membership_status, body_json FROM dossiers WHERE investigation_id = ?",
        (investigation_id,),
    ).fetchall()
    return {
        "schema": 1,
        "generated_at": time.time(),
        "research_revision": research_revision,
        "input_digests": input_digests,
        "subsystems": briefing_subsystems or [],
        "claims": [
            {
                "claim_id": c["claim_id"],
                "statement": c["statement"],
                "scientific_status": c["scientific_status"],
                "review_status": c["review_status"],
                "evidence_ids": json.loads(c["evidence_ids_json"]),
            }
            for c in claims
        ],
        "function_dossiers": [
            {
                "entry_rva": d["entry_rva"],
                "subsystem": d["subsystem"],
                "role": d["role"],
                "membership_status": d["membership_status"],
                "body": json.loads(d["body_json"]),
            }
            for d in dossiers
        ],
    }
