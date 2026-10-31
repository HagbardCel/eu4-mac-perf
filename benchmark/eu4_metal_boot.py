#!/usr/bin/env python3
"""Metal window boot. One callsite redirect, then the process exits. No CI launch."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import time
from pathlib import Path

import eu4_benchmark as base


SOURCE = Path(__file__).resolve().with_suffix(".m")
PATCH = SOURCE.with_name("eu4_metal_boot_patch.c")
LIBRARY = SOURCE.parent / ".build/libeu4_metal_boot.dylib"
HOST_SOURCE = SOURCE.parents[1] / "tests/metal_boot_host.c"
HOST = SOURCE.parent / ".build/metal_boot_host"


def build_library(arch: str = "x86_64") -> Path:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    output = LIBRARY if arch == "x86_64" else LIBRARY.with_name(f"libeu4_metal_boot_{arch}.dylib")
    command = [
        "clang", "-arch", arch, "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-dynamiclib",
        "-framework", "Foundation", "-framework", "Metal", "-framework", "QuartzCore",
        "-o", str(output), str(SOURCE), str(PATCH),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(result.stderr.strip() or "Metal boot probe failed to build")
    if base.command("lipo", "-archs", str(output)).strip() != arch:
        raise base.BenchmarkError(f"Metal boot probe is not {arch}")
    return output


def build_host(arch: str = "x86_64") -> Path:
    HOST.parent.mkdir(parents=True, exist_ok=True)
    output = HOST if arch == "x86_64" else HOST.with_name(f"metal_boot_host_{arch}")
    command = [
        "clang", "-arch", arch, "-O2", "-Wall", "-Wextra",
        "-o", str(output), str(HOST_SOURCE), str(PATCH),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(result.stderr.strip() or "Metal boot host failed to build")
    return output


def run_host(arch: str = "x86_64") -> None:
    host = build_host(arch)
    result = subprocess.run([str(host)], capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(result.stderr.strip() or result.stdout.strip() or "Metal boot host failed")


def launch_session(run_dir: Path, wait_seconds: float = 180) -> dict:
    """Operator launch. The dylib exits the game after the receipt. Not a CI job."""
    identity = base.gog_identity()
    if base.command("lipo", "-archs", str(base.GOG_EXE)).strip() != "x86_64":
        raise base.BenchmarkError("This probe is for the installed x86_64 GOG EU IV build only")
    base.running_game_pid("idle")
    library = build_library("x86_64")
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    run_dir.mkdir(parents=True, exist_ok=False)
    receipt_path = run_dir / "receipt.json"
    env = os.environ.copy()
    env["EU4_METAL_BOOT_RECEIPT"] = str(receipt_path)
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
    deadline = time.monotonic() + wait_seconds
    receipt = None
    try:
        while time.monotonic() < deadline:
            if receipt_path.is_file() and receipt_path.stat().st_size > 0:
                try:
                    receipt = json.loads(receipt_path.read_text())
                    break
                except json.JSONDecodeError:
                    pass
            if game.poll() is not None and receipt_path.is_file():
                receipt = json.loads(receipt_path.read_text())
                break
            time.sleep(0.2)
    finally:
        if game.poll() is None:
            game.terminate()
            try:
                game.wait(timeout=10)
            except subprocess.TimeoutExpired:
                game.kill()
        stdout.close()
        stderr.close()
    if receipt is None:
        raise base.BenchmarkError("EU4 did not write the Metal boot receipt")
    metadata = {
        "status": "complete",
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "gog": identity,
        "exit_code": game.returncode,
        "result": receipt.get("result"),
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return receipt
