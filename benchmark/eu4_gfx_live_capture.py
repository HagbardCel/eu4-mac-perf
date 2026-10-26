#!/usr/bin/env python3
"""One bounded, operator-authorized _GfxSetConstantBuffer live acquisition."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import mmap
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import autonomous_runner as auto  # noqa: E402
import eu4_benchmark as base  # noqa: E402
from fixture_manager import FixtureManager  # noqa: E402
from oracle.function import fixture  # noqa: E402
from reverse_engineering import paths  # noqa: E402
from reverse_engineering.macho_rva import (  # noqa: E402
    MachOMapper,
    assert_pinned_executable,
)

BENCH = ROOT / "benchmark"
BUILD = BENCH / ".build"
LIBRARY = BUILD / "libeu4_gfx_live_capture.dylib"
FOCUS_SOURCE = BENCH / "mac_game_focus.m"
FOCUS = BUILD / "mac_game_focus"
FOCUS_BUILD_TIMEOUT_S = 15
FOCUS_ACTIVATE_TIMEOUT_S = 3
TARGET_RVA = 0x15EC36A
PATCH_BYTES = bytes.fromhex("554889e5488b0789c94183f9ff")
QUOTA = 64
THREADS = 64
ACCEPTANCE = 50
CONTROL_BYTES = 4096
SOURCES = (
    BENCH / "eu4_gfx_capture_detour.c",
    BENCH / "eu4_gfx_capture_gateway.S",
    BENCH / "eu4_gfx_set_constant_buffer_capture.c",
    BENCH / "eu4_gfx_live_capture.c",
    BENCH / "eu4_gfx_live_capture_dylib.c",
)
HEADERS = (
    BENCH / "eu4_gfx_capture_detour.h",
    BENCH / "eu4_gfx_set_constant_buffer_capture.h",
    BENCH / "eu4_gfx_live_capture.h",
)
SCENARIOS = {"live:length-from-buffer", "live:explicit-length"}
EPOCH = "p5a-live"
SUMMARY_HEADER = "EU4-GFX-LIVE-SUMMARY v1"
EXACT_SUMMARY = "EU4-GFX-LIVE-SUMMARY exact-dedup/v1"
MAX_UNIQUE_LIMIT = 4096
CANON_REGIONS = ("handle", "ctx", "canary-before", "canary-after", "buffer")
STARTUP_HEADER = "EU4-GFX-LIVE-STARTUP v1"
DONE_HEADER = "EU4-GFX-LIVE-DONE v1"
VENICE_FIXTURE = ROOT / "fixtures/venice_paused.eu4"
VENICE_MANIFEST = ROOT / "fixtures/venice_paused.json"
PROBE = auto.PROBE
PROBE_SOURCE = auto.PROBE_SOURCE
READY_TIMEOUT_S = auto.READY_TIMEOUT_S
VENICE_READY_TIMEOUT_MSG = auto.VENICE_READY_TIMEOUT_MSG
FOCUS_REPAIR_INTERVAL_S = 2.0
FOCUS_INSPECT_INTERVAL_S = 5.0
FOCUS_SNAPSHOT_MAX = 5
TERMINATE_GRACE_S = 15


class LiveError(base.BenchmarkError):
    pass


def build() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    command = [
        "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
        "-mno-avx", "-I", str(BENCH), "-dynamiclib", "-o", str(LIBRARY),
        *(str(source) for source in SOURCES),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise LiveError(
            f"Could not build live capture library: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    if base.command("lipo", "-archs", str(LIBRARY)).strip() != "x86_64":
        raise LiveError("Live capture library is not x86_64")


def _discard_partial(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _try_build_focus_helper() -> tuple[Path | None, str | None]:
    if not FOCUS_SOURCE.is_file():
        return None, "focus source missing"
    try:
        BUILD.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return None, f"focus build staging failed: {exc}"
    partial = BUILD / f".mac_game_focus.partial-{os.getpid()}"
    try:
        partial.unlink(missing_ok=True)
    except OSError as exc:
        return None, f"focus build staging failed: {exc}"
    command = [
        "clang", "-O2", "-Wall", "-Wextra", "-Werror",
        "-Wno-deprecated-declarations", "-fobjc-arc", "-framework", "AppKit",
        "-framework", "CoreGraphics", "-o", str(partial), str(FOCUS_SOURCE),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False,
            timeout=FOCUS_BUILD_TIMEOUT_S)
    except OSError as exc:
        _discard_partial(partial)
        return None, f"focus build launch failed: {exc}"
    except subprocess.TimeoutExpired:
        _discard_partial(partial)
        return None, "focus build timed out"
    if result.returncode:
        _discard_partial(partial)
        detail = result.stderr.strip() or result.stdout.strip() or "nonzero exit"
        return None, f"focus build failed: {detail}"
    try:
        partial.replace(FOCUS)
    except OSError as exc:
        _discard_partial(partial)
        return None, f"focus install failed: {exc}"
    return FOCUS, None


def _request_focus_activation(focus_bin: Path, pid: int) -> tuple[bool, bool, str | None]:
    try:
        result = subprocess.run(
            [str(focus_bin), "activate", str(pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            timeout=FOCUS_ACTIVATE_TIMEOUT_S)
    except OSError as exc:
        return True, False, f"activate launch failed: {exc}"
    except subprocess.TimeoutExpired:
        return True, False, "activate timed out"
    if result.returncode:
        return True, False, f"activate exit {result.returncode}"
    return True, True, None


def _venice_fixture_authority() -> dict:
    if not VENICE_MANIFEST.is_file():
        raise LiveError(f"Venice fixture manifest is missing: {VENICE_MANIFEST}")
    manifest = json.loads(VENICE_MANIFEST.read_text(encoding="utf-8"))
    digest = manifest.get("save_sha256")
    if not isinstance(digest, str) or not digest:
        raise LiveError("Venice fixture manifest has no save_sha256")
    if not VENICE_FIXTURE.is_file():
        raise LiveError(
            f"Pinned Venice fixture is missing locally: {VENICE_FIXTURE}")
    observed = base.sha256(VENICE_FIXTURE)
    if observed != digest:
        raise LiveError("Venice fixture digest differs from the pinned manifest")
    return {"fixture_sha256": digest, "fixture_manifest": manifest}


def _build_probe_dylib() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    command = [
        "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
        "-dynamiclib", "-framework", "OpenGL", "-o", str(PROBE), str(PROBE_SOURCE),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise LiveError(
            f"Could not build readiness probe: "
            f"{result.stderr.strip() or result.stdout.strip()}")
    if base.command("lipo", "-archs", str(PROBE)).strip() != "x86_64":
        raise LiveError("Readiness probe is not x86_64")


def _require_focus_helper() -> Path:
    focus_bin, error = _try_build_focus_helper()
    if focus_bin is None:
        raise LiveError(
            f"Auto Venice capture requires mac_game_focus: {error or 'unavailable'}")
    return focus_bin


def _focus_timeout_for(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 0.0
    return min(FOCUS_ACTIVATE_TIMEOUT_S, remaining)


def _focus_command(focus_bin: Path, action: str, pid: int, timeout: float) -> bool:
    if timeout <= 0:
        return False
    try:
        result = subprocess.run(
            [str(focus_bin), action, str(pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _focus_inspect(
    focus_bin: Path, pid: int, timeout: float,
) -> tuple[dict | None, str | None]:
    if timeout <= 0:
        return None, "inspect timeout budget exhausted"
    try:
        result = subprocess.run(
            [str(focus_bin), "inspect", str(pid)],
            capture_output=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, f"inspect exit {result.returncode}"
    try:
        payload = json.loads(result.stdout.decode("utf-8").strip() or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "inspect payload is not an object"
    return payload, None


def venice_log_probe_ready(predicates: dict[str, bool | int]) -> bool:
    return (
        predicates["game_log_ready"]
        and predicates["probe_sample_count"] >= 10
        and predicates["probe_fresh"]
        and predicates["paused_rows_ok"]
    )


def _observe_focus_state(
    focus_bin: Path, pid: int, deadline: float,
) -> tuple[bool, bool]:
    frontmost = _focus_command(
        focus_bin, "check", pid, _focus_timeout_for(deadline))
    interior = False
    if frontmost:
        interior = _focus_command(
            focus_bin, "interior", pid, _focus_timeout_for(deadline))
    return frontmost, interior


def _record_focus_snapshot(
    diagnostics: dict,
    focus_bin: Path,
    pid: int,
    deadline: float,
    *,
    reason: str,
) -> None:
    observed_utc = dt.datetime.now(dt.timezone.utc).isoformat()
    timeout = _focus_timeout_for(deadline)
    snapshot, error = _focus_inspect(focus_bin, pid, timeout)
    entry = {
        "reason": reason,
        "observed_utc": observed_utc,
        "snapshot": snapshot,
        "error": error,
    }
    snapshots = diagnostics.setdefault("focus_snapshots", [])
    snapshots.append(entry)
    if len(snapshots) > FOCUS_SNAPSHOT_MAX:
        del snapshots[:-FOCUS_SNAPSHOT_MAX]
    diagnostics["last_focus_snapshot"] = entry


def _maybe_record_focus_snapshot(
    diagnostics: dict,
    focus_bin: Path,
    pid: int,
    deadline: float,
    inspect_clock: list[float],
    *,
    reason: str,
) -> None:
    now = time.monotonic()
    if inspect_clock[0] == 0.0 or now - inspect_clock[0] >= FOCUS_INSPECT_INTERVAL_S:
        _record_focus_snapshot(
            diagnostics, focus_bin, pid, deadline, reason=reason)
        inspect_clock[0] = now


def _bounded_focus_state(
    focus_bin: Path,
    pid: int,
    deadline: float,
    counters: dict[str, int],
    repair_clock: list[float],
) -> tuple[bool, bool]:
    if time.monotonic() >= deadline:
        return False, False
    now = time.monotonic()
    frontmost = _focus_command(
        focus_bin, "check", pid, _focus_timeout_for(deadline))
    if not frontmost and now - repair_clock[0] >= FOCUS_REPAIR_INTERVAL_S:
        counters["activation_attempts"] += 1
        if _focus_command(focus_bin, "activate", pid, _focus_timeout_for(deadline)):
            frontmost = _focus_command(
                focus_bin, "check", pid, _focus_timeout_for(deadline))
        repair_clock[0] = time.monotonic()
    interior = False
    if frontmost:
        interior = _focus_command(
            focus_bin, "interior", pid, _focus_timeout_for(deadline))
        now = time.monotonic()
        if not interior and now - repair_clock[0] >= FOCUS_REPAIR_INTERVAL_S:
            counters["pointer_reparks"] += 1
            if _focus_command(focus_bin, "park", pid, _focus_timeout_for(deadline)):
                interior = _focus_command(
                    focus_bin, "interior", pid, _focus_timeout_for(deadline))
            repair_clock[0] = time.monotonic()
    if not frontmost:
        counters["frontmost_false_iterations"] += 1
    elif not interior:
        counters["pointer_noninterior_iterations"] += 1
    return frontmost, interior


def evaluate_venice_readiness(
    *,
    logged: bool,
    frontmost: bool,
    pointer_interior: bool,
    rows: list[dict],
    now_ns: int,
) -> dict[str, bool | int]:
    return auto.readiness_predicates(
        logged=logged,
        frontmost=frontmost,
        pointer_interior=pointer_interior,
        rows=rows,
        now_ns=now_ns,
        power=[],
    )


def wait_for_venice_ready(
    game: subprocess.Popen,
    focus_bin: Path,
    probe_path: Path,
    anchor: dict,
    game_log: Path,
    old_log: bytes,
    diagnostics: dict | None = None,
    *,
    require_focus: bool = True,
) -> dict:
    deadline = time.monotonic() + READY_TIMEOUT_S
    counters = {
        "activation_attempts": 0,
        "pointer_reparks": 0,
        "frontmost_false_iterations": 0,
        "pointer_noninterior_iterations": 0,
    }
    repair_clock = [0.0]
    inspect_clock = [0.0]
    last_predicates: dict[str, bool | int] | None = None
    if diagnostics is not None:
        diagnostics["require_focus"] = require_focus
        _record_focus_snapshot(
            diagnostics, focus_bin, game.pid, deadline, reason="initial")
        inspect_clock[0] = time.monotonic()
    while time.monotonic() < deadline:
        if game.poll() is not None:
            raise LiveError("GOG EU IV exited before Venice became ready")
        if time.monotonic() >= deadline:
            break
        if require_focus:
            frontmost, interior = _bounded_focus_state(
                focus_bin, game.pid, deadline, counters, repair_clock)
        else:
            frontmost, interior = _observe_focus_state(
                focus_bin, game.pid, deadline)
        if diagnostics is not None:
            _maybe_record_focus_snapshot(
                diagnostics, focus_bin, game.pid, deadline, inspect_clock,
                reason="periodic")
        now_ns = time.monotonic_ns()
        log_text = auto.fresh_game_log(game_log, old_log)
        logged = auto.game_log_ready(log_text)
        rows = auto.probe_rows(probe_path, anchor)[-10:]
        predicates = evaluate_venice_readiness(
            logged=logged,
            frontmost=frontmost,
            pointer_interior=interior,
            rows=rows,
            now_ns=now_ns,
        )
        last_predicates = predicates
        strict_ready = bool(predicates["ready_to_pass"])
        log_probe_ready = venice_log_probe_ready(predicates)
        selected_ready = strict_ready if require_focus else log_probe_ready
        if diagnostics is not None:
            diagnostics.update({
                "last_predicates": predicates,
                "counters": dict(counters),
                "strict_ready": strict_ready,
                "venice_log_probe_ready": log_probe_ready,
                "selected_gate_ready": selected_ready,
                "ready_to_pass": strict_ready,
                "observed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            })
        if selected_ready and time.monotonic() < deadline:
            if game.poll() is not None:
                raise LiveError("GOG EU IV exited before Venice became ready")
            return {
                "ready_ns": now_ns,
                "predicates": predicates,
                "counters": counters,
                "require_focus": require_focus,
                "strict_ready": strict_ready,
                "venice_log_probe_ready": log_probe_ready,
                "selected_gate_ready": selected_ready,
            }
        time.sleep(1)
    if diagnostics is not None:
        diagnostics["timed_out"] = True
        diagnostics["timed_out_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        diagnostics.setdefault("last_predicates", last_predicates or {})
        diagnostics.setdefault("counters", dict(counters))
    raise LiveError(
        f"{VENICE_READY_TIMEOUT_MSG}: {last_predicates or {}}")


def _terminate_owned_game(game: subprocess.Popen | None, focus_bin: Path) -> None:
    if game is None or game.poll() is not None:
        return
    _focus_command(focus_bin, "terminate", game.pid, FOCUS_ACTIVATE_TIMEOUT_S)
    grace_deadline = time.monotonic() + TERMINATE_GRACE_S
    while time.monotonic() < grace_deadline:
        if game.poll() is not None:
            return
        time.sleep(0.25)
    try:
        auto.stop_process(game, 10)
    except (OSError, subprocess.TimeoutExpired):
        return


def _fixture_cleanup_state(
    fixture: FixtureManager, working_save: Path,
) -> tuple[bool, bool]:
    journal = fixture.journal
    continue_restored = not journal.is_file()
    cleanup_completed = continue_restored and not working_save.exists()
    return continue_restored, cleanup_completed


def _auto_venice_failure_message(metadata: dict) -> str:
    parts: list[str] = []
    if metadata.get("error"):
        parts.append(str(metadata["error"]))
    for key in ("cleanup_error", "save_hash_error", "terminate_error"):
        value = metadata.get(key)
        if value:
            parts.append(f"{key}: {value}")
    if parts:
        return "; ".join(parts)
    return "Auto Venice capture did not produce an accepted report"


def preflight(save: Path | None = None, auto_venice: bool = False) -> dict:
    if auto_venice and save is not None:
        raise LiveError("--auto-venice cannot be combined with --save")
    build()
    identity = base.gog_identity()
    if base.command("lipo", "-archs", str(base.GOG_EXE)).strip() != "x86_64":
        raise LiveError("The installed GOG build is not x86_64")
    try:
        assert_pinned_executable(base.GOG_EXE)
    except Exception as exc:
        raise LiveError(str(exc)) from exc
    try:
        base.running_game_pid("idle")
    except base.BenchmarkError as exc:
        raise LiveError("Close EU IV before the live capture preflight") from exc

    descriptor = fixture.load_descriptor()
    target = paths.load_target()
    window = paths.code_window("_GfxSetConstantBuffer")
    if target["sha256"] != descriptor["binary"]["sha256"]:
        raise LiveError("Pinned target digest differs from the descriptor")
    entry = int(window["entry_rva"], 16)
    end = int(window["end_rva"], 16)
    if entry != int(descriptor["binary"]["entry_rva"], 16):
        raise LiveError("Pinned code window entry differs from the descriptor")
    with MachOMapper(base.GOG_EXE) as mapper:
        code = mapper.read_bytes_at_rva(int(target["image_base"]), entry, end - entry)
    if hashlib.sha256(code).hexdigest() != window["sha256"]:
        raise LiveError("Pinned code window digest does not match")
    if code[: len(PATCH_BYTES)] != PATCH_BYTES:
        raise LiveError("Pinned patch-site bytes do not match")

    if auto_venice:
        fixture_info = _venice_fixture_authority()
        _build_probe_dylib()
        _require_focus_helper()
        save_path = VENICE_FIXTURE
        save_hash = fixture_info["fixture_sha256"]
        acquisition_mode = "auto_venice"
    else:
        chosen_save = (save or base.DEFAULT_SAVE).expanduser()
        if not chosen_save.is_file():
            raise LiveError(f"Disposable Venice save is missing: {chosen_save}")
        save_path = chosen_save
        save_hash = base.sha256(chosen_save)
        acquisition_mode = "manual"
        fixture_info = None
    return {
        "acquisition_mode": acquisition_mode,
        "fixture_sha256": fixture_info["fixture_sha256"] if fixture_info else None,
        "gog": identity,
        "descriptor_sha256": base.sha256(
            ROOT / "oracle/function/descriptors/gfx_set_constant_buffer.json"
        ),
        "descriptor_policy": fixture.capture_policy_digest(descriptor),
        "code_window_sha256": window["sha256"],
        "patch_site_sha256": hashlib.sha256(PATCH_BYTES).hexdigest(),
        "library": str(LIBRARY),
        "library_sha256": base.sha256(LIBRARY),
        "source_sha256": {
            path.name: base.sha256(path) for path in (*SOURCES, *HEADERS)
        },
        "save": str(save_path),
        "save_sha256": save_hash,
        "probe_library": str(PROBE) if auto_venice else None,
        "probe_library_sha256": base.sha256(PROBE) if auto_venice else None,
    }


def _parse_table(path: Path, header: str) -> dict[str, str]:
    if not path.is_file():
        raise LiveError(f"Missing table: {path.name}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 3 or lines[0] != header or lines[-1] != "END":
        raise LiveError(f"Malformed table envelope: {path.name}")
    values: dict[str, str] = {}
    for line in lines[1:-1]:
        if "=" not in line:
            raise LiveError(f"Malformed table row in {path.name}: {line!r}")
        key, value = line.split("=", 1)
        if not key or key in values:
            raise LiveError(f"Duplicate or empty table key in {path.name}: {key!r}")
        values[key] = value
    return values


def _integer(values: dict[str, str], key: str, *, hexadecimal: bool = False) -> int:
    value = values.get(key)
    if value is None:
        raise LiveError(f"Missing table value: {key}")
    try:
        return int(value, 16 if hexadecimal else 10)
    except ValueError as exc:
        raise LiveError(f"Invalid integer for {key}: {value!r}") from exc


def _optional_integer(values: dict[str, str] | None, key: str) -> int | None:
    if values is None:
        return None
    try:
        return _integer(values, key)
    except LiveError:
        return None


def _write_control(path: Path, value: int) -> None:
    with path.open("r+b") as source:
        with mmap.mmap(source.fileno(), CONTROL_BYTES) as page:
            struct.pack_into("<Q", page, 0, value)
            page.flush()


def _wait_for(path: Path, seconds: float, game: subprocess.Popen) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        if game.poll() is not None:
            return path.is_file()
        time.sleep(0.1)
    return path.is_file()


def _verify_startup_attestation(
    startup_path: Path, session_id: str, game: subprocess.Popen,
) -> dict[str, str]:
    startup = _parse_table(startup_path, STARTUP_HEADER)
    if startup.get("state") != "installed":
        raise LiveError(
            f"Live dylib startup failed: {startup.get('error') or 'unknown'}")
    if startup.get("session") != session_id:
        raise LiveError("Startup session ID does not match this run")
    if _integer(startup, "pid") != game.pid:
        raise LiveError("Startup PID does not match the launched process")
    if _integer(startup, "site", hexadecimal=True) != (
            _integer(startup, "image_base", hexadecimal=True) + TARGET_RVA):
        raise LiveError("Startup site address is not the pinned RVA")
    if Path(startup.get("executable", "")).resolve() != base.GOG_EXE.resolve():
        raise LiveError("Startup executable path does not match the pinned GOG build")
    return startup


def _arm_and_seal_capture(
    control: Path,
    game: subprocess.Popen,
    run_dir: Path,
    captures: Path,
    wait_seconds: int,
    deadline_seconds: int,
    max_unique: int,
) -> None:
    _write_control(control, 1)
    print(f"Capture is armed. max_unique is {max_unique}; "
          "the session closes on that limit or the deadline.")
    done = run_dir / "session.done"
    wait_limit = max(1, min(wait_seconds, deadline_seconds - 10))
    end = time.monotonic() + wait_limit
    while time.monotonic() < end:
        if done.is_file():
            break
        if len(list(captures.glob("*.capture"))) >= max_unique:
            break
        if game.poll() is not None:
            break
        time.sleep(0.25)
    _write_control(control, 2)
    if not _wait_for(done, 15, game):
        raise LiveError("The live session did not seal before the drain deadline")
    if game.poll() is not None:
        raise LiveError("The game exited before the session was confirmed")


def _run_auto_venice(
    output: Path,
    evidence: dict,
    deadline_seconds: int,
    wait_seconds: int,
    *,
    allow_unfocused_venice: bool = False,
    max_unique: int = 64,
) -> dict:
    output_root = output.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    focus_bin = _require_focus_helper()
    fixture_digest = evidence["fixture_sha256"]
    assert fixture_digest is not None
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    if (launcher.get("exePath") != "./eu4.app/Contents/MacOS/eu4" or
            not isinstance(launcher.get("exeArgs"), list) or
            not all(isinstance(item, str) for item in launcher["exeArgs"])):
        raise LiveError("Unexpected GOG launcher executable or arguments")

    now = dt.datetime.now(dt.timezone.utc)
    run_dir = output_root / f"{now:%Y%m%dT%H%M%SZ}-gfx-live"
    run_dir.mkdir(parents=True, exist_ok=False)
    captures = run_dir / "captures"
    captures.mkdir()
    control = run_dir / "control.bin"
    control.write_bytes(bytes(CONTROL_BYTES))
    session_id = f"gfx-live-{now:%Y%m%dT%H%M%SZ}"
    probe_path = run_dir / "auto-probe.csv"
    metadata_path = run_dir / "metadata.json"
    metadata: dict = {
        "status": "launching",
        "acquisition_mode": "auto_venice",
        "started_utc": now.isoformat(),
        "session_id": session_id,
        "fixture_sha256": fixture_digest,
        "save_path": None,
        "save_sha256": fixture_digest,
        "capture_dir": str(run_dir),
        "control": str(control),
        "deadline_seconds": deadline_seconds,
        "wait_seconds": wait_seconds,
        "max_unique": max_unique,
        "acceptance_qualified": ACCEPTANCE,
        "gog": evidence["gog"],
        "descriptor_sha256": evidence["descriptor_sha256"],
        "descriptor_policy": evidence["descriptor_policy"],
        "code_window_sha256": evidence["code_window_sha256"],
        "patch_site_sha256": evidence["patch_site_sha256"],
        "library": evidence["library"],
        "library_sha256": evidence["library_sha256"],
        "source_sha256": evidence["source_sha256"],
        "probe_library": evidence["probe_library"],
        "probe_library_sha256": evidence["probe_library_sha256"],
        "focus_helper": str(focus_bin),
        "readiness_mode": (
            "strict_focus" if not allow_unfocused_venice
            else "allow_unfocused_venice"),
    }
    game: subprocess.Popen | None = None
    working_save: Path | None = None
    fixture_installed = False
    sealed = False
    report_result: dict | None = None
    readiness_diag: dict = {}
    with FixtureManager(output_root, base.USER_DATA, VENICE_FIXTURE,
                        fixture_digest) as fixture:
        fixture.recover()
        try:
            working_save = fixture.install()
            fixture_installed = True
            metadata["save_path"] = str(working_save)
            metadata["working_save"] = str(working_save)
            game_log = base.USER_DATA / "logs/game.log"
            old_log = game_log.read_bytes() if game_log.is_file() else b""
            anchor = {"monotonic_ns": time.monotonic_ns(), "wall_ns": time.time_ns()}
            metadata["readiness_anchor"] = anchor
            env = os.environ.copy()
            env.update({
                "DYLD_INSERT_LIBRARIES": f"{PROBE}:{LIBRARY}",
                "EU4_AUTO_PROBE_LOG": str(probe_path),
                "EU4_GFX_CAPTURE_DIR": str(run_dir),
                "EU4_GFX_CAPTURE_CONTROL": str(control),
                "EU4_GFX_CAPTURE_ID": session_id,
                "EU4_GFX_EXPECTED_EXE": str(base.GOG_EXE),
                "EU4_GFX_CAPTURE_DEADLINE_S": str(deadline_seconds),
                "EU4_GFX_CAPTURE_MAX_UNIQUE": str(max_unique),
            })
            with (run_dir / "game.stdout").open("wb") as stdout, (
                    run_dir / "game.stderr").open("wb") as stderr:
                game = subprocess.Popen(
                    ["./eu4", *launcher["exeArgs"], "--continuelastsave"],
                    cwd=base.GOG_EXE.parent, env=env, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr)
            metadata["game_pid"] = game.pid
            startup_path = run_dir / "startup.status"
            if not _wait_for(startup_path, 30, game):
                raise LiveError("The live dylib did not write startup attestation")
            _verify_startup_attestation(startup_path, session_id, game)
            readiness = wait_for_venice_ready(
                game, focus_bin, probe_path, anchor, game_log, old_log,
                diagnostics=readiness_diag,
                require_focus=not allow_unfocused_venice)
            metadata["readiness"] = readiness
            metadata["readiness_diagnostics"] = readiness_diag
            metadata["game_settings"] = base.game_settings()
            metadata["display_mode"] = base.display_mode()
            process_verified = (
                game.poll() is None
                and base.running_game_pid("paused") == game.pid
            )
            readiness["game_process_verified"] = process_verified
            if not process_verified:
                raise LiveError("The expected GOG EU IV process is not running")
            metadata["armed_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
            _arm_and_seal_capture(control, game, run_dir, captures,
                                  wait_seconds, deadline_seconds, max_unique)
            sealed = True
        except (LiveError, base.BenchmarkError, OSError, KeyboardInterrupt) as exc:
            metadata["status"] = "incomplete"
            metadata["error"] = str(exc)
            if readiness_diag:
                metadata["readiness_diagnostics"] = readiness_diag
            try:
                _write_control(control, 2)
            except OSError:
                pass
        finally:
            metadata["game_launched"] = game is not None
            try:
                _terminate_owned_game(game, focus_bin)
            except BaseException as term_exc:
                metadata["terminate_error"] = str(term_exc)

            launched_exited = game is not None and game.poll() is not None
            metadata["game_exited"] = launched_exited
            safe_to_restore = game is None or launched_exited

            save_verified = False
            if working_save is not None and working_save.is_file():
                try:
                    after_hash = base.sha256(working_save)
                    metadata["save_after_sha256"] = after_hash
                    save_verified = after_hash == fixture_digest
                    if not save_verified:
                        metadata["save_mismatch"] = True
                except OSError as exc:
                    metadata["save_hash_error"] = str(exc)
            elif working_save is not None and safe_to_restore:
                metadata["save_hash_error"] = "working save missing before verification"

            metadata["continue_game_restored"] = False
            metadata["fixture_cleanup_completed"] = False
            metadata["working_save_changed"] = False
            if safe_to_restore and fixture_installed and working_save is not None:
                try:
                    metadata["working_save_changed"] = fixture.finish(run_dir)
                    restored, completed = _fixture_cleanup_state(
                        fixture, working_save)
                    metadata["continue_game_restored"] = restored
                    metadata["fixture_cleanup_completed"] = completed
                except BaseException as cleanup_exc:
                    metadata["cleanup_error"] = str(cleanup_exc)
            elif not safe_to_restore:
                metadata["cleanup_error"] = (
                    "EU IV remained running; recovery journal was retained")

            cleanup_ok = (
                metadata.get("continue_game_restored") is True
                and metadata.get("fixture_cleanup_completed") is True
            )
            success = (
                sealed
                and launched_exited
                and cleanup_ok
                and save_verified
                and not metadata.get("working_save_changed", False)
            )
            metadata["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
            metadata["capture_files"] = len(list(captures.glob("*.capture")))
            metadata["status"] = "captured" if success else "incomplete"
            if not success and not metadata.get("error"):
                if not save_verified:
                    metadata.setdefault(
                        "error", "Working save was not verified against fixture")
                elif not cleanup_ok:
                    metadata.setdefault(
                        "error", "Fixture cleanup did not complete")
            metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
            if success:
                print(f"Sealed session: {run_dir}")
                report_result = report(run_dir)
                print(json.dumps(report_result, indent=2))
    if report_result is None:
        raise LiveError(_auto_venice_failure_message(metadata))
    return report_result


def run(output: Path, save: Path | None = None, deadline_seconds: int = 120,
        wait_seconds: int = 90, authorized: bool = False,
        auto_venice: bool = False,
        allow_unfocused_venice: bool = False,
        max_unique: int = 64) -> dict:
    if not authorized:
        raise LiveError("Live launch requires the explicit --authorize-live flag")
    if auto_venice and save is not None:
        raise LiveError("--auto-venice cannot be combined with --save")
    if allow_unfocused_venice and not auto_venice:
        raise LiveError(
            "--allow-unfocused-venice requires --auto-venice")
    max_unique = validate_max_unique(max_unique)
    evidence = preflight(save, auto_venice=auto_venice)
    if auto_venice:
        return _run_auto_venice(
            output, evidence, deadline_seconds, wait_seconds,
            allow_unfocused_venice=allow_unfocused_venice,
            max_unique=max_unique)
    focus_bin, focus_build_error = _try_build_focus_helper()
    save_path = Path(evidence["save"])
    save_hash = evidence["save_sha256"]
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    if (launcher.get("exePath") != "./eu4.app/Contents/MacOS/eu4" or
            not isinstance(launcher.get("exeArgs"), list) or
            not all(isinstance(item, str) for item in launcher["exeArgs"])):
        raise LiveError("Unexpected GOG launcher executable or arguments")

    now = dt.datetime.now(dt.timezone.utc)
    run_dir = output.expanduser().resolve() / f"{now:%Y%m%dT%H%M%SZ}-gfx-live"
    run_dir.mkdir(parents=True, exist_ok=False)
    captures = run_dir / "captures"
    captures.mkdir()
    control = run_dir / "control.bin"
    control.write_bytes(bytes(CONTROL_BYTES))
    session_id = f"gfx-live-{now:%Y%m%dT%H%M%SZ}"
    metadata_path = run_dir / "metadata.json"
    metadata = {
        "status": "launching",
        "acquisition_mode": "manual",
        "started_utc": now.isoformat(),
        "session_id": session_id,
        "save_path": str(save_path),
        "save_sha256": save_hash,
        "capture_dir": str(run_dir),
        "control": str(control),
        "deadline_seconds": deadline_seconds,
        "wait_seconds": wait_seconds,
        "max_unique": max_unique,
        "acceptance_qualified": ACCEPTANCE,
        "gog": evidence["gog"],
        "descriptor_sha256": evidence["descriptor_sha256"],
        "descriptor_policy": evidence["descriptor_policy"],
        "code_window_sha256": evidence["code_window_sha256"],
        "patch_site_sha256": evidence["patch_site_sha256"],
        "library": evidence["library"],
        "library_sha256": evidence["library_sha256"],
        "source_sha256": evidence["source_sha256"],
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    backup = base.backup_save(save_path, run_dir, save_hash)
    metadata["save_backup"] = str(backup)

    env = os.environ.copy()
    env.update({
        "DYLD_INSERT_LIBRARIES": str(LIBRARY),
        "EU4_GFX_CAPTURE_DIR": str(run_dir),
        "EU4_GFX_CAPTURE_CONTROL": str(control),
        "EU4_GFX_CAPTURE_ID": session_id,
        "EU4_GFX_EXPECTED_EXE": str(base.GOG_EXE),
        "EU4_GFX_CAPTURE_DEADLINE_S": str(deadline_seconds),
        "EU4_GFX_CAPTURE_MAX_UNIQUE": str(max_unique),
    })
    game: subprocess.Popen | None = None
    try:
        with (run_dir / "game.stdout").open("wb") as stdout, (
                run_dir / "game.stderr").open("wb") as stderr:
            game = subprocess.Popen(
                ["./eu4", *launcher["exeArgs"]], cwd=base.GOG_EXE.parent,
                env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        metadata["game_pid"] = game.pid
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        startup_path = run_dir / "startup.status"
        if not _wait_for(startup_path, 30, game):
            raise LiveError("The live dylib did not write startup attestation")
        _verify_startup_attestation(startup_path, session_id, game)

        print("The live hook is installed and the process is attested.", flush=True)
        print(
            "Load the disposable Venice save and leave the game paused. "
            "Capture remains DISARMED until you return and enter yes.",
            flush=True,
        )
        print(
            "If EU IV is not visible, use the Dock or Mission Control; "
            "Terminal.app may work better than an integrated terminal.",
            flush=True,
        )
        focus_attempted = False
        focus_ok = False
        focus_activate_error: str | None = None
        if focus_bin is not None:
            focus_attempted, focus_ok, focus_activate_error = _request_focus_activation(
                focus_bin, game.pid)
            if focus_ok:
                print("EU IV foreground activation requested.", flush=True)
        metadata["focus_activate_attempted"] = focus_attempted
        metadata["focus_activate_ok"] = focus_ok
        if focus_build_error:
            metadata["focus_helper_build_error"] = focus_build_error
        if focus_activate_error:
            metadata["focus_activate_error"] = focus_activate_error
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        if base.prompt("Confirm the expected Venice save is loaded and paused (yes/no): ",
                       True).lower() != "yes":
            raise LiveError("Game state was not confirmed; no live capture was made")
        if game.poll() is not None or base.running_game_pid("paused") != game.pid:
            raise LiveError("The expected GOG EU IV process is not running")
        metadata["armed_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        _arm_and_seal_capture(control, game, run_dir, captures,
                              wait_seconds, deadline_seconds, max_unique)

        after_hash = base.sha256(save_path)
        metadata["save_after_sha256"] = after_hash
        if after_hash != save_hash:
            raise LiveError("Disposable save changed during the live acquisition")
        metadata["status"] = "captured"
        metadata["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        metadata["capture_files"] = len(list(captures.glob("*.capture")))
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        print(f"Sealed session: {run_dir}")
        base.prompt("Quit EU IV normally now; press Enter after the process has exited. ")
        report_result = report(run_dir)
        print(json.dumps(report_result, indent=2))
        return report_result
    except (LiveError, base.BenchmarkError, OSError, KeyboardInterrupt) as exc:
        metadata["status"] = "incomplete"
        metadata["error"] = str(exc)
        metadata["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        try:
            _write_control(control, 2)
        except OSError:
            pass
        if game is not None and game.poll() is None:
            print("EU IV is still running. Quit it normally; the dylib is not unloaded.")
        raise


def _relpath(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def validate_max_unique(value: int) -> int:
    if (not isinstance(value, int) or isinstance(value, bool) or
            value < 1 or value > MAX_UNIQUE_LIMIT):
        raise LiveError(
            f"--max-unique must be an integer from 1 to {MAX_UNIQUE_LIMIT}")
    return value


def canonical_observation(parsed: dict) -> bytes:
    """Rebuild ``eu4-exact-dedup-canon/v1`` from one parsed transcript."""
    name = parsed["capture"]["function"].encode("utf-8")
    out = bytearray(b"EXDEDUP1")
    out += struct.pack("<H", len(name))
    out += name
    for carrier in ("RDI", "RSI", "RCX", "R9"):
        out += struct.pack("<Q", parsed["carriers"][carrier]["value"])
    regions = {(item["name"], item["phase"]): item for item in parsed["regions"]}
    out.append(1 if ("buffer", "pre") in regions else 0)
    for region_name in CANON_REGIONS:
        pre = regions.get((region_name, "pre"))
        post = regions.get((region_name, "post"))
        if pre is None:
            out.append(0)
            continue
        blob = pre["bytes"]
        out.append(1)
        out += struct.pack("<QH", pre["address"], len(blob))
        out += blob
        out += post["bytes"]
    out += struct.pack("<Q", parsed["outcome"]["value"])
    return bytes(out)


def report_exact(run_dir: Path) -> dict:
    """Check an exact-dedup summary. Retained transcripts must be distinct.

    Discarded observations are not on disk, so this cannot prove they were
    duplicates. Historical v1 sessions never enter this function.
    """
    run_dir = run_dir.expanduser().resolve()
    problems: list[str] = []

    def add_problem(message: str) -> None:
        problems.append(message)

    try:
        summary = _parse_table(run_dir / "summary.native", EXACT_SUMMARY)
    except (LiveError, OSError) as exc:
        return {"status": "failed", "qualified": 0, "unqualified": 0,
                "malformed": [], "problems": [str(exc)]}
    try:
        done = _parse_table(run_dir / "session.done", DONE_HEADER)
    except (LiveError, OSError) as exc:
        done = None
        add_problem(str(exc))
    startup_path = run_dir / "startup.status"
    if startup_path.is_file():
        try:
            startup = _parse_table(startup_path, STARTUP_HEADER)
            if startup.get("max_unique") != summary.get("max_unique"):
                add_problem("Startup max_unique does not match the summary")
        except LiveError as exc:
            add_problem(str(exc))

    keys = (
        "max_unique", "intercepted", "not_admitted", "not_admitted_busy",
        "not_admitted_slot", "not_admitted_pointer", "not_admitted_address",
        "not_admitted_closed", "not_admitted_thread_limit", "buffer_refused",
        "complete_observations", "unique_retained", "duplicates_discarded",
        "unique_limit_discarded", "storage_failed", "incomplete_observations",
        "undrained_observations")
    values: dict[str, int] = {}
    try:
        values = {key: _integer(summary, key) for key in keys}
    except LiveError as exc:
        add_problem(str(exc))
    if values:
        if not 1 <= values["max_unique"] <= MAX_UNIQUE_LIMIT:
            add_problem("max_unique is outside 1..4096")
        if values["unique_retained"] > values["max_unique"]:
            add_problem("unique_retained exceeds max_unique")
        complete = (values["unique_retained"] + values["duplicates_discarded"] +
                    values["unique_limit_discarded"] + values["storage_failed"])
        if values["complete_observations"] != complete:
            add_problem("complete_observations does not match its parts")
        intercepted = (values["not_admitted"] + values["buffer_refused"] +
                       values["incomplete_observations"] + complete +
                       values["undrained_observations"])
        if values["intercepted"] != intercepted:
            add_problem("intercepted does not match its parts")
        refusals = (values["not_admitted_busy"] + values["not_admitted_slot"] +
                    values["not_admitted_pointer"] +
                    values["not_admitted_address"] +
                    values["not_admitted_closed"] +
                    values["not_admitted_thread_limit"])
        if values["not_admitted"] != refusals:
            add_problem("not_admitted does not match its parts")
        if values["undrained_observations"] != 0 or summary.get("state") != "sealed":
            add_problem("Session is not cleanly sealed")
        if summary.get("quiescence") != "ok":
            add_problem("Quiescence timed out")
    if done is not None and done.get("state") != summary.get("state"):
        add_problem("Done marker does not match the summary state")
    if list(run_dir.rglob("*.partial")):
        add_problem("A partial file survived the session")

    fingerprints: list[bytes] = []
    malformed: list[dict] = []
    captures = run_dir / "captures"
    files = sorted(captures.glob("*.capture")) if captures.is_dir() else []
    expected = values.get("unique_retained")
    if expected is not None and len(files) != expected:
        add_problem("Retained file count does not match unique_retained")
    indexes: set[int] = set()
    session = summary.get("session")
    for path in files:
        prefix = f"{session}."
        stem = path.name[:-len(".capture")]
        ticket_text = stem[len(prefix):] if stem.startswith(prefix) else ""
        if (not ticket_text.isascii() or not ticket_text.isdigit() or
                str(int(ticket_text)) != ticket_text):
            malformed.append({"file": path.name, "error": "Unexpected filename"})
            continue
        indexes.add(int(ticket_text))
        try:
            parsed = fixture.parse_capture(path.read_text(encoding="utf-8"))
            fingerprints.append(canonical_observation(parsed))
        except (OSError, fixture.FixtureError, ValueError, struct.error) as exc:
            malformed.append({"file": path.name, "error": str(exc)})
    if expected is not None and indexes != set(range(expected)):
        add_problem("Retained filenames are not 0..unique_retained-1")
    if len(fingerprints) != len(set(fingerprints)):
        add_problem("Retained canonical observations are not mutually distinct")
    for item in malformed:
        add_problem(f"Malformed transcript {item['file']}: {item['error']}")

    status = "accepted" if not problems else "failed"
    return {"status": status, "qualified": len(fingerprints),
            "unqualified": 0, "malformed": malformed, "problems": problems,
            "max_unique": summary.get("max_unique"),
            "unique_retained": summary.get("unique_retained")}


def report(run_dir: Path) -> dict:
    run_dir = run_dir.expanduser().resolve()
    try:
        first = (run_dir / "summary.native").read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        first = ""
    if first == EXACT_SUMMARY:
        return report_exact(run_dir)
    problems: list[str] = []
    gates: dict[str, bool] = {"pids": False}
    summary: dict[str, str] | None = None
    startup: dict[str, str] | None = None
    metadata: dict = {}
    metadata_pid: int | None = None
    startup_pid: int | None = None
    native_pid: int | None = None
    transcripts: list[dict] = []
    qualified: list[dict] = []
    unqualified: list[dict] = []
    malformed: list[dict] = []
    file_hashes: dict[str, str] = {}

    def add_problem(message: str) -> None:
        problems.append(message)

    summary_path = run_dir / "summary.native"
    startup_path = run_dir / "startup.status"
    done_path = run_dir / "session.done"
    metadata_path = run_dir / "metadata.json"
    done: dict[str, str] | None = None
    try:
        summary = _parse_table(summary_path, SUMMARY_HEADER)
    except LiveError as exc:
        add_problem(str(exc))
    try:
        startup = _parse_table(startup_path, STARTUP_HEADER)
    except LiveError as exc:
        add_problem(str(exc))
    try:
        done = _parse_table(done_path, DONE_HEADER)
    except LiveError as exc:
        add_problem(str(exc))
    if metadata_path.is_file():
        try:
            parsed_metadata = json.loads(
                metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            add_problem(f"Malformed metadata.json: {exc}")
        else:
            if isinstance(parsed_metadata, dict):
                metadata = parsed_metadata
                game_pid = metadata.get("game_pid")
                if type(game_pid) is int and game_pid > 0:
                    metadata_pid = game_pid
                else:
                    add_problem("Launch metadata game_pid is missing or invalid")
            else:
                add_problem("Malformed metadata.json: expected a JSON object")
    else:
        add_problem("Missing metadata.json")

    session = summary.get("session") if summary else None
    descriptor = fixture.load_descriptor()
    descriptor_sha = descriptor["binary"]["sha256"]
    unique_calls: set[tuple[int, int]] = set()
    ticket_numbers: set[int] = set()
    capture_files = sorted((run_dir / "captures").glob("*.capture")) \
        if (run_dir / "captures").is_dir() else []
    for path in capture_files:
        if session is None:
            malformed.append({"file": _relpath(path, run_dir),
                              "error": "Native session is unavailable"})
            continue
        prefix = f"{session}."
        suffix = ".capture"
        name = path.name
        ticket_text = (
            name[len(prefix):-len(suffix)]
            if name.startswith(prefix) and name.endswith(suffix) else ""
        )
        if (not ticket_text or not ticket_text.isascii() or
                not ticket_text.isdigit() or
                str(int(ticket_text)) != ticket_text):
            malformed.append({"file": _relpath(path, run_dir),
                              "error": "Unexpected ticket filename"})
            continue
        ticket = int(ticket_text)
        if ticket < 0 or ticket >= QUOTA:
            malformed.append({"file": _relpath(path, run_dir),
                              "error": "Ticket number outside 0..63"})
            continue
        if ticket in ticket_numbers:
            malformed.append({"file": _relpath(path, run_dir),
                              "error": "Duplicate ticket filename"})
            continue
        ticket_numbers.add(ticket)
        try:
            text = path.read_text(encoding="utf-8")
            parsed = fixture.parse_capture(text)
            capture = parsed["capture"]
            if capture["synthetic"] is not False:
                raise LiveError("Transcript is synthetic")
            if session is None or capture["capture"] != session:
                raise LiveError("Transcript capture ID does not match the session")
            if capture["scenario"] not in SCENARIOS:
                raise LiveError(f"Unexpected live scenario: {capture['scenario']}")
            if capture["epoch"] != EPOCH:
                raise LiveError(f"Unexpected live epoch: {capture['epoch']}")
            call = (int(capture["thread"]), int(capture["sequence"]))
            if call in unique_calls:
                raise LiveError(f"Duplicate thread/sequence: {call}")
            unique_calls.add(call)
            materialized = fixture.materialize_capture(text, descriptor)
            verdict = materialized["manifest"]["verdict"]
            entry = {
                "file": _relpath(path, run_dir),
                "verdict": verdict,
                "thread": int(capture["thread"]),
                "sequence": int(capture["sequence"]),
                "scenario": capture["scenario"],
                "record_digest": materialized["manifest"].get("record_digest"),
            }
            transcripts.append(entry)
            if verdict == "qualified":
                qualified.append(entry)
            else:
                entry["missing"] = materialized["manifest"].get("missing")
                unqualified.append(entry)
        except Exception as exc:  # one bad transcript must not hide the others
            malformed.append({"file": _relpath(path, run_dir),
                              "error": f"{type(exc).__name__}: {exc}"})

    for item in malformed:
        add_problem(f"Malformed transcript {item['file']}: {item['error']}")

    gates["transcripts_parse"] = not malformed
    gates["live_metadata"] = not malformed
    gates["transcript_count"] = (
        summary is not None and
        summary.get("written") == str(len(capture_files))
    )
    gates["quota"] = summary is not None and summary.get("quota") == str(QUOTA)
    gates["at_least_50"] = len(qualified) >= ACCEPTANCE

    if summary is not None:
        try:
            numeric_keys = (
                "intercepted", "not_admitted", "admitted", "incomplete",
                "completed", "written", "write_failed", "undrained",
                "reserved", "ready", "quota", "threads", "pid", "started_ns",
                "armed_ns", "closed_ns", "not_admitted_busy",
                "not_admitted_slot", "not_admitted_pointer",
                "not_admitted_address", "not_admitted_quota",
                "not_admitted_closed", "not_admitted_thread_limit")
            values = {key: _integer(summary, key) for key in numeric_keys}
            native_pid = values["pid"]
            for key, value in values.items():
                if value < 0:
                    add_problem(f"Native counter {key} is negative")
            if values["pid"] <= 0:
                add_problem("Native PID is not positive")
            if values["admitted"] > values["quota"]:
                add_problem("Native admitted exceeds quota")
            if values["threads"] > THREADS:
                add_problem("Native thread count exceeds the thread table")
            if values["armed_ns"] > 0 and values["threads"] == 0:
                add_problem("Native armed session has no registered threads")
            gates["numeric_invariants"] = (
                all(value >= 0 for value in values.values()) and
                values["pid"] > 0 and
                values["admitted"] <= values["quota"] and
                values["threads"] <= THREADS and
                (values["armed_ns"] == 0 or values["threads"] > 0)
            )

            started_ns = values["started_ns"]
            armed_ns = values["armed_ns"]
            closed_ns = values["closed_ns"]
            gates["epoch"] = (
                started_ns > 0 and armed_ns >= started_ns and closed_ns >= armed_ns
            )
            if not gates["epoch"]:
                add_problem("Native epoch timestamps are not monotonic and armed")
            reasons = sum(values[key] for key in (
                "not_admitted_busy", "not_admitted_slot",
                "not_admitted_pointer", "not_admitted_address",
                "not_admitted_quota", "not_admitted_closed",
                "not_admitted_thread_limit"))
            admitted = values["admitted"]
            incomplete = values["incomplete"]
            completed = values["completed"]
            written = values["written"]
            failed = values["write_failed"]
            undrained = values["undrained"]
            if values["not_admitted"] != reasons:
                add_problem("Native not-admitted total is inconsistent")
            if admitted != incomplete + completed:
                add_problem("Native admitted != incomplete + completed")
            if completed != written + failed + undrained:
                add_problem("Native completed != written + write_failed + undrained")
            if values["intercepted"] != admitted + reasons:
                add_problem("Native intercepted != admitted + not_admitted")
            if values["ready"] != 0 or values["reserved"] != 0:
                add_problem("Native session retained READY or RESERVED tickets")
            if written != len(capture_files):
                add_problem("Native written count does not match transcript files")
            if len({entry["thread"] for entry in transcripts}) > values["threads"]:
                add_problem("Distinct transcript threads exceed the native thread table")
            gates["native_reconciled"] = not any(
                "Native " in problem for problem in problems)
        except LiveError as exc:
            add_problem(str(exc))
            gates["native_reconciled"] = False
            gates["numeric_invariants"] = False
            gates["epoch"] = False

        state = summary.get("state")
        quiescence = summary.get("quiescence")
        if state == "partial":
            add_problem("Native session is a partial diagnostic, not an accepted seal")
            if quiescence != "timeout":
                add_problem("Partial native session is not a quiescence timeout")
        elif state != "sealed":
            add_problem(f"Unexpected native state: {state!r}")
        elif quiescence != "ok":
            add_problem(f"Unexpected native quiescence: {quiescence!r}")

    gates["done_sealed"] = done is not None and done.get("state") == "sealed"
    if done is not None:
        if done.get("session") != session:
            add_problem("Session-done marker does not match the native summary")
        if summary is not None and done.get("state") != summary.get("state"):
            add_problem("Session-done state does not match the native summary")
        if done.get("state") not in {"sealed", "partial"}:
            add_problem(f"Session-done state is {done.get('state')!r}")

    if startup is not None:
        gates["startup_installed"] = startup.get("state") == "installed"
        if not gates["startup_installed"]:
            add_problem(f"Dylib startup failed: {startup.get('error') or 'unknown'}")
        if session is not None and startup.get("session") != session:
            add_problem("Startup session does not match the native summary")
        if startup.get("arch") != "x86_64":
            add_problem("Startup architecture is not x86_64")
        if startup.get("module") != descriptor_sha:
            add_problem("Startup module digest does not match the descriptor")
        try:
            image_base = _integer(startup, "image_base", hexadecimal=True)
            site = _integer(startup, "site", hexadecimal=True)
            if site != image_base + TARGET_RVA:
                add_problem("Startup site is not image_base + pinned RVA")
            startup_pid = _integer(startup, "pid")
            if startup_pid <= 0:
                add_problem("Startup PID is not positive")
        except (LiveError, TypeError, ValueError) as exc:
            add_problem(f"Startup address/PID check failed: {exc}")
        executable = startup.get("executable")
        if not executable or Path(executable).resolve() != base.GOG_EXE.resolve():
            add_problem("Startup executable does not match the GOG executable")
    else:
        gates["startup_installed"] = False

    if (metadata_pid is not None and startup_pid is not None and
            native_pid is not None and
            metadata_pid > 0 and startup_pid > 0 and native_pid > 0):
        if metadata_pid == startup_pid == native_pid:
            gates["pids"] = True
        else:
            add_problem("Launch, startup and native PIDs do not match")

    if metadata:
        if metadata.get("error"):
            add_problem(f"Launch metadata reports an error: {metadata['error']}")
        if metadata.get("status") != "captured":
            add_problem(f"Launch metadata status is {metadata.get('status')!r}")
        if metadata.get("session_id") != session:
            add_problem("Metadata session ID does not match the native summary")
        if metadata.get("capture_files") != len(capture_files):
            add_problem("Metadata capture-file count does not match the transcripts")
        save_before = metadata.get("save_sha256")
        save_after = metadata.get("save_after_sha256")
        if not save_before or not save_after or save_before != save_after:
            add_problem("Disposable save was not proven unchanged")
        else:
            gates["save_unchanged"] = True
        if metadata.get("descriptor_sha256") != base.sha256(
                ROOT / "oracle/function/descriptors/gfx_set_constant_buffer.json"):
            add_problem("Descriptor digest changed after the run")
        if LIBRARY.is_file() and metadata.get("library_sha256") != base.sha256(LIBRARY):
            add_problem("Live capture library changed after the run")
    else:
        gates["save_unchanged"] = False

    loss = {
        "write_failed": _optional_integer(summary, "write_failed"),
        "undrained": _optional_integer(summary, "undrained"),
        "incomplete": _optional_integer(summary, "incomplete"),
    }
    gates["no_malformed"] = not malformed
    gates["no_loss"] = (
        loss["write_failed"] == 0 and loss["undrained"] == 0
        if summary else False
    )
    if summary is not None and (loss["write_failed"] or loss["undrained"]):
        add_problem(
            "Native session lost persisted captures; strict acceptance "
            "requires zero write_failed and undrained"
        )

    for path in (summary_path, startup_path, metadata_path,
                 run_dir / "session.done"):
        if path.is_file():
            file_hashes[_relpath(path, run_dir)] = base.sha256(path)
    for path in capture_files:
        file_hashes[_relpath(path, run_dir)] = base.sha256(path)

    if any(problem for problem in problems
           if "partial diagnostic" not in problem):
        status = "failed"
    elif summary is not None and summary.get("state") == "partial":
        status = "partial"
    elif len(qualified) >= ACCEPTANCE and all(gates.values()):
        status = "accepted"
    else:
        status = "insufficient"

    manifest = {
        "schema": "eu4-gfx-live-manifest/v1",
        "session": session,
        "status": status,
        "files": file_hashes,
        "descriptor_sha256": base.sha256(
            ROOT / "oracle/function/descriptors/gfx_set_constant_buffer.json"),
        "library_sha256": base.sha256(LIBRARY) if LIBRARY.is_file() else None,
    }
    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            existing_manifest = json.loads(
                manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            add_problem(f"Existing manifest is unreadable: {exc}")
            status = "failed"
        else:
            if existing_manifest != manifest:
                add_problem(
                    "Existing manifest does not match the current evidence")
                status = "failed"
    else:
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    clean = status == "accepted" and all(gates.values())

    result = {
        "schema": "eu4-gfx-live-session/v1",
        "run_dir": str(run_dir),
        "session": session,
        "status": status,
        "clean": clean,
        "qualified": len(qualified),
        "unqualified": len(unqualified),
        "malformed": malformed,
        "problems": problems,
        "gates": gates,
        "loss": loss,
        "native": summary,
        "startup": startup,
        "transcripts": transcripts,
        "qualified_transcripts": qualified,
        "unqualified_transcripts": unqualified,
    }
    (run_dir / "session.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Live _GfxSetConstantBuffer capture",
        "",
        f"- Status: **{status}** (clean={str(clean).lower()})",
        f"- Session: `{session}`",
        f"- Qualified A2 fixtures: **{len(qualified)}** (target {ACCEPTANCE})",
        f"- Unqualified transcripts: {len(unqualified)}",
        f"- Malformed transcripts: {len(malformed)}",
        f"- Loss: write_failed={loss['write_failed']}, "
        f"undrained={loss['undrained']}, incomplete={loss['incomplete']}",
        "",
        "## Gates",
        "",
    ]
    lines.extend(f"- {name}: {'pass' if passed else 'FAIL'}"
                 for name, passed in sorted(gates.items()))
    if problems:
        lines.extend(["", "## Problems", ""])
        lines.extend(f"- {problem}" for problem in problems)
    lines.extend([
        "",
        "The native summary is the accounting source; transcripts are immutable "
        "after sealing. Qualified fixtures still close through the A2 descriptor "
        "and PR C replay contract. Raw transcripts are not committed by default.",
        "",
    ])
    (run_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    preflight_parser = commands.add_parser("preflight")
    preflight_parser.add_argument("--save", type=Path, default=None)
    preflight_parser.add_argument("--auto-venice", action="store_true")

    run_parser = commands.add_parser("run")
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--save", type=Path, default=None)
    run_parser.add_argument("--deadline", type=int, default=120)
    run_parser.add_argument("--max-unique", type=int, default=64)
    run_parser.add_argument("--wait", type=int, default=90)
    run_parser.add_argument("--authorize-live", action="store_true")
    run_parser.add_argument("--auto-venice", action="store_true")
    run_parser.add_argument("--allow-unfocused-venice", action="store_true")

    report_parser = commands.add_parser("report")
    report_parser.add_argument("--run-dir", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            print(json.dumps(preflight(args.save, auto_venice=args.auto_venice),
                             indent=2))
            return 0
        if args.command == "run":
            result = run(args.output, args.save, args.deadline, args.wait,
                         args.authorize_live, args.auto_venice,
                         args.allow_unfocused_venice,
                         max_unique=args.max_unique)
            return 0 if result["status"] == "accepted" else 1
        result = report(args.run_dir)
        print(json.dumps({
            "status": result["status"],
            "qualified": result["qualified"],
            "unqualified": result["unqualified"],
            "malformed": len(result["malformed"]),
            "problems": result["problems"],
        }, indent=2))
        return 0 if result["status"] == "accepted" else 1
    except (LiveError, base.BenchmarkError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
