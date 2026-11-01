#!/usr/bin/env python3
"""One operator EU4 session. Builds the capture dylib, launches once, then stops."""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import eu4_benchmark as base


SOURCE = Path(__file__).resolve().parents[1] / "analysis/metal-draw/b2/capture.m"
LIBRARY = Path(__file__).resolve().parent / ".build/libeu4_b2_capture.dylib"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/b2"


def build_library() -> Path:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-dynamiclib",
        "-framework", "Foundation", "-framework", "OpenGL",
        "-o", str(LIBRARY), str(SOURCE),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(result.stderr.strip() or "capture dylib failed to build")
    return LIBRARY


def main() -> int:
    base.running_game_pid("idle")
    library = build_library()
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    FIXTURE.mkdir(parents=True, exist_ok=True)
    for child in FIXTURE.iterdir():
        if child.is_file():
            child.unlink()
    env = os.environ.copy()
    env["EU4_B2_CAPTURE_DIR"] = str(FIXTURE)
    env["DYLD_INSERT_LIBRARIES"] = str(library)
    stdout = (FIXTURE / "game.stdout").open("wb")
    stderr = (FIXTURE / "game.stderr").open("wb")
    game = subprocess.Popen(
        ["./eu4", *launcher["exeArgs"]],
        cwd=base.GOG_EXE.parent,
        env=env,
        stdout=stdout,
        stderr=stderr,
    )
    deadline = time.monotonic() + 300
    fixture = FIXTURE / "fixture.json"
    try:
        while time.monotonic() < deadline and game.poll() is None:
            if fixture.is_file():
                break
            time.sleep(0.5)
    finally:
        if game.poll() is None:
            game.send_signal(signal.SIGTERM)
            try:
                game.wait(timeout=10)
            except subprocess.TimeoutExpired:
                game.kill()
                game.wait(timeout=5)
        stdout.close()
        stderr.close()
    print(json.dumps({
        "fixture": fixture.is_file(),
        "returncode": game.returncode,
        "when": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2))
    return 0 if fixture.is_file() else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except base.BenchmarkError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
