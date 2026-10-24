#!/usr/bin/env python3
"""Deterministic flood harness for F1 acceptance (bounded chunks, process group)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

CHUNK = b"x" * 4096
CHUNKS = 200


def _write_markers(work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "flood.started").write_text("1\n", encoding="utf-8")
    pid_file = work_dir / "flood.pid"
    with pid_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{os.getpid()}\n")


def main() -> int:
    work_dir = Path(os.environ.get("F1_FLOOD_WORK_DIR", "."))
    _write_markers(work_dir)
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        _write_markers(work_dir)
        for _ in range(CHUNKS):
            sys.stdout.write(CHUNK.decode("ascii"))
            sys.stdout.flush()
            time.sleep(0.001)
        time.sleep(30)
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "grandchild":
        _write_markers(work_dir)
        time.sleep(30)
        return 0
    pid = os.fork()
    if pid == 0:
        gpid = os.fork()
        if gpid == 0:
            os.execv(sys.executable, [sys.executable, __file__, "grandchild"])
        os._exit(0)
    os.waitpid(pid, 0)
    os.execv(sys.executable, [sys.executable, __file__, "child"])


if __name__ == "__main__":
    raise SystemExit(main())
