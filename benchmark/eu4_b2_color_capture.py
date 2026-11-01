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
import tempfile
import time
from pathlib import Path

import eu4_benchmark as base


SOURCE = base.ROOT / "analysis/metal-draw/b2/color_capture.m"
DRIVER_SOURCE = base.ROOT / "tests/b2_color_driver.m"
LIBRARY = base.ROOT / "benchmark/.build/libeu4_b2_color_capture_x86_64.dylib"
DRIVER = base.ROOT / "benchmark/.build/b2_color_driver"
VERT = base.ROOT / "analysis/metal-stage-d/v1/color.vert"
FRAG = base.ROOT / "analysis/metal-stage-d/v1/color.frag"
PIN = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
HISTORICAL = "ea46e32:results/20261031T144434Z-metal-draw/matched.glsl"
MATCHER = "benchmark/.build/b2_color_driver"


def authorized(flag: bool) -> bool:
    return flag and os.environ.get("EU4_B2_COLOR_AUTHORIZE") == "yes"


def new_wait() -> dict:
    return {"phase": "menu", "fixture_phase": None, "ack": None}


def see_fixture(state: dict, present: bool) -> bool:
    if present and state["fixture_phase"] is None:
        state["fixture_phase"] = state["phase"]
    return state["fixture_phase"] is not None and state["ack"] == state["fixture_phase"]


def take_ack(state: dict, line: str) -> bool:
    if line == "menu-options" and state["phase"] == "menu":
        if state["fixture_phase"] == "menu":
            state["ack"] = "menu"
        else:
            state["phase"] = "ingame"
            state["ack"] = None
    elif line == "ingame-esc" and state["phase"] == "ingame":
        state["ack"] = "ingame"
    return state["fixture_phase"] is not None and state["ack"] == state["fixture_phase"]


def wait_status(state: dict) -> str:
    if state["fixture_phase"] is None:
        return "acquisition-failed"
    if state["ack"] == state["fixture_phase"]:
        return "acquired"
    return "incomplete"


def pull_line(stream, timeout: float):
    ready, _, _ = select.select([stream], [], [], timeout)
    if not ready:
        return None
    line = stream.readline()
    if line == "":
        return ""
    return line.strip()


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


def build_driver() -> Path:
    DRIVER.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "clang", "-arch", "x86_64", "-Wall", "-Wextra", "-framework", "OpenGL",
        "-o", str(DRIVER), str(DRIVER_SOURCE),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(result.stderr.strip() or "could not build the Color matcher")
    return DRIVER


def historical_vertex_matches(library: Path, source: Path) -> bool:
    env = os.environ.copy()
    env["DYLD_INSERT_LIBRARIES"] = str(library)
    result = subprocess.run(
        [str(build_driver()), "match-vertex", str(VERT), str(source)],
        capture_output=True, text=True, env=env, check=False,
    )
    return result.returncode == 0


def require_historical_vertex(library: Path) -> dict:
    present = subprocess.run(["git", "cat-file", "-e", HISTORICAL], cwd=base.ROOT, check=False)
    if present.returncode != 0:
        raise base.BenchmarkError(f"historical Color vertex is not in this checkout: {HISTORICAL}")
    blob = subprocess.check_output(["git", "show", HISTORICAL], cwd=base.ROOT)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "matched.glsl"
        path.write_bytes(blob)
        if not historical_vertex_matches(library, path):
            raise base.BenchmarkError(f"historical Color vertex does not match {VERT.name}")
    return {
        "historical_vertex": HISTORICAL,
        "historical_vertex_match": True,
        "matcher": MATCHER,
    }


def launch_session(run_dir: Path, wait_seconds: float) -> dict:
    identity = base.gog_identity()
    if identity["executable_sha256"] != PIN:
        raise base.BenchmarkError(f"GOG executable hash is {identity['executable_sha256']}, not the pinned build")
    base.running_game_pid("idle")
    library = build_library()
    preflight = require_historical_vertex(library)
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
    state = new_wait()
    acks: list[str] = []
    deadline = time.monotonic() + wait_seconds
    try:
        while time.monotonic() < deadline:
            if see_fixture(state, fixture.is_file()):
                break
            if game.poll() is not None and state["fixture_phase"] is None:
                break
            line = pull_line(sys.stdin, 0.5)
            if line == "":
                break
            if line not in ("menu-options", "ingame-esc"):
                continue
            acks.append(line)
            see_fixture(state, fixture.is_file())
            if take_ack(state, line):
                break
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
        "status": wait_status(state),
        "gog": identity,
        "library_sha256": base.sha256(library),
        "preflight": preflight,
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
