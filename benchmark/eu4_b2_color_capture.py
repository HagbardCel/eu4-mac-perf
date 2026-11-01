#!/usr/bin/env python3
"""One authorized EU4 process that freezes a single Color draw. It does not replay it."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import eu4_benchmark as base


SOURCE = base.ROOT / "analysis/metal-draw/b2/color_capture.m"
LIBRARY = base.ROOT / "benchmark/.build/libeu4_b2_color_capture_x86_64.dylib"
VERT = base.ROOT / "analysis/metal-stage-d/v1/color.vert"
FRAG = base.ROOT / "analysis/metal-stage-d/v1/color.frag"
PIN = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
ACKS = {"menu-options", "ingame-esc"}


def authorized(flag: bool) -> bool:
    return flag and os.environ.get("EU4_B2_COLOR_AUTHORIZE") == "yes"


def build_library() -> Path:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-dynamiclib",
        "-framework", "OpenGL", "-o", str(LIBRARY), str(SOURCE),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(result.stderr.strip())
    return LIBRARY


def launch_session(run_dir: Path, wait_seconds: float) -> dict:
    identity = base.gog_identity()
    if identity["executable_sha256"] != PIN:
        raise base.BenchmarkError(f"GOG executable hash is {identity['executable_sha256']}, not the pinned build")
    base.running_game_pid("idle")
    library = build_library()
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    run_dir.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env["EU4_B2_COLOR_DIR"] = str(run_dir)
    env["EU4_B2_COLOR_VERT"] = str(VERT)
    env["EU4_B2_COLOR_FRAG"] = str(FRAG)
    env["EU4_B2_COLOR_EXE_SHA256"] = PIN
    env["DYLD_INSERT_LIBRARIES"] = str(library)
    print(
        "On the main menu, open Options and close it, then type menu-options. "
        "If no fixture appears, start one single-player game, open the Esc menu, close it, "
        "and type ingame-esc.",
        flush=True,
    )
    stdout = (run_dir / "game.stdout").open("wb")
    stderr = (run_dir / "game.stderr").open("wb")
    game = subprocess.Popen(
        ["./eu4", *launcher["exeArgs"]],
        cwd=base.GOG_EXE.parent,
        env=env,
        stdout=stdout,
        stderr=stderr,
    )
    fixture = run_dir / "fixture" / "fixture.json"
    acks: list[str] = []
    deadline = time.monotonic() + wait_seconds
    try:
        while time.monotonic() < deadline and game.poll() is None and not fixture.is_file():
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if not ready:
                continue
            line = sys.stdin.readline().strip()
            if line in ACKS:
                acks.append(line)
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
    counters_path = run_dir / "counters.json"
    counters = json.loads(counters_path.read_text()) if counters_path.is_file() else {}
    metadata = {
        "status": "acquired" if fixture.is_file() else "acquisition-failed",
        "gog": identity,
        "library_sha256": base.sha256(library),
        "acknowledged_steps": acks,
        "counters": counters,
        "fixture": str(fixture) if fixture.is_file() else None,
        "game_returncode": game.returncode,
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorize-live", action="store_true")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    if not authorized(args.authorize_live):
        print(
            "refusing: live capture needs --authorize-live and EU4_B2_COLOR_AUTHORIZE=yes",
            file=sys.stderr,
        )
        return 2
    run_dir = base.ROOT / "fixtures" / "b2-color" / (
        dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    try:
        metadata = launch_session(run_dir, args.timeout)
    except (base.BenchmarkError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "run_dir": str(run_dir),
        "status": metadata["status"],
        "acknowledged_steps": metadata["acknowledged_steps"],
        "counters": metadata["counters"],
    }, indent=2))
    return 0 if metadata["status"] == "acquired" else 1


if __name__ == "__main__":
    raise SystemExit(main())
