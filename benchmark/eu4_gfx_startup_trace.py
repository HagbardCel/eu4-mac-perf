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
import eu4_gfx_observe_registry as observe_registry  # noqa: E402
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
        str(BENCH / "eu4_gfx_site_patch.c"),
        str(BENCH / "eu4_gfx_observe.c"),
        str(BENCH / "eu4_gfx_observe_buffers.c"),
        str(BENCH / "eu4_gfx_observe_draw.c"),
        str(BENCH / "eu4_gfx_observe_viewport.c"),
        str(BENCH / "eu4_gfx_observe_gateway.S"),
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
        str(BENCH / "eu4_gfx_site_patch.c"),
        str(BENCH / "eu4_gfx_observe.c"),
        str(BENCH / "eu4_gfx_observe_buffers.c"),
        str(BENCH / "eu4_gfx_observe_draw.c"),
        str(BENCH / "eu4_gfx_observe_viewport.c"),
        str(BENCH / "eu4_gfx_observe_gateway.S"),
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


def preflight_ops(
    targets: list[str],
    *,
    max_events: int = 256,
    duration_s: float = 10.0,
    until_target_draw: bool = False,
    target_caller_rva: int | None = None,
) -> dict:
    """Pin the executable and verify every selected observation prefix."""
    if not targets:
        raise StartupError("At least one --target is required")
    if len(targets) > 8:
        raise StartupError("At most eight observation targets are supported")
    if not 1 <= max_events <= 1024:
        raise StartupError("--max-events must be between 1 and 1024")
    if not 0.0 <= duration_s <= 600.0:
        raise StartupError("--duration must be between 0 and 600 seconds")
    if until_target_draw:
        if "_GfxDraw" not in targets:
            raise StartupError("--until-target-draw requires --target _GfxDraw")
        if target_caller_rva is None:
            raise StartupError("--until-target-draw requires --caller-rva")
        if not 0 < target_caller_rva <= 0xFFFFFFFF:
            raise StartupError("--caller-rva must be a non-zero 32-bit RVA")
    elif target_caller_rva is not None:
        raise StartupError("--caller-rva requires --until-target-draw")
    entries = observe_registry.load_entries()
    by_symbol = {entry.symbol: entry for entry in entries}
    selected = []
    seen = set()
    for target in targets:
        if target in seen:
            raise StartupError(f"Duplicate observation target: {target}")
        seen.add(target)
        entry = by_symbol.get(target)
        if entry is None:
            raise StartupError(f"Unknown observation target: {target}")
        if not observe_registry.installable(entry):
            raise StartupError(f"Observation target is not relocation-safe: {target}")
        selected.append(entry)

    evidence = preflight()
    target = paths.load_target()
    image_base = target["image_base"]
    if isinstance(image_base, str):
        image_base = int(image_base, 16)
    caller_site_hex = None
    with MachOMapper(base.GOG_EXE) as mapper:
        for entry in selected:
            window = mapper.read_bytes_at_rva(image_base, entry.rva, len(entry.prefix))
            if window != entry.prefix:
                raise StartupError(
                    f"Observation prefix mismatch for {entry.symbol}: {window.hex()}"
                )
        if target_caller_rva is not None:
            try:
                caller_site_hex = mapper.read_bytes_at_rva(
                    image_base, target_caller_rva, 8
                ).hex()
            except Exception as exc:  # noqa: BLE001
                raise StartupError(
                    f"--caller-rva is not file-backed in the pinned image: {exc}"
                ) from exc
    evidence["ops"] = [
        {
            "symbol": entry.symbol,
            "rva": f"0x{entry.rva:x}",
            "prefix": entry.prefix.hex(),
            "length": len(entry.prefix),
            "relocation": "safe",
        }
        for entry in selected
    ]
    evidence["max_events"] = max_events
    evidence["duration_ms"] = int(duration_s * 1000)
    evidence["until_target_draw"] = until_target_draw
    evidence["target_caller_rva"] = (
        f"0x{target_caller_rva:x}" if target_caller_rva is not None else None
    )
    evidence["caller_site_hex"] = caller_site_hex
    return evidence


def _wait_pair(run_dir: Path, game: subprocess.Popen, deadline: float) -> bool:
    summary = run_dir / "summary.json"
    operations = run_dir / "operations-summary.json"
    while time.monotonic() < deadline:
        if summary.is_file() and operations.is_file():
            return True
        if game.poll() is not None:
            time.sleep(0.3)
            return summary.is_file() and operations.is_file()
        time.sleep(0.2)
    return summary.is_file() and operations.is_file()


def _terminate_owned_process(game: subprocess.Popen) -> dict:
    info = {"pid": game.pid, "terminated": False, "killed": False}
    if game.poll() is not None:
        return info
    try:
        info["executable"] = base.process_executable_path(game.pid)
    except Exception:  # noqa: BLE001
        info["executable"] = None
    game.terminate()
    info["terminated"] = True
    try:
        game.wait(timeout=10)
    except subprocess.TimeoutExpired:
        game.kill()
        game.wait(timeout=10)
        info["killed"] = True
    return info


def run_ops(
    *,
    targets: list[str],
    max_events: int,
    duration_s: float,
    timeout_s: float,
    output: Path,
    authorize_live: bool,
    evidence: dict | None = None,
    until_target_draw: bool = False,
    target_caller_rva: int | None = None,
) -> dict:
    """Launch one bounded original-process observation session."""
    if not authorize_live:
        raise StartupError("Live capture requires --authorize-live or a campaign")
    if not 1 <= timeout_s <= 600:
        raise StartupError("--timeout must be between 1 and 600 seconds")
    if until_target_draw and target_caller_rva is None:
        raise StartupError("--until-target-draw requires --caller-rva")
    evidence = evidence or preflight_ops(
        targets,
        max_events=max_events,
        duration_s=duration_s,
        until_target_draw=until_target_draw,
        target_caller_rva=target_caller_rva,
    )
    run_dir = Path(output).expanduser().resolve()
    if run_dir.exists():
        raise StartupError(f"Capture output already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=False)
    session = str(uuid.uuid4())
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    env = os.environ.copy()
    env["DYLD_INSERT_LIBRARIES"] = str(LIBRARY)
    env["EU4_GFX_STARTUP_DIR"] = str(run_dir)
    env["EU4_GFX_STARTUP_ID"] = session
    env["EU4_GFX_EXPECTED_EXE"] = str(base.GOG_EXE)
    env["EU4_GFX_EXECUTABLE_SHA256"] = evidence["executable_sha256"]
    env["EU4_GFX_STARTUP_DEADLINE_S"] = str(int(timeout_s))
    env["EU4_GFX_OPS_TARGETS"] = ",".join(targets)
    env["EU4_GFX_OPS_MAX_EVENTS"] = str(max_events)
    env["EU4_GFX_OPS_DURATION_MS"] = str(int(duration_s * 1000))
    env["EU4_GFX_OPS_STOP_POLICY"] = (
        "target_draw" if until_target_draw else "quota"
    )
    env["EU4_GFX_OPS_RETENTION"] = "state" if until_target_draw else "legacy"
    if target_caller_rva is not None:
        env["EU4_GFX_OPS_TARGET_CALLER"] = f"0x{target_caller_rva:x}"
    env.pop("EU4_METAL_BOOT_MODE", None)
    env.pop("EU4_METAL_INIT_DIR", None)
    request = {
        "schema": "live-gfx-capture-request/v0",
        "session": session,
        "targets": evidence["ops"],
        "max_events": max_events,
        "duration_ms": int(duration_s * 1000),
        "stop_policy": "target_draw" if until_target_draw else "quota",
        "retention": "state" if until_target_draw else "legacy",
        "target_caller_rva": evidence.get("target_caller_rva"),
        "caller_site_hex": evidence.get("caller_site_hex"),
        "timeout_s": timeout_s,
        "executable_sha256": evidence["executable_sha256"],
        "library_sha256": evidence["library_sha256"],
    }
    (run_dir / "capture-request.json").write_text(
        json.dumps(request, indent=2) + "\n"
    )
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
    status = None
    deadline = time.monotonic() + timeout_s + 20.0
    try:
        while time.monotonic() < deadline:
            if status_path.is_file():
                status = _parse_status(status_path)
                if status.get("state") == "installed":
                    if int(status.get("pid", "-1")) != game.pid:
                        raise StartupError("startup.status pid does not match launch")
                    break
                if status.get("state") == "error":
                    break
            if game.poll() is not None:
                break
            time.sleep(0.2)
        if status is not None and status.get("state") == "installed":
            _wait_pair(run_dir, game, deadline)
    finally:
        termination = _terminate_owned_process(game)
        stdout.close()
        stderr.close()
    summary = {}
    if (run_dir / "summary.json").is_file():
        try:
            summary = json.loads((run_dir / "summary.json").read_text())
        except json.JSONDecodeError:
            summary = {"error": "invalid summary"}
    operations = {}
    if (run_dir / "operations-summary.json").is_file():
        try:
            operations = json.loads((run_dir / "operations-summary.json").read_text())
        except json.JSONDecodeError:
            operations = {"error": "invalid operations summary"}
    accepted = bool(
        summary.get("complete")
        and operations.get("integrity_ok")
        and operations.get("records_copied")
        and operations.get("events", 0) >= 1
    )
    report = {
        "schema": "live-gfx-capture-report/v0",
        "run_dir": str(run_dir),
        "session": session,
        "pid": game.pid,
        "evidence": evidence,
        "status": status,
        "summary": summary,
        "operations": operations,
        "termination": termination,
        "accepted": accepted,
    }
    (run_dir / "launcher-report.json").write_text(json.dumps(report, indent=2) + "\n")
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
