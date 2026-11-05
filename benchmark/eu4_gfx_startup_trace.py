#!/usr/bin/env python3
"""Passive gfx-startup/v0 observer for the pinned GOG EU4 binary."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_benchmark as base  # noqa: E402
from reverse_engineering.macho_rva import (  # noqa: E402
    MachOMapper,
    assert_pinned_executable,
)
from reverse_engineering import paths  # noqa: E402

BENCH = ROOT / "benchmark"
BUILD = BENCH / ".build"
LIBRARY = BUILD / "eu4_gfx_startup_trace.dylib"
HOST = BUILD / "gfx_startup_trace_host"

SETTINGS_RVA = 0x15ED388
INIT_RVA = 0x15E8EA0
PATCH_BYTES = bytes.fromhex("554889e5415741564155415453")
PATCH_LENGTHS = (1, 3, 2, 2, 2, 2, 1)


class StartupError(RuntimeError):
    pass


def build_library() -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    command = [
        "clang",
        "-arch",
        "x86_64",
        "-O2",
        "-Wall",
        "-Wextra",
        "-dynamiclib",
        "-I",
        str(BENCH),
        "-o",
        str(LIBRARY),
        str(BENCH / "eu4_gfx_startup_trace.c"),
        str(BENCH / "eu4_gfx_startup_trace_detour.c"),
        str(BENCH / "eu4_gfx_startup_trace_gateway.S"),
        str(BENCH / "eu4_gfx_startup_trace_dylib.c"),
    ]
    built = subprocess.run(command, capture_output=True, text=True, check=False)
    if built.returncode:
        raise StartupError(built.stderr.strip() or "startup dylib failed to build")
    return LIBRARY


def build_host() -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    command = [
        "clang",
        "-arch",
        "x86_64",
        "-O2",
        "-Wall",
        "-Wextra",
        "-I",
        str(BENCH),
        "-o",
        str(HOST),
        str(BENCH / "eu4_gfx_startup_trace.c"),
        str(BENCH / "eu4_gfx_startup_trace_detour.c"),
        str(BENCH / "eu4_gfx_startup_trace_gateway.S"),
        str(ROOT / "tests" / "gfx_startup_trace_stubs.S"),
        str(ROOT / "tests" / "gfx_startup_trace_host.c"),
    ]
    built = subprocess.run(command, capture_output=True, text=True, check=False)
    if built.returncode:
        raise StartupError(built.stderr.strip() or "startup host failed to build")
    return HOST


def _instruction_cover(window: bytes) -> None:
    if sum(PATCH_LENGTHS) != len(PATCH_BYTES):
        raise StartupError("internal length table mismatch")
    if window != PATCH_BYTES:
        raise StartupError(f"entry bytes mismatch: {window.hex()}")
    # All instructions in this window are register pushes / frame setup: no RIP-relative.


def preflight() -> dict:
    build_library()
    identity = base.gog_identity()
    if base.command("lipo", "-archs", str(base.GOG_EXE)).strip() != "x86_64":
        raise StartupError("The installed GOG build is not x86_64")
    assert_pinned_executable(base.GOG_EXE)
    try:
        base.running_game_pid("idle")
    except base.BenchmarkError as exc:
        raise StartupError("Close EU IV before the startup-trace preflight") from exc
    target = paths.load_target()
    image_base = target["image_base"]
    if isinstance(image_base, str):
        image_base = int(image_base, 16)
    with MachOMapper(base.GOG_EXE) as mapper:
        settings = mapper.read_bytes_at_rva(image_base, SETTINGS_RVA, 13)
        init = mapper.read_bytes_at_rva(image_base, INIT_RVA, 13)
    _instruction_cover(settings)
    _instruction_cover(init)
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    if launcher.get("exePath") != "./eu4.app/Contents/MacOS/eu4" or not isinstance(
        launcher.get("exeArgs"), list
    ) or not all(isinstance(item, str) for item in launcher["exeArgs"]):
        raise StartupError("Unexpected GOG launcher executable or arguments")
    return {
        "executable_sha256": identity["executable_sha256"],
        "settings_patch": settings.hex(),
        "init_patch": init.hex(),
        "library_sha256": base.sha256(LIBRARY),
    }


def _parse_status(path: Path) -> dict:
    text = path.read_text()
    values = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def run(*, authorize_live: bool, wait_seconds: float = 180.0) -> dict:
    if not authorize_live:
        raise StartupError("Live launch requires the explicit --authorize-live flag")
    evidence = preflight()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (ROOT / "results" / f"{stamp}-gfx-startup").resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    session = str(uuid.uuid4())
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    env = os.environ.copy()
    env["DYLD_INSERT_LIBRARIES"] = str(LIBRARY)
    env["EU4_GFX_STARTUP_DIR"] = str(run_dir)
    env["EU4_GFX_STARTUP_ID"] = session
    env["EU4_GFX_EXPECTED_EXE"] = str(base.GOG_EXE)
    env["EU4_GFX_EXECUTABLE_SHA256"] = evidence["executable_sha256"]
    env["EU4_GFX_STARTUP_DEADLINE_S"] = str(int(wait_seconds))
    env.pop("EU4_METAL_BOOT_MODE", None)
    env.pop("EU4_METAL_INIT_DIR", None)
    stdout = (run_dir / "game.stdout").open("wb")
    stderr = (run_dir / "game.stderr").open("wb")
    game = subprocess.Popen(
        ["./eu4", *launcher["exeArgs"]],
        cwd=base.GOG_EXE.parent,
        env=env,
        stdout=stdout,
        stderr=stderr,
        stdin=subprocess.DEVNULL,
    )
    status_path = run_dir / "startup.status"
    deadline = time.monotonic() + wait_seconds
    status = None
    try:
        while time.monotonic() < deadline:
            if status_path.is_file():
                status = _parse_status(status_path)
                if status.get("state") == "installed":
                    if int(status.get("pid", "-1")) != game.pid:
                        raise StartupError("startup.status pid does not match launched process")
                    break
                if status.get("state") == "error":
                    raise StartupError(f"hook install failed: {status.get('error')}")
            if game.poll() is not None:
                break
            time.sleep(0.2)
        if status is None or status.get("state") != "installed":
            raise StartupError("The startup dylib did not write an installed attestation")
        summary_path = run_dir / "summary.json"
        while time.monotonic() < deadline:
            if summary_path.is_file():
                break
            if game.poll() is not None and summary_path.is_file():
                break
            time.sleep(0.5)
        if game.poll() is None:
            game.terminate()
            try:
                game.wait(timeout=15)
            except subprocess.TimeoutExpired:
                game.kill()
                game.wait(timeout=10)
        # Allow destructor/finalizer to finish.
        time.sleep(1.0)
    finally:
        stdout.close()
        stderr.close()
        if game.poll() is None:
            game.kill()
            game.wait(timeout=10)

    summary = {}
    if (run_dir / "summary.json").is_file():
        summary = json.loads((run_dir / "summary.json").read_text())
    report = {
        "run_dir": str(run_dir),
        "session": session,
        "pid": game.pid,
        "evidence": evidence,
        "status": status,
        "summary": summary,
        "accepted": bool(summary.get("complete")),
    }
    (run_dir / "launcher-report.json").write_text(json.dumps(report, indent=2) + "\n")
    if not report["accepted"]:
        raise StartupError(f"startup capture incomplete: {summary}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    sub.add_parser("build")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--authorize-live", action="store_true")
    run_parser.add_argument("--wait-seconds", type=float, default=180.0)
    args = parser.parse_args()
    if args.command == "build":
        print(build_library())
        print(build_host())
        return 0
    if args.command == "preflight":
        print(json.dumps(preflight(), indent=2))
        return 0
    report = run(authorize_live=args.authorize_live, wait_seconds=args.wait_seconds)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except StartupError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
