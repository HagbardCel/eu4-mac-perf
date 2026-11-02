#!/usr/bin/env python3
"""Metal window boot. One callsite redirect, then the process exits. No CI launch."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import eu4_benchmark as base


SOURCE = Path(__file__).resolve().with_suffix(".m")
PATCH = SOURCE.with_name("eu4_metal_boot_patch.c")
ENTRY = SOURCE.with_name("eu4_gfx_entry_patch.c")
EVENTS = SOURCE.with_name("eu4_metal_events.c")
GL_DIAG = SOURCE.with_name("eu4_metal_gl_diag.c")
GFX = SOURCE.with_name("eu4_gfx_init.mm")
BUFFERS = SOURCE.with_name("eu4_gfx_buffers.mm")
DRAW_C = SOURCE.with_name("eu4_gfx_draw_capture.c")
DRAW_S = SOURCE.with_name("eu4_gfx_draw_entry.S")
TEXTURE = SOURCE.with_name("eu4_gfx_texture.mm")
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
        "clang", "-arch", arch, "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++", "-dynamiclib",
        "-framework", "AppKit", "-framework", "Foundation", "-framework", "Metal",
        "-framework", "QuartzCore", "-framework", "OpenGL",
        "-o", str(output), str(SOURCE), str(PATCH), str(ENTRY), str(EVENTS), str(GL_DIAG), str(GFX),
        str(BUFFERS), str(DRAW_C), str(DRAW_S), str(TEXTURE),
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
        "clang", "-arch", arch, "-O2", "-Wall", "-Wextra", "-DEU4_METAL_GL_NO_INTERPOSE",
        "-framework", "OpenGL",
        "-o", str(output), str(HOST_SOURCE), str(PATCH), str(ENTRY), str(EVENTS), str(GL_DIAG),
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


def read_event(path: Path) -> dict | None:
    """A missing, empty, or truncated file is not an event. Temp siblings are not read."""
    if path.name.endswith(".partial") or not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _trusted_pid(value: object, game_pid: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == game_pid


def _quarantine(path: Path) -> None:
    dest = path.with_name(path.name + ".untrusted")
    number = 0
    while dest.exists():
        number += 1
        dest = path.with_name(f"{path.name}.untrusted-{number}")
    path.rename(dest)


def session_event(path: Path, game_pid: int, *, legacy: bool = False) -> dict | None:
    """A live receipt counts only when its pid is the launched process.

    Legacy mode keeps a historical file that has no pid field. A pid that is present
    and wrong is still quarantined.
    """
    event = read_event(path)
    if event is None:
        return None
    if _trusted_pid(event.get("pid"), game_pid):
        return event
    if legacy and "pid" not in event:
        return event
    _quarantine(path)
    return None



DIAGNOSTIC_GRACE_SECONDS = 2
_GFX_ENTRY = re.compile(
    r'^GFX_ENTRY\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*'
    r'(0x[0-9a-fA-F]+)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*'
    r'"((?:\\x[0-9a-fA-F]{2})+)"\s*\)$'
)


def parse_gfx_entries(text: str) -> tuple[tuple[str, int, int, str, bytes], ...]:
    """One GFX_ENTRY record per line. A bad registry raises; nothing is skipped."""
    found = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line == "" or line.startswith("//"):
            continue
        match = _GFX_ENTRY.fullmatch(line)
        if match is None:
            raise ValueError(f"gfx registry line {number}")
        name, entry_text, end_text, fn, encoded = match.groups()
        prefix = bytes.fromhex(encoded.replace("\\x", ""))
        entry, end = int(entry_text, 16), int(end_text, 16)
        if not 5 <= len(prefix) <= 8 or end <= entry:
            raise ValueError(f"gfx registry line {number}")
        found.append((name, entry, end, fn, prefix))
    if not found:
        raise ValueError("gfx registry is empty")
    if len({item[0] for item in found}) != len(found):
        raise ValueError("duplicate gfx entry name")
    if len({item[1] for item in found}) != len(found):
        raise ValueError("duplicate gfx entry rva")
    if len({item[3] for item in found}) != len(found):
        raise ValueError("duplicate gfx replacement")
    spans = sorted(found, key=lambda item: item[1])
    for prev, nxt in zip(spans, spans[1:]):
        if nxt[1] < prev[2]:
            raise ValueError("overlapping gfx bodies")
    return tuple(found)


GFX_ENTRIES = parse_gfx_entries(SOURCE.with_name("eu4_gfx_entries.def").read_text())
# Half-open [entry, end) in install order. Ends come from the registry.
REPLACED_BODIES = tuple((entry, end) for _name, entry, end, _fn, _prefix in GFX_ENTRIES)


def parse_rva(value: object) -> int | None:
    """A return address. Unparseable, null, and non-positive values are not callers."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value, 0)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed > 0 else None


def caller_outside_replacements(value: object, bodies: tuple | None = None) -> bool:
    """The recorded address is the instruction after the call, so the call byte is rva - 1."""
    spans = REPLACED_BODIES if bodies is None else bodies
    rva = parse_rva(value)
    if rva is None or not spans:
        return False
    call = rva - 1
    return all(call < start or call >= end for start, end in spans)


def same_pinned_executable(image: object) -> bool:
    if not isinstance(image, str) or image == "":
        return False
    return Path(image).resolve(strict=False) == Path(base.GOG_EXE).resolve(strict=False)


def metal_init_should_stop(init_seen: bool, stop_seen: bool, exited: bool, deadline_hit: bool) -> str | None:
    """init.json alone does not stop the run."""
    del init_seen
    if stop_seen:
        return "stop"
    if exited:
        return "exit"
    if deadline_hit:
        return "deadline"
    return None


def classify_diagnostic_exit(exited_within_grace: bool, returncode: int | None) -> tuple[int | None, bool]:
    """A process that exits during the grace wait keeps its own status. Otherwise the launcher signals it."""
    if exited_within_grace:
        return returncode, False
    return returncode, True


def metal_init_outcome(init_success: bool, stop_phase: str | None, why: str | None, returncode: int | None,
                       init_thread: int | None, stop_thread: int | None, launcher_signaled: bool,
                       stop_reason: str | None = None, stop_image: str | None = None,
                       stop_rva: object = None, bodies: tuple | None = None) -> dict:
    """Exit code 2 is a diagnostic _exit. gl is the init-loop stop. blocked_at and draw are D1 terminals, not init success."""
    threads = type(init_thread) is int and init_thread > 0 and init_thread == stop_thread
    spans = REPLACED_BODIES if bodies is None else bodies
    terminal = (
        why == "stop" and stop_phase == "after_init" and init_success and returncode == 2
        and not launcher_signaled and threads
        and same_pinned_executable(stop_image) and caller_outside_replacements(stop_rva, spans)
    )
    if terminal and stop_reason == "gl":
        return {"status": "complete", "live_gfx_init": "returned", "raises": False}
    if terminal and stop_reason == "blocked_at":
        return {"status": "blocked_at", "live_gfx_init": "returned", "raises": False}
    if terminal and stop_reason == "draw":
        return {"status": "draw_reached", "live_gfx_init": "returned", "raises": False}
    if why == "exit" and returncode == 0 and init_success:
        return {"status": "clean_exit", "live_gfx_init": "postconditions", "raises": False}
    if why == "deadline" and init_success and stop_phase is None:
        return {"status": "deadline", "live_gfx_init": "postconditions", "raises": False}
    if init_success and (why == "exit" or why == "stop"):
        return {"status": "post_init_failure", "live_gfx_init": "postconditions", "raises": True}
    return {
        "status": "failed",
        "live_gfx_init": "postconditions" if init_success else "not_verified",
        "raises": True,
    }


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


def _cpu_time(pid: int) -> str:
    ran = subprocess.run(["ps", "-o", "time=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return ran.stdout.strip() if ran.returncode == 0 else ""


def _sample_hang(pid: int, run_dir: Path) -> None:
    """One short sample after init with no stop. A failure is recorded and not interpreted."""
    before = _cpu_time(pid)
    path = run_dir / "sample.txt"
    try:
        ran = subprocess.run(
            ["sample", str(pid), "3", "-file", str(path)],
            capture_output=True, text=True, timeout=45, check=False,
        )
        if ran.returncode != 0:
            path.write_text((ran.stderr or ran.stdout or "sample failed") + "\n")
    except (OSError, subprocess.TimeoutExpired) as exc:
        path.write_text(f"sample failed: {exc}\n")
    after = _cpu_time(pid)
    (run_dir / "sample-cpu.txt").write_text(f"cpu_time_before: {before}\ncpu_time_after: {after}\n")


def launch_session(run_dir: Path, wait_seconds: float = 180, bootstrap: bool = False,
                   metal_init: bool = False) -> dict:
    """Operator launch. a1 and bootstrap exit after their receipt. metal_init keeps running."""
    if metal_init and bootstrap:
        raise base.BenchmarkError("--metal-init and --bootstrap are mutually exclusive")
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
    if metal_init:
        env = dict(os.environ)
        env["EU4_METAL_BOOT_MODE"] = "metal_init"
        env["EU4_METAL_INIT_DIR"] = str(run_dir)
        env.pop("EU4_METAL_BOOT_RECEIPT", None)
    else:
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
    why = None
    launcher_signaled = False
    init_at = None
    sampled = False
    try:
        if metal_init:
            while True:
                exited = game.poll() is not None
                stop_seen = session_event(run_dir / "stop.json", game.pid) is not None
                init_seen = session_event(run_dir / "init.json", game.pid) is not None
                if init_seen and init_at is None:
                    init_at = time.monotonic()
                deadline_hit = time.monotonic() >= deadline
                why = metal_init_should_stop(init_seen, stop_seen, exited, deadline_hit)
                if why is not None:
                    break
                if (
                    not sampled
                    and init_at is not None
                    and not stop_seen
                    and game.poll() is None
                    and time.monotonic() - init_at >= 15
                ):
                    sampled = True
                    _sample_hang(game.pid, run_dir)
                time.sleep(0.2)
            if why == "stop" and game.poll() is None:
                try:
                    game.wait(timeout=DIAGNOSTIC_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    launcher_signaled = True
            elif why == "deadline" and game.poll() is None:
                launcher_signaled = True
        else:
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
    if metal_init:
        init = session_event(run_dir / "init.json", game.pid)
        stop = session_event(run_dir / "stop.json", game.pid)
        success = init is not None and init.get("success") == 1
        phase = None if stop is None else stop.get("phase")
        outcome = metal_init_outcome(
            success, phase, why, game.returncode,
            None if init is None else init.get("thread"),
            None if stop is None else stop.get("thread"),
            launcher_signaled,
            None if stop is None else stop.get("reason"),
            None if stop is None else stop.get("image"),
            None if stop is None else stop.get("rva"),
        )
        metadata = {
            "status": outcome["status"],
            "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "gog": identity,
            "pid": game.pid,
            "exit_code": game.returncode,
            "stop_reason": why,
            "phase": phase,
            "launcher_signaled": launcher_signaled,
            "probe_sha256": base.sha256(library),
            "live_gfx_init": outcome["live_gfx_init"],
            "gfx_entry_ranges": [[entry, end] for _name, entry, end, _fn, _prefix in GFX_ENTRIES],
        }
        (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        if outcome["raises"]:
            raise base.BenchmarkError(
                f"metal_init did not reach a successful init: stop={why} phase={phase} "
                f"exit={game.returncode}"
            )
        return init
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
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--bootstrap", action="store_true")
    group.add_argument("--metal-init", action="store_true")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        launch_session(args.run_dir, bootstrap=args.bootstrap, metal_init=args.metal_init)
    except base.BenchmarkError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
