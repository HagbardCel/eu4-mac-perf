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


def make_sequence_case(root: Path, *, case_id: str = "sequence",
                       session_id: str = "sequence-session",
                       capture_frame_id: int = 2) -> Path:
    """A verified incomplete v2 sequence package with a target frame seal.

    Diagnostic-only sequence summaries are a supported portable shape; the
    target seal still lives under ``frames/<index>/frame-seal.json``.
    """
    root.mkdir(parents=True)
    frames = root / "frames"
    for index in (1, 2):
        directory = frames / f"{index:06d}"
        directory.mkdir(parents=True)
        (directory / "frame-seal.json").write_text(json.dumps({
            "schema": "gfx-frame-seal/v1", "sealed": True, "complete": True,
            "session_id": session_id,
            "capture_frame_id": index,
            "event_stream_sha256": sha(f"events-{index}".encode()),
            "reference_sha256": sha(f"reference-{index}".encode()),
        }))
    (root / "sequence.json").write_text(json.dumps({
        "schema": "gfx-frame-sequence/v1",
        "session_id": session_id,
        "target_index": 2,
        "frames": [
            {"index": 1, "dir": "frames/000001", "capture_frame_id": 1},
            {"index": 2, "dir": "frames/000002",
             "capture_frame_id": capture_frame_id},
        ],
    }))
    (root / "reproduction-incomplete.json").write_text(json.dumps({
        "schema": "gfx-reproduction-incomplete/v1", "reason": "bounded"}))
    (root / "diagnostic-report.json").write_text(json.dumps({
        "schema": "gfx-frame-diagnosis/v1", "classification": "incomplete"}))
    (root / "dependency-graph.json").write_text(json.dumps({"status": "partial"}))
    (root / "graph.json").write_text(json.dumps({"status": "partial"}))
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "case.json":
            continue
        payload = path.read_bytes()
        files.append({
            "path": path.relative_to(root).as_posix(),
            "bytes": len(payload),
            "sha256": sha(payload),
        })
    (root / "case.json").write_text(json.dumps({
        "schema": gfx_replay_case.SEQUENCE_SCHEMA,
        "case_id": case_id,
        "files": files,
        "reproduction": {"status": "incomplete", "reason": "bounded"},
    }))
    return root
