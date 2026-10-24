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

import eu4_benchmark as base  # noqa: E402
from oracle.function import fixture  # noqa: E402
from reverse_engineering import paths  # noqa: E402
from reverse_engineering.macho_rva import (  # noqa: E402
    MachOMapper,
    assert_pinned_executable,
)

BENCH = ROOT / "benchmark"
BUILD = BENCH / ".build"
LIBRARY = BUILD / "libeu4_gfx_live_capture.dylib"
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
STARTUP_HEADER = "EU4-GFX-LIVE-STARTUP v1"
DONE_HEADER = "EU4-GFX-LIVE-DONE v1"


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


def preflight(save: Path | None = None) -> dict:
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

    chosen_save = (save or base.DEFAULT_SAVE).expanduser()
    if not chosen_save.is_file():
        raise LiveError(f"Disposable Venice save is missing: {chosen_save}")
    return {
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
        "save": str(chosen_save),
        "save_sha256": base.sha256(chosen_save),
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


def run(output: Path, save: Path | None = None, deadline_seconds: int = 120,
        wait_seconds: int = 90, authorized: bool = False) -> dict:
    if not authorized:
        raise LiveError("Live launch requires the explicit --authorize-live flag")
    evidence = preflight(save)
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
        "started_utc": now.isoformat(),
        "session_id": session_id,
        "save_path": str(save_path),
        "save_sha256": save_hash,
        "capture_dir": str(run_dir),
        "control": str(control),
        "deadline_seconds": deadline_seconds,
        "wait_seconds": wait_seconds,
        "quota": QUOTA,
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
    })
    game: subprocess.Popen | None = None
    try:
        with (run_dir / "game.stdout").open("wb") as stdout, (
                run_dir / "game.stderr").open("wb") as stderr:
            game = subprocess.Popen(
                ["./eu4", *launcher["exeArgs"]], cwd=base.GOG_EXE.parent,
                env=env, stdout=stdout, stderr=stderr)
        metadata["game_pid"] = game.pid
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        startup_path = run_dir / "startup.status"
        if not _wait_for(startup_path, 30, game):
            raise LiveError("The live dylib did not write startup attestation")
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

        print("The live hook is installed and the process is attested.")
        print("Load the disposable Venice save, keep the game paused, and return here.")
        if base.prompt("Confirm the expected Venice save is loaded and paused (yes/no): ",
                       True).lower() != "yes":
            raise LiveError("Game state was not confirmed; no live capture was made")
        if game.poll() is not None or base.running_game_pid("paused") != game.pid:
            raise LiveError("The expected GOG EU IV process is not running")
        _write_control(control, 1)
        metadata["armed_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        print("Capture is armed. Quota is 64; the session closes on quota or deadline.")

        done = run_dir / "session.done"
        wait_limit = max(1, min(wait_seconds, deadline_seconds - 10))
        end = time.monotonic() + wait_limit
        while time.monotonic() < end:
            if done.is_file():
                break
            if len(list(captures.glob("*.capture"))) >= QUOTA:
                break
            if game.poll() is not None:
                break
            time.sleep(0.25)
        _write_control(control, 2)
        if not _wait_for(done, 15, game):
            raise LiveError("The live session did not seal before the drain deadline")
        if game.poll() is not None:
            raise LiveError("The game exited before the session was confirmed")

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


def report(run_dir: Path) -> dict:
    run_dir = run_dir.expanduser().resolve()
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

    run_parser = commands.add_parser("run")
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--save", type=Path, default=None)
    run_parser.add_argument("--deadline", type=int, default=120)
    run_parser.add_argument("--wait", type=int, default=90)
    run_parser.add_argument("--authorize-live", action="store_true")

    report_parser = commands.add_parser("report")
    report_parser.add_argument("--run-dir", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            print(json.dumps(preflight(args.save), indent=2))
            return 0
        if args.command == "run":
            result = run(args.output, args.save, args.deadline, args.wait,
                         args.authorize_live)
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
