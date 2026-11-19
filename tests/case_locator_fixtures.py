"""Shared portable-case input for the case-locator tests.

Not discovered by ``unittest discover`` (no ``test_`` prefix).  Both test
modules import it by adding this directory to ``sys.path`` so the helper works
under focused module selection and full discovery alike.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from automation import gfx_replay_case


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def make_case(root: Path, *, case_id: str = "synthetic",
              session_id: str = "session-1",
              capture_frame_id: int = 7,
              report_status: str = "partial") -> Path:
    root.mkdir(parents=True)
    (root / "capture").mkdir()
    (root / "shaders").mkdir()
    (root / "capture" / "frame-trace.json").write_text(
        json.dumps({"schema": "gfx-frame-trace/v1", "complete": True}))
    (root / "capture" / "frame-events.jsonl").write_text("")
    (root / "capture" / "frame-seal.json").write_text(json.dumps({
        "schema": "gfx-frame-seal/v1", "sealed": True, "complete": True,
        "session_id": session_id,
        "capture_frame_id": capture_frame_id,
        "event_stream_sha256": sha(b"events"),
        "reference_sha256": sha(b"reference"),
    }))
    (root / "shaders" / "shaders.json").write_text("{}")
    (root / "report.json").write_text(json.dumps({"status": report_status}))
    (root / "graph.json").write_text(json.dumps({"status": "partial"}))
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        files.append({
            "path": path.relative_to(root).as_posix(),
            "bytes": len(payload),
            "sha256": sha(payload),
        })
    (root / "case.json").write_text(json.dumps({
        "schema": gfx_replay_case.SCHEMA,
        "case_id": case_id,
        "files": files,
        "replay_status": "partial",
    }))
    return root
