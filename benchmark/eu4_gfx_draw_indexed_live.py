#!/usr/bin/env python3
"""One bounded _GfxDrawIndexed entry observation on the Venice save."""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import mmap
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

import eu4_benchmark as base  # noqa: E402
import eu4_gfx_live_capture as live  # noqa: E402
from fixture_manager import FixtureManager  # noqa: E402
from reverse_engineering.macho_rva import assert_pinned_executable  # noqa: E402

BUILD = BENCH / ".build"
LIBRARY = BUILD / "libeu4_gfx_draw_indexed_observe.dylib"
TARGET_RVA = 0x15EAA77
SAMPLES = 64
OBSERVE_WINDOW_S = 60
CONTROL_BYTES = 4096
STARTUP_HEADER = "EU4-GFX-DRAW-STARTUP v1"
SAMPLE_HEADER = "EU4-GFX-DRAW-OBSERVE v1"
SOURCES = (
    BENCH / "eu4_gfx_capture_detour.c",
    BENCH / "eu4_gfx_capture_gateway.S",
    BENCH / "eu4_gfx_draw_indexed_observe.c",
    BENCH / "eu4_gfx_draw_indexed_observe_dylib.c",
)


def derive_selected(rsi: int, stored_count: int) -> int:
    """Recompute the count _GfxDrawIndexed keeps: esi > 0, otherwise context+0x68."""
    esi = ctypes.c_int32(rsi & 0xFFFFFFFF).value
    if esi > 0:
        return esi
    return ctypes.c_int32(stored_count & 0xFFFFFFFF).value


def classify(*, startup_ok: bool, ready: bool, receipt_ok: bool,
             post_ready: int, completed: int, refusals: int,
             process_exited: bool, save_verified: bool,
             continue_restored: bool, working_save_removed: bool,
             cleanup_ok: bool) -> str:
    if not (startup_ok and ready and receipt_ok and process_exited and
            save_verified and continue_restored and working_save_removed and
            cleanup_ok):
        return "probe-failed"
    if post_ready == 0:
        return "not-observed"
    retained = min(post_ready, SAMPLES)
    if refusals or completed < retained:
        return "partial"
    if completed >= 1:
        return "observed"
    return "probe-failed"


def build() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    command = [
        "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
        "-mno-avx", "-I", str(BENCH), "-dynamiclib", "-o", str(LIBRARY),
        *(str(source) for source in SOURCES),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise live.LiveError(
            f"Could not build draw observer: {result.stderr.strip() or result.stdout.strip()}")
    if base.command("lipo", "-archs", str(LIBRARY)).strip() != "x86_64":
        raise live.LiveError("Draw observer is not x86_64")


def _write_word(path: Path, offset: int, value: int) -> None:
    with path.open("r+b") as source:
        with mmap.mmap(source.fileno(), CONTROL_BYTES) as page:
            struct.pack_into("<Q", page, offset, value)
            page.flush()


def _parse_samples(path: Path) -> tuple[dict[str, str], list[dict[str, int]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 3 or lines[0] != SAMPLE_HEADER or lines[-1] != "END":
        raise live.LiveError(f"Malformed sample envelope: {path.name}")
    header: dict[str, str] = {}
    samples: list[dict[str, int]] = []
    for line in lines[1:-1]:
        if line.startswith("sample "):
            fields = {}
            for item in line.split()[1:]:
                key, value = item.split("=", 1)
                fields[key] = int(value, 16) if value.startswith("0x") else int(value)
            samples.append(fields)
            continue
        key, value = line.split("=", 1)
        if not key or key in header:
            raise live.LiveError(f"Malformed sample header: {line!r}")
        header[key] = value
    return header, samples


def _receipt_text(outcome: str, header: dict[str, str],
                  samples: list[dict[str, int]], extra: dict[str, str]) -> str:
    lines = ["EU4-GFX-DRAW-RECEIPT v1", f"outcome={outcome}"]
    for key in ("hits", "post_ready", "completed", "refusals",
                "uniform_nonzero", "attrib_nonzero"):
        if key in header:
            lines.append(f"{key}={header[key]}")
    for key, value in extra.items():
        lines.append(f"{key}={value}")
    for sample in samples:
        parts = [f"{key}={value if key in ('context_ok', 'refusal') else hex(value)}"
                 for key, value in sample.items()]
        if sample.get("context_ok") == 1:
            selected = derive_selected(sample["rsi"], sample["stored_count"])
            parts.append(f"selected={selected}")
        lines.append("sample " + " ".join(parts))
    lines.append("END")
    return "\n".join(lines) + "\n"


def run(output: Path, *, authorize_live: bool) -> dict:
    if not authorize_live:
        raise live.LiveError("Refusing to launch EU IV without --authorize-live")
    output_root = output.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    assert_pinned_executable(base.GOG_EXE)
    base.running_game_pid("idle")
    build()
    live._build_probe_dylib()
    focus_bin = live._require_focus_helper()
    fixture_info = live._venice_fixture_authority()
    digest = fixture_info["fixture_sha256"]
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    if (launcher.get("exePath") != "./eu4.app/Contents/MacOS/eu4" or
            not isinstance(launcher.get("exeArgs"), list) or
            not all(isinstance(item, str) for item in launcher["exeArgs"])):
        raise live.LiveError("Unexpected GOG launcher executable or arguments")
    now = dt.datetime.now(dt.timezone.utc)
    run_dir = output_root / f"{now:%Y%m%dT%H%M%SZ}-gfx-draw-indexed"
    run_dir.mkdir(parents=True, exist_ok=False)
    control = run_dir / "control.bin"
    control.write_bytes(bytes(CONTROL_BYTES))
    session_id = f"gfx-draw-{now:%Y%m%dT%H%M%SZ}"
    probe_path = run_dir / "auto-probe.csv"
    game: subprocess.Popen | None = None
    working_save: Path | None = None
    startup_ok = False
    ready = False
    receipt_ok = False
    error = ""
    header: dict[str, str] = {}
    samples: list[dict[str, int]] = []
    with FixtureManager(output_root, base.USER_DATA, live.VENICE_FIXTURE, digest) as fixture:
        fixture.recover()
        try:
            working_save = fixture.install()
            game_log = base.USER_DATA / "logs/game.log"
            old_log = game_log.read_bytes() if game_log.is_file() else b""
            anchor = {"monotonic_ns": time.monotonic_ns(), "wall_ns": time.time_ns()}
            env = os.environ.copy()
            env.update({
                "DYLD_INSERT_LIBRARIES": f"{live.PROBE}:{LIBRARY}",
                "EU4_AUTO_PROBE_LOG": str(probe_path),
                "EU4_GFX_DRAW_DIR": str(run_dir),
                "EU4_GFX_DRAW_CONTROL": str(control),
                "EU4_GFX_DRAW_ID": session_id,
                "EU4_GFX_EXPECTED_EXE": str(base.GOG_EXE),
            })
            with (run_dir / "game.stdout").open("wb") as stdout, (
                    run_dir / "game.stderr").open("wb") as stderr:
                game = subprocess.Popen(
                    ["./eu4", *launcher["exeArgs"], "--continuelastsave"],
                    cwd=base.GOG_EXE.parent, env=env, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr)
            startup_path = run_dir / "startup.status"
            if not live._wait_for(startup_path, 30, game):
                raise live.LiveError("The draw observer did not write startup attestation")
            startup = live._parse_table(startup_path, STARTUP_HEADER)
            if startup.get("state") != "installed":
                raise live.LiveError(
                    f"Draw observer startup failed: {startup.get('error') or 'unknown'}")
            if live._integer(startup, "pid") != game.pid:
                raise live.LiveError("Startup PID does not match the launched process")
            if live._integer(startup, "site", hexadecimal=True) != (
                    live._integer(startup, "image_base", hexadecimal=True) + TARGET_RVA):
                raise live.LiveError("Startup site is not _GfxDrawIndexed")
            startup_ok = True
            live.wait_for_venice_ready(
                game, focus_bin, probe_path, anchor, game_log, old_log)
            ready = True
            _write_word(control, 0, 1)
            deadline = time.monotonic() + OBSERVE_WINDOW_S
            ready_path = run_dir / "samples.ready"
            while time.monotonic() < deadline:
                if ready_path.is_file() or game.poll() is not None:
                    break
                time.sleep(0.25)
            _write_word(control, 0, 0)
            _write_word(control, 8, 1)
            flush_deadline = time.monotonic() + 5
            while time.monotonic() < flush_deadline and not ready_path.is_file():
                if game.poll() is not None:
                    break
                time.sleep(0.1)
            sample_path = run_dir / "samples.txt"
            if not sample_path.is_file():
                raise live.LiveError("The observer did not publish a sample file")
            header, samples = _parse_samples(sample_path)
            completed = int(header["completed"])
            refusals = int(header["refusals"])
            if completed != len(samples) or refusals != sum(
                    1 for sample in samples if sample.get("refusal")):
                raise live.LiveError("Sample file counts do not match its records")
            receipt_ok = True
        except (live.LiveError, base.BenchmarkError, OSError) as exc:
            error = str(exc)
        finally:
            try:
                live._terminate_owned_game(game, focus_bin)
            except BaseException as exc:
                error = error or str(exc)
            process_exited = game is not None and game.poll() is not None
            save_matches = False
            continue_restored = False
            working_save_removed = False
            cleanup_ok = False
            cleanup_reason = ""
            if game is not None and game.poll() is None:
                cleanup_reason = "process still running"
            elif working_save is not None:
                if working_save.is_file():
                    try:
                        save_matches = base.sha256(working_save) == digest
                    except OSError:
                        save_matches = False
                if process_exited and not save_matches:
                    cleanup_reason = "save hash mismatch"
                try:
                    fixture.finish(run_dir)
                except BaseException as exc:
                    error = error or str(exc)
                    cleanup_reason = cleanup_reason or "fixture cleanup did not complete"
                else:
                    continue_restored, _completed = live._fixture_cleanup_state(
                        fixture, working_save)
                    working_save_removed = not working_save.exists()
                    cleanup_ok = continue_restored and working_save_removed
                    if not cleanup_ok:
                        cleanup_reason = (
                            cleanup_reason or "fixture cleanup did not complete")
            if cleanup_reason:
                error = f"{error}; {cleanup_reason}" if error else cleanup_reason
            outcome = classify(
                startup_ok=startup_ok, ready=ready, receipt_ok=receipt_ok,
                post_ready=int(header.get("post_ready", "0")),
                completed=int(header.get("completed", "0")),
                refusals=int(header.get("refusals", "0")),
                process_exited=process_exited, save_verified=save_matches,
                continue_restored=continue_restored,
                working_save_removed=working_save_removed, cleanup_ok=cleanup_ok)
            extra = {
                "session": session_id,
                "save_verified": "1" if save_matches else "0",
                "error": error,
            }
            receipt = _receipt_text(outcome, header, samples, extra)
            (run_dir / "receipt.txt").write_text(receipt, encoding="utf-8")
            result = {
                "outcome": outcome,
                "run_dir": str(run_dir),
                "error": error,
                "save_verified": "1" if save_matches else "0",
            }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--authorize-live", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(args.output, authorize_live=args.authorize_live)
    except (live.LiveError, base.BenchmarkError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["outcome"] in {"observed", "not-observed", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
