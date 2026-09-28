#!/usr/bin/env python3
"""Count paused GOG EU IV OpenGL swaps, optionally testing multithreading."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import eu4_benchmark as base


SOURCE = Path(__file__).resolve().with_suffix(".c")
LIBRARY = SOURCE.parent / ".build/libeu4_frame_counter.dylib"
PAUSED_BASELINE_SWAPS_PER_SECOND = 87.38906306014651


def build_library() -> None:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    command = ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra",
               "-dynamiclib", "-framework", "OpenGL", "-o", str(LIBRARY), str(SOURCE)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(f"Could not build x86_64 frame counter: {result.stderr.strip()}")
    if base.command("lipo", "-archs", str(LIBRARY)).strip() != "x86_64":
        raise base.BenchmarkError("Frame counter is not x86_64")


def new_rows(path: Path, offset: int) -> list[dict]:
    with path.open("rb") as source:
        source.seek(offset)
        data = source.read().decode("ascii", errors="replace")
    rows = []
    for line in data.splitlines()[1:]:  # first bucket may overlap the cue
        parts = line.split(",")
        if len(parts) != 2:
            continue
        try:
            interval_ns, calls = map(int, parts)
        except ValueError:
            continue
        if 0 < interval_ns < 2_000_000_000 and calls > 0:
            rows.append({"interval_s": interval_ns / 1e9, "swap_calls": calls,
                         "swaps_per_second": calls * 1e9 / interval_ns})
    return rows


def multithreaded_status(path: Path) -> list[dict]:
    if not path.is_file():
        raise base.BenchmarkError("Multithreaded OpenGL status was not logged; no capture was made")
    with path.open(newline="") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise base.BenchmarkError("No OpenGL context reported its multithreading state")
    values = []
    for row in rows:
        try:
            values.append({key: int(row[key]) for key in
                           ("query_before", "before", "enable_error", "query_after", "after")})
        except (KeyError, ValueError) as exc:
            raise base.BenchmarkError("Malformed multithreaded OpenGL status log") from exc
    if any(row["query_before"] != 0 or row["enable_error"] != 0 or
           row["query_after"] != 0 or row["after"] != 1 for row in values):
        raise base.BenchmarkError("Apple's multithreaded OpenGL engine was not enabled on every observed context")
    if all(row["before"] == 1 for row in values):
        raise base.BenchmarkError("OpenGL multithreading was already enabled; no experiment is needed")
    return values


def wait_for_first_bucket(log: Path, game: subprocess.Popen, timeout: float = 5) -> None:
    """Allow the first full logging interval to finish after a quick confirmation."""
    deadline = time.monotonic() + timeout
    header_size = len("interval_ns,swap_calls\n")
    while game.poll() is None and time.monotonic() < deadline:
        if log.is_file() and log.stat().st_size > header_size:
            return
        time.sleep(0.2)
    raise base.BenchmarkError("The frame counter did not log swap calls; inspect game.stderr")


def measure(args: argparse.Namespace) -> None:
    identity = base.gog_identity()
    if base.command("lipo", "-archs", str(base.GOG_EXE)).strip() != "x86_64":
        raise base.BenchmarkError("This counter is for the installed x86_64 GOG EU IV build only")
    try:
        base.running_game_pid("idle")
    except base.BenchmarkError as exc:
        if "Idle control requires EU IV closed" in str(exc):
            raise base.BenchmarkError("Close EU IV before this one controlled relaunch") from exc
        raise
    save = base.DEFAULT_SAVE
    if not save.is_file():
        raise base.BenchmarkError(f"Disposable Venice save is missing: {save}")
    save_hash = base.sha256(save)
    build_library()
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    expected_path = "./eu4.app/Contents/MacOS/eu4"
    if launcher.get("exePath") != expected_path or not isinstance(launcher.get("exeArgs"), list) or not all(
        isinstance(item, str) for item in launcher["exeArgs"]
    ):
        raise base.BenchmarkError("Unexpected GOG launcher executable or arguments")

    now = dt.datetime.now(dt.timezone.utc)
    suffix = "paused-mp" if args.multithreaded_gl else "paused-interpose"
    run_dir = Path(args.output).expanduser().resolve() / f"{now:%Y%m%dT%H%M%SZ}-{suffix}"
    run_dir.mkdir(parents=True, exist_ok=False)
    log = run_dir / "frame-swaps.csv"
    status_log = run_dir / "multithreaded-gl.csv"
    metadata = {"status": "launching", "started_utc": now.isoformat(),
                "gog": identity, "save_path": str(save), "save_sha256": save_hash,
                "counter_library": str(LIBRARY), "counter_source_sha256": base.sha256(SOURCE),
                "requested_seconds": args.seconds,
                "multithreaded_gl_requested": args.multithreaded_gl}
    metadata_path = run_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    try:
        env = os.environ.copy()
        env["EU4_FRAME_LOG"] = str(log)
        env["DYLD_INSERT_LIBRARIES"] = str(LIBRARY)
        if args.multithreaded_gl:
            env["EU4_GL_MULTITHREADED"] = "1"
            env["EU4_GL_STATUS_LOG"] = str(status_log)
        with (run_dir / "game.stdout").open("wb") as stdout, (run_dir / "game.stderr").open("wb") as stderr:
            game = subprocess.Popen(["./eu4", *launcher["exeArgs"]], cwd=base.GOG_EXE.parent,
                                    env=env, stdout=stdout, stderr=stderr)
        metadata["game_pid"] = game.pid
        print("Launched the installed GOG EU IV executable with a passive frame counter.")
        print("Load the disposable Venice save and pause. Return to Terminal when ready.")
        if base.prompt("Confirm the expected DLC/mods and Venice save are loaded (yes/no): ", True).lower() != "yes":
            raise base.BenchmarkError("Game state was not confirmed; no FPS capture was made")
        if game.poll() is not None or base.running_game_pid("paused") != game.pid:
            raise base.BenchmarkError("The expected GOG EU IV process is not running")
        wait_for_first_bucket(log, game)
        if args.multithreaded_gl:
            metadata["multithreaded_gl_contexts"] = multithreaded_status(status_log)
        base.prompt("Keep EU IV paused. Press Enter and switch back to the game: ")
        print(f"Capture begins after a {args.lead_in}s lead-in; keep EU IV foreground until the second sound.", flush=True)
        time.sleep(args.lead_in)
        if game.poll() is not None or base.running_game_pid("paused") != game.pid:
            raise base.BenchmarkError("GOG EU IV process changed before capture")
        display = base.display_mode()
        if not display.get("verified") or abs(display["refresh_hz"] - 120) > 1:
            raise base.BenchmarkError(f"Expected 120 Hz with EU IV foreground; observed {display}")
        metadata["display"] = display
        metadata["settings"] = base.game_settings()
        metadata["power"] = base.power_state()
        metadata["dlc_mods"] = base.mod_settings()
        if metadata["power"].get("mode") != "normal":
            raise base.BenchmarkError("Expected Normal power mode for the 120 Hz comparison")
        if (metadata["settings"].get("fullScreen") != "yes" or
                metadata["settings"].get("borderless") != "no" or
                metadata["settings"].get("game_resolution") != "3456x2234"):
            raise base.BenchmarkError("Expected the original 3456x2234 full-screen EU IV settings")
        offset = log.stat().st_size
        base.cue()
        time.sleep(args.seconds)
        base.cue()
        rows = new_rows(log, offset)
        if len(rows) < 3:
            raise base.BenchmarkError("Too few complete one-second swap buckets; FPS is unverified")
        if base.sha256(save) != save_hash:
            raise base.BenchmarkError("Disposable save changed during paused frame count")
        with (run_dir / "capture-swaps.csv").open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=("interval_s", "swap_calls", "swaps_per_second"))
            writer.writeheader()
            writer.writerows(rows)
        rates = [row["swaps_per_second"] for row in rows]
        metadata["status"] = "complete"
        metadata["full_buckets"] = len(rows)
        metadata["median_swaps_per_second"] = statistics.median(rates)
        metadata["min_swaps_per_second"] = min(rates)
        metadata["max_swaps_per_second"] = max(rates)
        print(f"Paused OpenGL swap calls: median {metadata['median_swaps_per_second']:.1f}/s "
              f"({len(rows)} complete one-second buckets)")
        if args.multithreaded_gl:
            metadata["multithreaded_gl_contexts"] = multithreaded_status(status_log)
            gain = 100 * (metadata["median_swaps_per_second"] / PAUSED_BASELINE_SWAPS_PER_SECOND - 1)
            print(f"Compared with the earlier 87.4/s direct-launch baseline: {gain:+.1f}% swap rate. "
                  "Power was not measured.")
        print(f"Saved log and metadata: {run_dir}")
        print("Quit this measured game session normally when finished; the counter was loaded only for this launch.")
    except (base.BenchmarkError, OSError, KeyboardInterrupt) as exc:
        metadata["status"] = "incomplete"
        metadata["error"] = str(exc)
        raise
    finally:
        metadata["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        metadata_path.write_text(json.dumps(metadata, indent=2, default=str) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--lead-in", type=int, default=5)
    parser.add_argument("--output", default=str(base.ROOT / "results"))
    parser.add_argument("--multithreaded-gl", action="store_true",
                        help="experimentally enable Apple's multithreaded OpenGL engine in this launch")
    args = parser.parse_args()
    if args.seconds < 5 or args.lead_in < 0:
        parser.error("seconds must be >= 5 and lead-in >= 0")
    try:
        measure(args)
    except (base.BenchmarkError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
