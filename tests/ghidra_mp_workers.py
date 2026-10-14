"""Picklable multiprocessing workers for Ghidra acquire tests."""

from __future__ import annotations

import os
from pathlib import Path

from reverse_engineering.graphics_agent.ghidra_acquire import ghidra_try_acquire
from reverse_engineering.graphics_agent.research_store import connect, ensure_investigation


def mp_try_acquire(
    db_path: str,
    inv_digest: str,
    investigation_id: str,
    project_key: str,
    idempotency_key: str,
    owner: str,
    quota_limit: int,
    barrier: object,
    out_queue: object,
) -> None:
    conn = connect(Path(db_path))
    ensure_investigation(conn, investigation_id, "bin", inv_digest, "rev")
    barrier.wait()
    try:
        ghidra_try_acquire(
            conn,
            project_key=project_key,
            idempotency_key=idempotency_key,
            investigation_id=investigation_id,
            operation="disassembly",
            args={"rvas": ["0x0000000000005000"]},
            owner=owner,
            worker_pid=os.getpid(),
            quota_key="ghidra_queries",
            quota_limit=quota_limit,
            quota_delta=1,
            max_concurrent=1,
        )
        out_queue.put({"owner": owner, "ok": True})
    except Exception as exc:
        out_queue.put({"owner": owner, "error": str(exc)})
