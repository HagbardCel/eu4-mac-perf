"""Mock sealed PR D sessions for E1 acceptance (no live.report on the tree)."""
from __future__ import annotations

import json
import sys
import tempfile
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmark"
sys.path.insert(0, str(BENCH))

import eu4_benchmark as base  # noqa: E402
import eu4_gfx_live_capture as live  # noqa: E402
from oracle.function import fixture, synthetic  # noqa: E402

SESSION = "gfx-e1-acceptance"
ACCEPTANCE = 50


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _transcript(thread: int, *, synthetic_flag: int) -> str:
    text = synthetic.capture_text(fixture.load_descriptor(), "s03")
    text = text.replace(
        "capture=re0-pilot scenario=s03:length-from-buffer epoch=synthetic-a2",
        f"capture={SESSION} scenario=live:length-from-buffer epoch=p5a-live",
    )
    text = text.replace("thread=0 sequence=3", f"thread={thread} sequence=1")
    text = text.replace("synthetic=1", f"synthetic={synthetic_flag}")
    return text


def build_mock_live_session(
        *,
        file_count: int = ACCEPTANCE,
        synthetic_flag: int = 0,
        omit_capture: int | None = None,
) -> Path:
    """Structurally valid mock live session with a pre-existing manifest."""
    root = Path(tempfile.mkdtemp(prefix="e1-accept-"))
    descriptor = fixture.load_descriptor()
    descriptor_sha = base.sha256(
        ROOT / "oracle/function/descriptors/gfx_set_constant_buffer.json")
    binary_sha = descriptor["binary"]["sha256"]
    library_sha = base.sha256(live.LIBRARY) if live.LIBRARY.is_file() else None

    captures = root / "captures"
    captures.mkdir()
    names: list[str] = []
    for thread in range(file_count):
        if omit_capture is not None and thread == omit_capture:
            continue
        name = f"{SESSION}.{thread}.capture"
        (captures / name).write_text(
            _transcript(thread, synthetic_flag=synthetic_flag), encoding="utf-8")
        names.append(name)

    def table(header: str, values: dict[str, str]) -> str:
        return "\n".join([header, *(f"{k}={v}" for k, v in values.items()),
                          "END"]) + "\n"

    summary = {
        "session": SESSION,
        "pid": "123",
        "state": "sealed",
        "closed_reason": "operator",
        "quiescence": "ok",
        "started_ns": "1",
        "armed_ns": "2",
        "closed_ns": "3",
        "quota": "64",
        "threads": str(max(file_count, 1)),
        "intercepted": str(file_count),
        "not_admitted": "0",
        "not_admitted_busy": "0",
        "not_admitted_slot": "0",
        "not_admitted_pointer": "0",
        "not_admitted_address": "0",
        "not_admitted_quota": "0",
        "not_admitted_closed": "0",
        "not_admitted_thread_limit": "0",
        "admitted": str(file_count),
        "incomplete": "0",
        "completed": str(file_count),
        "written": str(len(names)),
        "write_failed": "0",
        "undrained": "0",
        "reserved": "0",
        "ready": "0",
    }
    startup = {
        "state": "installed",
        "session": SESSION,
        "pid": "123",
        "executable": str(base.GOG_EXE),
        "arch": "x86_64",
        "image_base": hex(0x100000000),
        "site": hex(0x100000000 + live.TARGET_RVA),
        "module": binary_sha,
        "error": "",
    }
    metadata = {
        "status": "captured",
        "session_id": SESSION,
        "game_pid": 123,
        "capture_files": len(names),
        "save_sha256": "a" * 64,
        "save_after_sha256": "a" * 64,
        "descriptor_sha256": descriptor_sha,
        "library_sha256": library_sha,
    }
    (root / "summary.native").write_text(
        table(live.SUMMARY_HEADER, summary), encoding="utf-8")
    (root / "startup.status").write_text(
        table(live.STARTUP_HEADER, startup), encoding="utf-8")
    (root / "session.done").write_text(
        table(live.DONE_HEADER, {"session": SESSION, "state": "sealed"}),
        encoding="utf-8")
    (root / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    file_hashes: dict[str, str] = {}
    for rel in ("summary.native", "startup.status", "session.done", "metadata.json"):
        file_hashes[rel] = _file_hash(root / rel)
    for name in names:
        file_hashes[f"captures/{name}"] = _file_hash(captures / name)

    manifest = {
        "schema": "eu4-gfx-live-manifest/v1",
        "session": SESSION,
        "status": "accepted" if len(names) >= ACCEPTANCE else "insufficient",
        "files": file_hashes,
        "descriptor_sha256": descriptor_sha,
        "library_sha256": library_sha,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return root
