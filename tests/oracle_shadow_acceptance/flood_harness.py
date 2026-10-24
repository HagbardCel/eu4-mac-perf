#!/usr/bin/env python3
"""Deterministic flood harness for F1 acceptance (bounded chunks, process group)."""
from __future__ import annotations

import os
import sys
import time

CHUNK = b"x" * 4096
CHUNKS = 200


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        for _ in range(CHUNKS):
            sys.stdout.write(CHUNK.decode("ascii"))
            sys.stdout.flush()
            time.sleep(0.001)
        time.sleep(30)
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "grandchild":
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
