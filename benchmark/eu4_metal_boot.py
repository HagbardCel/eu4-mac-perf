#!/usr/bin/env python3
"""Metal window boot. One callsite redirect, then the process exits. No CI launch."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import eu4_benchmark as base


SOURCE = Path(__file__).resolve().with_suffix(".m")
PATCH = SOURCE.with_name("eu4_metal_boot_patch.c")
LIBRARY = SOURCE.parent / ".build/libeu4_metal_boot.dylib"
HOST_SOURCE = SOURCE.parents[1] / "tests/metal_boot_host.c"
HOST = SOURCE.parent / ".build/metal_boot_host"
PINNED_SHA256 = json.loads(
    (SOURCE.parents[1] / "reverse_engineering" / "target.json").read_text()
)["sha256"]
EXIT_WAIT_SECONDS = 5


def build_library(arch: str = "x86_64") -> Path:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    output = LIBRARY if arch == "x86_64" else LIBRARY.with_name(f"libeu4_metal_boot_{arch}.dylib")
    command = [
        "clang", "-arch", arch, "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-dynamiclib",
        "-framework", "AppKit", "-framework", "Foundation", "-framework", "Metal", "-framework", "QuartzCore",
        "-o", str(output), str(SOURCE), str(PATCH),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(result.stderr.strip() or "Metal boot probe failed to build")
    if base.command("lipo", "-archs", str(output)).strip() != arch:
        raise base.BenchmarkError(f"Metal boot probe is not {arch}")
    return output


def build_host(arch: str = "x86_64", fail_inject: bool = False) -> Path:
    HOST.parent.mkdir(parents=True, exist_ok=True)
    output = HOST if not fail_inject else HOST.with_name("metal_boot_host_fail")
    if arch != "x86_64":
        output = output.with_name(f"{output.name}_{arch}")
    command = [
        "clang", "-arch", arch, "-O2", "-Wall", "-Wextra",
        "-o", str(output), str(HOST_SOURCE), str(PATCH),
    ]
    if fail_inject:
        command.insert(1, "-DEU4_METAL_BOOT_TEST_FAIL")
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(result.stderr.strip() or "Metal boot host failed to build")
    return output


def run_host(arch: str = "x86_64") -> None:
    for fail_inject in (False, True):
        host = build_host(arch, fail_inject=fail_inject)
        result = subprocess.run([str(host)], capture_output=True, text=True, check=False)
        if result.returncode:
            label = "rollback host" if fail_inject else "Metal boot host"
            raise base.BenchmarkError(result.stderr.strip() or result.stdout.strip() or f"{label} failed")


def probe_environ(inherited: dict, bootstrap: bool) -> dict:
    """Select the mode here. An inherited bootstrap value does not stick."""
    env = dict(inherited)
    env["EU4_METAL_BOOT_MODE"] = "bootstrap" if bootstrap else "a1"
    return env


def read_receipt(path: Path) -> dict | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def stop_game(game: subprocess.Popen) -> None:
    if game.poll() is not None:
        return
    game.terminate()
    try:
        game.wait(timeout=10)
    except subprocess.TimeoutExpired:
        game.kill()
        game.wait(timeout=10)


def launch_session(run_dir: Path, wait_seconds: float = 180, bootstrap: bool = False) -> dict:
    """Operator launch. The dylib exits the game after the receipt. Not a CI job."""
    identity = base.gog_identity()
    if identity["executable_sha256"] != PINNED_SHA256:
        raise base.BenchmarkError(
            f"EU4 executable sha256 {identity['executable_sha256']} is not the pinned target"
        )
    if base.command("lipo", "-archs", str(base.GOG_EXE)).strip() != "x86_64":
        raise base.BenchmarkError("This probe is for the installed x86_64 GOG EU IV build only")
    base.running_game_pid("idle")
    library = build_library("x86_64")
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    receipt_path = run_dir / "receipt.json"
    env = probe_environ(os.environ, bootstrap)
    env["EU4_METAL_BOOT_RECEIPT"] = str(receipt_path)
    env["SDL_VIDEO_EXTERNAL_CONTEXT"] = "1"
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
            if game.poll() is not None:
                receipt = read_receipt(receipt_path)
                break
            receipt = read_receipt(receipt_path)
            if receipt is not None:
                try:
                    game.wait(timeout=EXIT_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    stop_game(game)
                break
            time.sleep(0.2)
    finally:
        stop_game(game)
        stdout.close()
        stderr.close()
    wanted = "backend_bootstrap_checkpoint_reached" if bootstrap else "metal_window_boot_verified"
    verified = receipt is not None and receipt.get("result") == wanted and game.returncode == 0
    metadata = {
        "status": "complete" if verified else "failed",
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "gog": identity,
        "exit_code": game.returncode,
        "result": None if receipt is None else receipt.get("result"),
        "probe_sha256": base.sha256(library),
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    if not verified:
        raise base.BenchmarkError(
            f"Metal boot was not verified: result={metadata['result']} exit={game.returncode}"
        )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operator EU4 Metal boot. Not a CI job.")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        launch_session(args.run_dir, bootstrap=args.bootstrap)
    except base.BenchmarkError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
