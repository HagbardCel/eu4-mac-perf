#!/usr/bin/env python3
"""One snapshotted EU4 draw into an offscreen Metal texture. The GL draw still runs."""

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


SOURCE = Path(__file__).resolve().with_suffix(".m")
LIBRARY = SOURCE.parent / ".build/libeu4_metal_draw_x86_64.dylib"
VERT = SOURCE.parents[1] / "analysis/metal-stage-d/v1/color.vert.metal"
FRAG = SOURCE.parents[1] / "analysis/metal-stage-d/v1/color.frag.metal"


def build_library() -> Path:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-dynamiclib",
        "-framework", "Foundation", "-framework", "Metal", "-framework", "OpenGL",
        "-o", str(LIBRARY), str(SOURCE),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(result.stderr.strip())
    return LIBRARY


def launch_session(run_dir: Path, wait_seconds: float = 300) -> dict:
    identity = base.gog_identity()
    base.running_game_pid("idle")
    library = build_library()
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    run_dir.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env["EU4_METAL_DRAW_DIR"] = str(run_dir)
    env["EU4_METAL_DRAW_VERT"] = str(VERT)
    env["EU4_METAL_DRAW_FRAG"] = str(FRAG)
    env["DYLD_INSERT_LIBRARIES"] = str(library)
    stdout = (run_dir / "game.stdout").open("wb")
    stderr = (run_dir / "game.stderr").open("wb")
    game = subprocess.Popen(
        ["./eu4", *launcher["exeArgs"]],
        cwd=base.GOG_EXE.parent,
        env=env,
        stdout=stdout,
        stderr=stderr,
    )
    result_path = run_dir / "result.json"
    flushes_path = run_dir / "gl_flushes"
    deadline = time.monotonic() + wait_seconds
    result = None
    metadata: dict = {}
    try:
        while time.monotonic() < deadline and game.poll() is None:
            if result_path.is_file():
                try:
                    result = json.loads(result_path.read_text())
                    break
                except json.JSONDecodeError:
                    pass
            time.sleep(0.5)
        flushes = int(flushes_path.read_text().strip()) if flushes_path.is_file() else 0
        if result is None:
            result = {
                "nonzero_pixels": 0,
                "cause": "no closed color or DebugLines draw was recorded before the load window closed",
                "gl_draw_not_skipped": True,
            }
            result_path.write_text(json.dumps(result, indent=2) + "\n")
        metadata = {
            "status": "complete",
            "gog": identity,
            "library_sha256": base.sha256(library),
            "game_pid": game.pid,
            "result": result,
            "gl_flushes": flushes,
            "gl_draw_still_called": True,
        }
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
        metadata["game_returncode"] = game.returncode
        (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main() -> int:
    run_dir = base.ROOT / "results" / (
        dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-metal-draw"
    )
    try:
        metadata = launch_session(run_dir)
    except (base.BenchmarkError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"run_dir": str(run_dir), "result": metadata["result"], "gl_flushes": metadata["gl_flushes"]}, indent=2))
    result = metadata["result"]
    return 0 if result.get("nonzero_pixels", 0) > 0 and not result.get("cause") else 1


if __name__ == "__main__":
    raise SystemExit(main())
