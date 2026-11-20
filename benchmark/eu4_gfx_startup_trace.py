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
from automation import launch_lease  # noqa: E402

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


def _build_arc_object(source: Path, output: Path) -> Path:
    built = subprocess.run(
        [
            "clang",
            "-arch",
            "x86_64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-fobjc-arc",
            "-I",
            str(BENCH),
            "-c",
            str(source),
            "-o",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if built.returncode:
        raise StartupError(built.stderr.strip() or "replay object failed to build")
    return output


def build_library() -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    replay_object = _build_arc_object(
        BENCH / "eu4_gfx_frame_replay.mm", BUILD / "eu4_gfx_frame_replay.o"
    )
    bridge_object = _build_arc_object(
        BENCH / "eu4_gfx_shadow_bridge.mm", BUILD / "eu4_gfx_shadow_bridge.o"
    )
    command = [
        "clang",
        "-arch",
        "x86_64",
        "-O2",
        "-Wall",
        "-Wextra",
        "-dynamiclib",
        "-framework", "OpenGL",
        "-framework", "AppKit",
        "-framework", "Metal",
        "-lc++",
        "-I",
        str(BENCH),
        "-o",
        str(LIBRARY),
        str(BENCH / "eu4_gfx_site_patch.c"),
        str(BENCH / "eu4_gfx_observe.c"),
        str(BENCH / "eu4_gfx_observe_buffers.c"),
        str(BENCH / "eu4_gfx_observe_draw.c"),
        str(BENCH / "eu4_gfx_observe_framebuffer.c"),
        str(BENCH / "eu4_gfx_observe_framebuffer_drawable.mm"),
        str(BENCH / "eu4_gfx_observe_viewport.c"),
        str(BENCH / "eu4_gfx_frame_trace.c"),
        str(BENCH / "eu4_gfx_frame_observe.c"),
        str(BENCH / "eu4_gfx_gl_interpose.c"),
        str(BENCH / "eu4_gfx_observe_gateway.S"),
        str(BENCH / "eu4_gfx_startup_trace.c"),
        str(BENCH / "eu4_gfx_startup_trace_detour.c"),
        str(BENCH / "eu4_gfx_startup_trace_gateway.S"),
        str(BENCH / "eu4_gfx_startup_trace_dylib.c"),
        str(replay_object),
        str(bridge_object),
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
        "-framework", "OpenGL",
        "-o",
        str(HOST),
        str(BENCH / "eu4_gfx_site_patch.c"),
        str(BENCH / "eu4_gfx_observe.c"),
        str(BENCH / "eu4_gfx_observe_buffers.c"),
        str(BENCH / "eu4_gfx_observe_draw.c"),
        str(BENCH / "eu4_gfx_observe_framebuffer.c"),
        str(BENCH / "eu4_gfx_observe_viewport.c"),
        str(BENCH / "eu4_gfx_frame_trace.c"),
        str(BENCH / "eu4_gfx_frame_observe.c"),
        str(BENCH / "eu4_gfx_gl_interpose.c"),
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


def run(
    *,
    authorize_live: bool,
    wait_seconds: float = 180.0,
    env_extra: dict | None = None,
    game_args: list[str] | None = None,
    background_plan: object | None = None,
    campaign_reservation: str | None = None,
    terminal_result=None,
) -> dict:
    """Launch one startup-trace session.

    ``env_extra`` and ``game_args`` mirror :func:`run_ops`: integration callers
    may pin additional observer environment or launcher arguments without
    changing the CLI contract.  Both are optional and omitted by default.
    """
    if not authorize_live:
        raise StartupError("Live launch requires the explicit --authorize-live flag")
    from automation import campaign_guard

    guard = campaign_guard.from_env()
    try:
        campaign_guard.require_launch_reservation(campaign_reservation,
                                                  guard=guard)
    except campaign_guard.CampaignRefusal as exc:
        raise StartupError(str(exc)) from exc
    evidence = preflight()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (ROOT / "results" / f"{stamp}-gfx-startup").resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    session = str(uuid.uuid4())
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    env = os.environ.copy()
    inserted = []
    if env.get("EU4_DISPLAY_MODE") == "background":
        for key in ("EU4_DISPLAY_PLACEMENT_LIBRARY",
                    "EU4_DISPLAY_ISOLATE_LIBRARY"):
            value = env.get(key)
            if value:
                inserted.append(value)
    inserted.append(str(LIBRARY))
    env["DYLD_INSERT_LIBRARIES"] = ":".join(inserted)
    env["EU4_GFX_STARTUP_DIR"] = str(run_dir)
    env["EU4_GFX_STARTUP_ID"] = session
    env["EU4_GFX_EXPECTED_EXE"] = str(base.GOG_EXE)
    env["EU4_GFX_EXECUTABLE_SHA256"] = evidence["executable_sha256"]
    env["EU4_GFX_STARTUP_DEADLINE_S"] = str(int(wait_seconds))
    if env_extra:
        for key, value in env_extra.items():
            env[str(key)] = str(value)
    env.pop("EU4_METAL_BOOT_MODE", None)
    env.pop("EU4_METAL_INIT_DIR", None)
    stdout = (run_dir / "game.stdout").open("wb")
    stderr = (run_dir / "game.stderr").open("wb")
    lease = launch_lease.acquire(base.GOG_EXE, output=run_dir)
    display_session = None
    try:
        if background_plan is not None:
            from automation import gfx_display

            # The lease is already held: shared display/settings changes are
            # ordered behind launch ownership and restored before release.
            display_session = gfx_display.activate_background(
                background_plan, run_dir
            )
            env.update(display_session.env)
            insertion = [str(display_session.placement_library)]
            if display_session.isolation_library is not None:
                insertion.append(str(display_session.isolation_library))
            existing = env.get("DYLD_INSERT_LIBRARIES") or ""
            if existing:
                insertion.append(existing)
            env["DYLD_INSERT_LIBRARIES"] = ":".join(insertion)
        try:
            campaign_guard.claim_launch_reservation(
                campaign_reservation, guard=guard,
                evidence={"output": str(run_dir)})
        except campaign_guard.CampaignRefusal as exc:
            raise StartupError(str(exc)) from exc
        game = subprocess.Popen(
            ["./eu4", *launcher["exeArgs"], *(game_args or [])],
            cwd=base.GOG_EXE.parent,
            env=env,
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            pass_fds=lease.child_fds(),
        )
    except BaseException as exc:
        # Setup failed before an owned game existed; still attempt every
        # independent restoration step and keep the original exception.
        cleanup = _cleanup_owned_launch(
            None, stdout=stdout, stderr=stderr,
            display_session=display_session, lease=lease)
        setattr(exc, "cleanup_errors", cleanup["errors"])
        if not cleanup["ok"]:
            exc.args = (
                f"{exc}; cleanup failed: " + "; ".join(cleanup["errors"]),)
        raise
    status_path = run_dir / "startup.status"
    deadline = time.monotonic() + wait_seconds
    status = None
    wait_error = None
    wait_reason = "not_installed"
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
    except BaseException as exc:  # cleanup must still run and be reported
        wait_error = exc
    finally:
        # Each step is attempted even when an earlier one fails.  The
        # aggregate record is the only authority for cleanup success.
        cleanup = _cleanup_owned_launch(
            game, stdout=stdout, stderr=stderr,
            display_session=display_session, lease=lease)

    if wait_error is not None:
        cleanup_ok = bool(cleanup.get("ok"))
        failure_report = {
            "run_dir": str(run_dir), "session": session, "pid": game.pid,
            "evidence": evidence, "status": status,
            "cleanup": cleanup, "cleanup_ok": cleanup_ok,
            "cleanup_errors": cleanup.get("errors") or [],
            "process_alive": cleanup.get("process_alive"),
            "display": cleanup.get("display"), "lease": cleanup.get("lease"),
            "accepted": False, "outcome": "failed",
            "failure": f"{type(wait_error).__name__}: {wait_error}",
        }
        (run_dir / "launcher-report.json").write_text(
            json.dumps(failure_report, indent=2) + "\n")
        setattr(wait_error, "cleanup_errors", cleanup.get("errors") or [])
        if not cleanup_ok:
            wait_error.args = (
                f"{wait_error}; cleanup failed: "
                + "; ".join(cleanup.get("errors") or []),)
        raise wait_error
    summary = {}
    if (run_dir / "summary.json").is_file():
        summary = json.loads((run_dir / "summary.json").read_text())
    cleanup_ok = bool(cleanup.get("ok"))
    accepted = bool(summary.get("complete") and cleanup_ok)
    report = {
        "run_dir": str(run_dir),
        "session": session,
        "pid": game.pid,
        "evidence": evidence,
        "status": status,
        "summary": summary,
        "cleanup": cleanup,
        "cleanup_ok": cleanup_ok,
        "cleanup_errors": cleanup.get("errors") or [],
        "process_alive": cleanup.get("process_alive"),
        "display": cleanup.get("display"),
        "lease": cleanup.get("lease"),
        "accepted": accepted,
        "outcome": "ok" if accepted else "failed",
    }
    (run_dir / "launcher-report.json").write_text(json.dumps(report, indent=2) + "\n")
    if not accepted:
        detail = "; ".join(cleanup.get("errors") or [])
        raise StartupError(
            f"startup capture incomplete: {summary}"
            + (f"; cleanup failed: {detail}" if detail else ""))
    return report


def preflight_ops(
    targets: list[str],
    *,
    max_events: int = 256,
    duration_s: float = 10.0,
    until_target_draw: bool = False,
    target_caller_rva: int | None = None,
    gl_reference: bool = False,
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
    if gl_reference and not until_target_draw:
        raise StartupError("--gl-reference requires --until-target-draw")
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
    evidence["gl_reference"] = bool(gl_reference)
    return evidence


def _wait_pair(run_dir: Path, game: subprocess.Popen, deadline: float,
               terminal=None) -> str:
    """Wait for the requested terminal record, the ops pair, or game exit.

    ``terminal`` is an optional predicate for a capture mode whose authoritative
    result is not the legacy operations pair.  It is evaluated alongside the
    original target, so ordinary callers keep their established wait.  The
    return value records *why* the wait ended; it is not a success claim.
    """
    summary = run_dir / "summary.json"
    operations = run_dir / "operations-summary.json"
    while time.monotonic() < deadline:
        if terminal is not None:
            try:
                if terminal():
                    return "terminal"
            except Exception:  # noqa: BLE001 - a parse race is retried
                pass
        if summary.is_file() and operations.is_file():
            return "summary_pair"
        if game.poll() is not None:
            time.sleep(0.3)
            if terminal is not None and terminal():
                return "terminal_after_exit"
            return "game_exit"
        time.sleep(0.2)
    if terminal is not None and terminal():
        return "terminal_at_deadline"
    return "deadline"


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


def _cleanup_error(step: str, exc: BaseException) -> str:
    return f"{step}: {type(exc).__name__}: {exc}"


def _cleanup_owned_launch(
    game: subprocess.Popen | None,
    *,
    stdout=None,
    stderr=None,
    display_session=None,
    lease=None,
) -> dict:
    """Attempt every cleanup step, then report the aggregate outcome.

    The steps are deliberately independent: a failure to terminate the owned
    process must not skip stream closure, display/settings restoration or the
    host-wide lease release.  Each attempt is recorded, and ``ok`` is true only
    when every mandatory step succeeded and the owned process is confirmed
    gone.  A caller must not report success when this record is not ok.
    """
    errors: list[str] = []
    record: dict = {
        "schema": "live-gfx-cleanup/v1",
        "termination": None,
        "process_alive": None,
        "streams": {},
        "display": None,
        "lease": None,
        "errors": errors,
        "ok": False,
    }

    if game is not None:
        termination = {"pid": getattr(game, "pid", None),
                       "terminated": False, "killed": False}
        try:
            termination = _terminate_owned_process(game)
        except Exception as exc:  # noqa: BLE001 - reported, not hidden
            termination["error"] = f"{type(exc).__name__}: {exc}"
            errors.append(_cleanup_error("terminate", exc))
        try:
            alive = game.poll() is None
        except Exception as exc:  # noqa: BLE001
            alive = None
            errors.append(_cleanup_error("terminate: poll", exc))
        termination["alive"] = alive
        record["termination"] = termination
        record["process_alive"] = alive
        if alive is True:
            errors.append(
                f"terminate: owned process {termination.get('pid')} is still alive")
        elif alive is None:
            errors.append(
                "terminate: owned process exit could not be confirmed")

    for stream, name in ((stdout, "stdout"), (stderr, "stderr")):
        if stream is None:
            continue
        try:
            stream.close()
        except Exception as exc:  # noqa: BLE001
            record["streams"][name] = f"failed: {type(exc).__name__}: {exc}"
            errors.append(_cleanup_error(f"close {name}", exc))
        else:
            record["streams"][name] = "closed"

    if display_session is not None:
        display: dict = {"session": None, "restore": None}
        try:
            display["session"] = display_session.as_dict()
        except Exception as exc:  # noqa: BLE001 - diagnostics only
            display["session_error"] = f"{type(exc).__name__}: {exc}"
        restore_failed = False
        try:
            restore = display_session.restore()
        except Exception as exc:  # noqa: BLE001
            restore = {"restored": False,
                       "error": f"{type(exc).__name__}: {exc}"}
            errors.append(_cleanup_error("display restore", exc))
            restore_failed = True
        display["restore"] = restore
        record["display"] = display
        if not restore_failed:
            restored = isinstance(restore, dict) and (
                restore.get("restored") is True
                or restore.get("reason") == "unchanged")
            if not restored:
                detail = ""
                if isinstance(restore, dict):
                    detail = str(
                        restore.get("error") or restore.get("reason") or "")
                errors.append(
                    "display restore: settings were not restored"
                    + (f": {detail}" if detail else ""))

    if lease is not None:
        lease_record: dict = {"released": False}
        try:
            lease.release()
        except Exception as exc:  # noqa: BLE001
            lease_record["error"] = f"{type(exc).__name__}: {exc}"
            errors.append(_cleanup_error("lease release", exc))
        else:
            lease_record["released"] = True
        release_error = getattr(lease, "release_error", None)
        if release_error is not None:
            lease_record["released"] = False
            lease_record["error"] = str(release_error)
            errors.append(f"lease release: {release_error}")
        record["lease"] = lease_record

    record["ok"] = not errors
    return record


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
    gl_reference: bool = False,
    env_extra: dict | None = None,
    retention: str | None = None,
    game_args: list[str] | None = None,
    background_plan: object | None = None,
    campaign_reservation: str | None = None,
    terminal_result=None,
) -> dict:
    """Launch one bounded original-process observation session.

    ``background_plan`` is a validated ``gfx_display.BackgroundPlan``.  Display
    activation and settings mutation happen only after the host-wide launch
    lease is held, and the display settings are restored before it is released.
    """
    if not authorize_live:
        raise StartupError("Live capture requires --authorize-live or a campaign")
    if not 1 <= timeout_s <= 600:
        raise StartupError("--timeout must be between 1 and 600 seconds")
    if until_target_draw and target_caller_rva is None:
        raise StartupError("--until-target-draw requires --caller-rva")
    # This is the single process-spawn choke point for the trace/capture paths.
    # Tests that replace ``run_ops`` with a fake never reach it; an explicit
    # offline profile cannot launch EU4 through this path.
    from automation import campaign_guard, test_profiles

    test_profiles.ensure_live_launch("eu4_gfx_startup_trace.run_ops")
    # Every capture path funnels through this spawn site.  When a campaign
    # budget is active, only an open launch reservation may proceed, so an
    # alternative unguarded launcher cannot bypass the guard.
    guard = campaign_guard.from_env()
    try:
        campaign_guard.require_launch_reservation(campaign_reservation,
                                                  guard=guard)
    except campaign_guard.CampaignRefusal as exc:
        raise StartupError(str(exc)) from exc
    evidence = evidence or preflight_ops(
        targets,
        max_events=max_events,
        duration_s=duration_s,
        until_target_draw=until_target_draw,
        target_caller_rva=target_caller_rva,
        gl_reference=gl_reference,
    )
    if gl_reference and not until_target_draw:
        raise StartupError("--gl-reference requires --until-target-draw")
    run_dir = Path(output).expanduser().resolve()
    if run_dir.exists():
        raise StartupError(f"Capture output already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=False)
    session = str(uuid.uuid4())
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    env = os.environ.copy()
    inserted = []
    if env.get("EU4_DISPLAY_MODE") == "background":
        for key in ("EU4_DISPLAY_PLACEMENT_LIBRARY",
                    "EU4_DISPLAY_ISOLATE_LIBRARY"):
            value = env.get(key)
            if value:
                inserted.append(value)
    inserted.append(str(LIBRARY))
    env["DYLD_INSERT_LIBRARIES"] = ":".join(inserted)
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
    env["EU4_GFX_OPS_RETENTION"] = (
        retention
        if retention is not None
        else ("state" if until_target_draw else "legacy")
    )
    if retention is not None and retention not in ("state", "legacy"):
        raise StartupError("retention must be 'state' or 'legacy'")
    if target_caller_rva is not None:
        env["EU4_GFX_OPS_TARGET_CALLER"] = f"0x{target_caller_rva:x}"
    env["EU4_GFX_OPS_FRAMEBUFFER"] = "pre+post" if gl_reference else ""
    if env_extra:
        for key, value in env_extra.items():
            env[str(key)] = str(value)
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
        "gl_reference": bool(gl_reference),
        "timeout_s": timeout_s,
        "executable_sha256": evidence["executable_sha256"],
        "library_sha256": evidence["library_sha256"],
    }
    (run_dir / "capture-request.json").write_text(
        json.dumps(request, indent=2) + "\n"
    )
    stdout = (run_dir / "game.stdout").open("wb")
    stderr = (run_dir / "game.stderr").open("wb")
    lease = launch_lease.acquire(base.GOG_EXE, output=run_dir)
    display_session = None
    try:
        if background_plan is not None:
            from automation import gfx_display

            # The lease is already held: shared display/settings changes are
            # ordered behind launch ownership and restored before release.
            display_session = gfx_display.activate_background(
                background_plan, run_dir
            )
            env.update(display_session.env)
            insertion = [str(display_session.placement_library)]
            if display_session.isolation_library is not None:
                insertion.append(str(display_session.isolation_library))
            existing = env.get("DYLD_INSERT_LIBRARIES") or ""
            if existing:
                insertion.append(existing)
            env["DYLD_INSERT_LIBRARIES"] = ":".join(insertion)
        # The one-use entitlement is consumed after all read-only preflight and
        # environment preparation, immediately before the process exists.
        try:
            campaign_guard.claim_launch_reservation(
                campaign_reservation, guard=guard,
                evidence={"output": str(run_dir)})
        except campaign_guard.CampaignRefusal as exc:
            raise StartupError(str(exc)) from exc
        game = subprocess.Popen(
            ["./eu4", *launcher["exeArgs"], *(game_args or [])],
            cwd=base.GOG_EXE.parent,
            env=env,
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            pass_fds=lease.child_fds(),
        )
    except BaseException as exc:
        # Setup failed before an owned game existed; still attempt every
        # independent restoration step and keep the original exception.
        cleanup = _cleanup_owned_launch(
            None, stdout=stdout, stderr=stderr,
            display_session=display_session, lease=lease)
        setattr(exc, "cleanup_errors", cleanup["errors"])
        if not cleanup["ok"]:
            exc.args = (
                f"{exc}; cleanup failed: " + "; ".join(cleanup["errors"]),)
        raise
    status_path = run_dir / "startup.status"
    status = None
    deadline = time.monotonic() + timeout_s + 20.0
    wait_error = None
    wait_reason = "not_installed"
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
            wait_reason = _wait_pair(
                run_dir, game, deadline, terminal=terminal_result)
    except BaseException as exc:  # cleanup must still run and be reported
        wait_error = exc
    finally:
        # Each step is attempted even when an earlier one fails.  The
        # aggregate record is the only authority for cleanup success.
        cleanup = _cleanup_owned_launch(
            game, stdout=stdout, stderr=stderr,
            display_session=display_session, lease=lease)
    if wait_error is not None:
        # A wait/startup error must not hide termination, display or lease
        # failures behind an unrelated exception.  Publish the cleanup record
        # and carry it on the exception before propagating.
        cleanup_ok = bool(cleanup.get("ok"))
        failure = ("cleanup failed: " + "; ".join(cleanup.get("errors") or [])
                   if not cleanup_ok else f"{type(wait_error).__name__}: "
                                          f"{wait_error}")
        failure_report = {
            "schema": "live-gfx-capture-report/v0",
            "run_dir": str(run_dir), "session": session, "pid": game.pid,
            "wait_reason": wait_reason,
            "cleanup": cleanup, "cleanup_ok": cleanup_ok,
            "cleanup_errors": cleanup.get("errors") or [],
            "process_alive": cleanup.get("process_alive"),
            "terminal_shadow": wait_reason.startswith("terminal"),
            "evidence": evidence, "status": status,
            "termination": cleanup.get("termination"),
            "display": cleanup.get("display"), "lease": cleanup.get("lease"),
            "accepted": False, "outcome": "failed", "failure": failure,
        }
        (run_dir / "launcher-report.json").write_text(
            json.dumps(failure_report, indent=2) + "\n")
        setattr(wait_error, "cleanup_errors", cleanup.get("errors") or [])
        if not cleanup_ok:
            wait_error.args = (
                f"{wait_error}; cleanup failed: "
                + "; ".join(cleanup.get("errors") or []),)
        raise wait_error
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
    accepted_operations = bool(
        summary.get("complete")
        and operations.get("integrity_ok")
        and operations.get("records_copied")
        and operations.get("events", 0) >= 1
    )
    cleanup_ok = bool(cleanup.get("ok"))
    accepted = bool(accepted_operations and cleanup_ok)
    failure = None
    if not cleanup_ok:
        failure = "cleanup failed: " + "; ".join(cleanup.get("errors") or [])
    elif not accepted_operations:
        failure = "operations were not accepted"
    report = {
        "schema": "live-gfx-capture-report/v0",
        "run_dir": str(run_dir),
        "session": session,
        "pid": game.pid,
        "wait_reason": locals().get("wait_reason", "not_installed"),
        "cleanup": cleanup,
        "cleanup_ok": cleanup_ok,
        "cleanup_errors": cleanup.get("errors") or [],
        "process_alive": cleanup.get("process_alive"),
        "terminal_shadow": locals().get("wait_reason", "").startswith(
            "terminal"),
        "evidence": evidence,
        "status": status,
        "summary": summary,
        "operations": operations,
        "termination": cleanup.get("termination"),
        "display": cleanup.get("display"),
        "lease": cleanup.get("lease"),
        "accepted": accepted,
        "outcome": "ok" if accepted else "failed",
        "failure": failure,
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
    run_parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="additional observer environment (repeatable)",
    )
    run_parser.add_argument(
        "--game-arg",
        action="append",
        default=[],
        help="additional launcher argument (repeatable)",
    )
    args = parser.parse_args()
    if args.command == "build":
        print(build_library())
        print(build_host())
        return 0
    if args.command == "preflight":
        print(json.dumps(preflight(), indent=2))
        return 0
    env_extra = {}
    for item in args.env:
        if "=" not in item:
            parser.error(f"--env requires KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        if not key:
            parser.error("--env requires a non-empty key")
        env_extra[key] = value
    report = run(
        authorize_live=args.authorize_live,
        wait_seconds=args.wait_seconds,
        env_extra=env_extra or None,
        game_args=list(args.game_arg) or None,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except StartupError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
