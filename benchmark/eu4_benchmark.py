#!/usr/bin/env python3
"""Reproducible, manually staged power measurements for GOG EU IV on macOS."""

from __future__ import annotations

import argparse
import csv
import ctypes
import datetime as dt
import hashlib
import json
import plistlib
import re
import shutil
import statistics
from collections import Counter
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GOG_ROOT = Path("/Applications/EuropaUniversalisIV")
GOG_EXE = GOG_ROOT / "eu4.app/Contents/MacOS/eu4"
GOG_INFO = GOG_ROOT / "Contents/Resources/goggame-2057001589.info"
GOG_LAUNCHER = GOG_ROOT / "launcher-settings.json"
USER_DATA = Path.home() / "Documents/Paradox Interactive/Europa Universalis IV GOG"
DEFAULT_SAVE = USER_DATA / "save games/Venice1444_11_11.eu4"
SCENARIOS = ("idle", "paused", "speed1", "speed3", "speed5")
CSV_FIELDS = ("sample", "timestamp", "interval_s", "cpu_w", "gpu_w", "combined_w", "gpu_active_pct", "thermal_pressure")


class BenchmarkError(Exception):
    pass


def command(*args: str, check: bool = True) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if check and result.returncode:
        raise BenchmarkError(f"{' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def backup_save(save: Path, output: Path, digest: str) -> Path:
    """Keep one local recovery copy for each exact save used by a run."""
    directory = output / "_save_backups"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}.eu4"
    if target.exists():
        if sha256(target) != digest:
            raise BenchmarkError(f"Existing benchmark save backup has changed: {target}")
        return target
    temporary = directory / f".{digest}.temporary"
    try:
        shutil.copy2(save, temporary)
        if sha256(temporary) != digest:
            raise BenchmarkError("Save backup did not match its source")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def gog_identity() -> dict:
    if not GOG_EXE.is_file() or not GOG_INFO.is_file() or not GOG_LAUNCHER.is_file():
        raise BenchmarkError(f"GOG EU IV was not found at {GOG_ROOT}")
    info = json.loads(GOG_INFO.read_text())
    launcher = json.loads(GOG_LAUNCHER.read_text())
    if info.get("gameId") != "2057001589" and str(info.get("gameId")) != "2057001589":
        raise BenchmarkError(f"Unexpected GOG game ID in {GOG_INFO}")
    if launcher.get("distPlatform") != "gog" or Path(launcher.get("gameDataPath", "")).expanduser() != USER_DATA:
        raise BenchmarkError(f"Unexpected GOG launcher data path in {GOG_LAUNCHER}")
    return {
        "executable": str(GOG_EXE),
        "executable_sha256": sha256(GOG_EXE),
        "gog_game_id": str(info["gameId"]),
        "gog_build_id": str(info.get("buildId", "")),
        "gog_package_version": str(info.get("version", "")),
        "eu4_version": str(launcher.get("version", "")),
        "eu4_branch": (GOG_ROOT / "eu4_branch.txt").read_text().strip(),
        "eu4_revision": (GOG_ROOT / "eu4_rev.txt").read_text().strip(),
        "gog_user_data": str(USER_DATA),
        "architecture": command("file", str(GOG_EXE)).strip(),
        "linked_graphics": [line.strip() for line in command("otool", "-L", str(GOG_EXE)).splitlines()
                            if "OpenGL.framework" in line or "GLUT.framework" in line],
    }


def process_executable_path(pid: int) -> str | None:
    """Resolve macOS's executable path even when the launcher used ./eu4."""
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    libproc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    libproc.proc_pidpath.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(4096)
    length = libproc.proc_pidpath(pid, buffer, len(buffer))
    return buffer.value.decode(errors="replace") if length > 0 else None


def running_game_pid(scenario: str) -> int | None:
    """Accept only the exact GOG Mach-O path, never a similarly named game."""
    output = command("ps", "-axo", "pid=,comm=")
    game_processes = []
    for line in output.splitlines():
        match = re.match(r"\s*(\d+)\s+(.+)$", line)
        if not match:
            continue
        pid, executable = int(match.group(1)), match.group(2).strip()
        if Path(executable).name == "eu4":
            game_processes.append((pid, executable))
    if scenario == "idle":
        if game_processes:
            raise BenchmarkError(f"Idle control requires EU IV closed; found {game_processes}")
        return None
    matches = [(pid, process_executable_path(pid)) for pid, _ in game_processes]
    matches = [(pid, path) for pid, path in matches if path == str(GOG_EXE)]
    if len(matches) != 1 or len(game_processes) != 1:
        resolved = [(pid, process_executable_path(pid)) for pid, _ in game_processes]
        raise BenchmarkError(f"Expected one running GOG EU IV process at {GOG_EXE}; resolved {resolved}")
    return matches[0][0]


def display_mode() -> dict:
    """CoreGraphics returns 0 Hz for some variable-refresh modes or headless sessions."""
    try:
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cg.CGMainDisplayID.restype = ctypes.c_uint32
        display = cg.CGMainDisplayID()
        if not display:
            return {"verified": False, "reason": "CoreGraphics did not expose a main display"}
        cg.CGDisplayCopyDisplayMode.argtypes = [ctypes.c_uint32]
        cg.CGDisplayCopyDisplayMode.restype = ctypes.c_void_p
        mode = cg.CGDisplayCopyDisplayMode(display)
        if not mode:
            return {"verified": False, "reason": "CoreGraphics did not expose a display mode"}
        try:
            for name, kind in (("CGDisplayModeGetWidth", ctypes.c_size_t),
                               ("CGDisplayModeGetHeight", ctypes.c_size_t),
                               ("CGDisplayModeGetPixelWidth", ctypes.c_size_t),
                               ("CGDisplayModeGetPixelHeight", ctypes.c_size_t),
                               ("CGDisplayModeGetRefreshRate", ctypes.c_double)):
                function = getattr(cg, name)
                function.argtypes = [ctypes.c_void_p]
                function.restype = kind
            width = cg.CGDisplayModeGetWidth(mode)
            height = cg.CGDisplayModeGetHeight(mode)
            pixel_width = cg.CGDisplayModeGetPixelWidth(mode)
            pixel_height = cg.CGDisplayModeGetPixelHeight(mode)
            hz = cg.CGDisplayModeGetRefreshRate(mode)
            cg.CGDisplayCopyAllDisplayModes.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
            cg.CGDisplayCopyAllDisplayModes.restype = ctypes.c_void_p
            cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
            cf.CFArrayGetCount.restype = ctypes.c_long
            cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
            cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
            available = set()
            modes = cg.CGDisplayCopyAllDisplayModes(display, None)
            if modes:
                try:
                    for index in range(cf.CFArrayGetCount(modes)):
                        candidate = cf.CFArrayGetValueAtIndex(modes, index)
                        if (cg.CGDisplayModeGetPixelWidth(candidate) == pixel_width and
                                cg.CGDisplayModeGetPixelHeight(candidate) == pixel_height):
                            rate = cg.CGDisplayModeGetRefreshRate(candidate)
                            if rate > 0:
                                available.add(rate)
                finally:
                    cf.CFRelease.argtypes = [ctypes.c_void_p]
                    cf.CFRelease(modes)
            return {"display_id": display, "width": width, "height": height,
                    "pixel_width": pixel_width, "pixel_height": pixel_height,
                    "refresh_hz": hz, "available_refresh_hz_at_current_resolution": sorted(available),
                    "verified": bool(hz)}
        finally:
            cf.CFRelease.argtypes = [ctypes.c_void_p]
            cf.CFRelease(mode)
    except (AttributeError, OSError) as exc:
        return {"verified": False, "reason": str(exc)}


def power_state() -> dict:
    supply = command("pmset", "-g", "ps")
    source_match = re.search(r"Now drawing from '([^']+)'", supply)
    source = source_match.group(1) if source_match else "unknown"
    settings = command("pmset", "-g", "custom")
    active = command("pmset", "-g")
    def mode_from(text: str) -> str | None:
        modern = re.search(r"^\s*powermode\s+(\d+)", text, re.MULTILINE)
        if modern:
            return {0: "normal", 1: "low", 2: "high"}.get(int(modern.group(1)), "unknown")
        legacy = re.search(r"^\s*lowpowermode\s+(\d+)", text, re.MULTILINE)
        if legacy:
            return "low" if int(legacy.group(1)) else "normal"
        return None
    section = None
    modes = {}
    for line in settings.splitlines():
        if line.endswith("Power:"):
            section = line[:-1]
        if section:
            parsed = mode_from(line)
            if parsed is not None:
                modes[section] = parsed
    mode = mode_from(active) or modes.get(source)
    return {"source": source, "mode": mode, "all_modes": modes}


def game_settings() -> dict:
    path = USER_DATA / "settings.txt"
    if not path.is_file():
        return {"path": str(path), "exists": False}
    content = path.read_text(errors="replace")
    def block(name: str) -> str:
        match = re.search(rf"\b{name}\s*=\s*\{{", content)
        if not match:
            return ""
        depth = 0
        for index in range(match.end() - 1, len(content)):
            if content[index] == "{":
                depth += 1
            elif content[index] == "}":
                depth -= 1
                if depth == 0:
                    return content[match.start():index + 1]
        return ""
    render_settings = block("graphics") + "\n" + block("mapRenderingOptions")
    values = {}
    for key in ("refreshRate", "fullScreen", "borderless", "vsync", "multi_sampling",
                "game_ui_scale", "autosave"):
        match = re.search(rf"\b{key}\s*=\s*([^\r\n]+)", content)
        values[key] = match.group(1).strip() if match else None
    match = re.search(r"\bsize\s*=\s*\{\s*x\s*=\s*(\d+)\s*y\s*=\s*(\d+)", content)
    values["game_resolution"] = f"{match.group(1)}x{match.group(2)}" if match else None
    return {"path": str(path), "sha256": sha256(path),
            "render_settings_sha256": hashlib.sha256(render_settings.encode()).hexdigest(), **values}


def mod_settings() -> dict:
    path = USER_DATA / "dlc_load.json"
    if not path.is_file():
        return {"path": str(path), "exists": False}
    try:
        return {"path": str(path), "sha256": sha256(path), "selection": json.loads(path.read_text())}
    except (json.JSONDecodeError, UnicodeError):
        return {"path": str(path), "sha256": sha256(path), "parse_error": True}


def read_plist_stream(path: Path) -> list[dict]:
    data = path.read_bytes()
    samples = []
    for index, item in enumerate(data.split(b"\0")):
        if not item.strip():
            continue
        try:
            sample = plistlib.loads(item.strip())
        except (ValueError, TypeError, plistlib.InvalidFileException) as exc:
            raise BenchmarkError(f"Invalid plist sample {index} in {path}: {exc}") from exc
        if not isinstance(sample, dict):
            raise BenchmarkError(f"Plist sample {index} is not a dictionary")
        samples.append(sample)
    return samples


def numeric(data: dict, names: tuple[str, ...]) -> float | None:
    for name in names:
        value = data.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def normalized_sample(sample: dict, index: int) -> dict:
    processor = sample.get("processor") or {}
    gpu = sample.get("gpu") or {}
    if not isinstance(processor, dict) or not isinstance(gpu, dict):
        raise BenchmarkError("Unexpected powermetrics processor/gpu structure")
    interval_ns = numeric(sample, ("elapsed_ns",))
    interval_s = interval_ns / 1e9 if interval_ns else None
    cpu_mw = numeric(processor, ("cpu_power",))
    gpu_mw = numeric(processor, ("gpu_power",))
    if cpu_mw is None and interval_s:
        cpu_energy_mj = numeric(processor, ("cpu_energy",))
        cpu_mw = cpu_energy_mj / interval_s if cpu_energy_mj is not None else None
    if gpu_mw is None and interval_s:
        gpu_energy_mj = numeric(processor, ("gpu_energy",))
        gpu_mw = gpu_energy_mj / interval_s if gpu_energy_mj is not None else None
    combined_mw = numeric(processor, ("combined_power",))
    if cpu_mw is None or gpu_mw is None:
        raise BenchmarkError(f"CPU/GPU power fields missing in sample {index}; inspect raw plist keys")
    idle_ratio = numeric(gpu, ("idle_ratio",))
    timestamp = sample.get("timestamp")
    if isinstance(timestamp, dt.datetime):
        timestamp = timestamp.isoformat()
    return {"sample": index, "timestamp": timestamp or "", "interval_s": interval_s or "",
            "cpu_w": cpu_mw / 1000, "gpu_w": gpu_mw / 1000,
            "combined_w": combined_mw / 1000 if combined_mw is not None else "",
            "gpu_active_pct": round(100 * (1 - idle_ratio), 3) if idle_ratio is not None else "",
            "thermal_pressure": sample.get("thermal_pressure", "")}


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_date(value: str) -> dt.date:
    match = re.fullmatch(r"(\d{1,4})[.\-/](\d{1,2})[.\-/](\d{1,2})", value.strip())
    if not match:
        raise BenchmarkError("Dates must be YYYY.MM.DD, YYYY-MM-DD, or YYYY/MM/DD")
    return dt.date(*(int(part) for part in match.groups()))


def prompt(message: str, required: bool = False) -> str:
    while True:
        value = input(message).strip()
        if value or not required:
            return value


def cue() -> None:
    """Mark timing boundaries while the game, not Terminal, is foreground."""
    sound = Path("/System/Library/Sounds/Glass.aiff")
    if sound.is_file():
        try:
            subprocess.Popen(["afplay", str(sound)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    print("\a", end="", flush=True)


def matching_multithreaded_run(output_root: Path, pid: int, executable_hash: str,
                               save_hash: str, settings_hash: str, mods_hash: str) -> Path:
    """Reject a power run if it is not the measured multithreaded game process."""
    for path in sorted(output_root.glob("*-paused-mp/metadata.json"), reverse=True):
        try:
            run = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if (run.get("status") == "complete" and run.get("multithreaded_gl_requested") is True and
                run.get("game_pid") == pid and
                run.get("gog", {}).get("executable_sha256") == executable_hash and
                run.get("save_sha256") == save_hash and
                run.get("settings", {}).get("render_settings_sha256") == settings_hash and
                run.get("dlc_mods", {}).get("sha256") == mods_hash and
                run.get("multithreaded_gl_contexts") and
                all(row.get("after") == 1 and row.get("enable_error") == 0
                    for row in run["multithreaded_gl_contexts"])):
            return path.parent
    raise BenchmarkError("No matching active multithreaded OpenGL run. Keep the successfully measured direct-launch game open, or relaunch it with --multithreaded-gl before this power capture")


def capture(args: argparse.Namespace) -> None:
    identity = gog_identity()
    pid = running_game_pid(args.scenario)
    save = Path(args.save).expanduser().resolve()
    if args.scenario != "idle" and not save.is_file():
        raise BenchmarkError(f"Benchmark save missing: {save}")
    settings = game_settings()
    mods = mod_settings()
    mp_run = None
    if getattr(args, "require_multithreaded_gl", False):
        if pid is None:
            raise BenchmarkError("Multithreaded OpenGL requires a running GOG EU IV process")
        mp_run = matching_multithreaded_run(
            Path(args.output).expanduser().resolve(), pid, identity["executable_sha256"],
            sha256(save), settings["render_settings_sha256"], mods["sha256"])
    power = power_state()
    if power["mode"] != args.power_mode:
        raise BenchmarkError(f"Power mode mismatch: requested {args.power_mode}, observed {power}")
    display_before_focus = display_mode()
    # Authenticate before asking the operator to start a timed game scenario.
    auth = subprocess.run(["sudo", "-n", "-v"] if args.non_interactive else ["sudo", "-v"], check=False)
    if auth.returncode:
        raise BenchmarkError("Administrator access is required for powermetrics; run from an interactive Terminal")
    if args.scenario != "idle" and not args.non_interactive:
        answer = prompt(f"Confirm {save.name} is loaded in GOG EU IV with intended DLC/mods (yes/no): ", True)
        if answer.lower() != "yes":
            raise BenchmarkError("Save compatibility was not confirmed")
    if args.scenario != "idle" and args.non_interactive and not args.save_confirmed:
        raise BenchmarkError("Non-interactive game runs require --save-confirmed")
    if args.scenario.startswith("speed") and settings.get("autosave", "").strip('"').upper() not in ("NO", "OFF", "NEVER", "NONE"):
        if not args.allow_autosave:
            raise BenchmarkError("Autosave is enabled. Use a disposable non-Ironman save with autosave off, or --allow-autosave if you accept that the campaign may advance and save")
        print("Warning: autosave appears enabled; save stalls are part of this run and the campaign may advance.", file=sys.stderr)
    start_date = args.start_date
    if args.scenario.startswith("speed") and not start_date and not args.non_interactive:
        start_date = prompt("In-game start date (YYYY.MM.DD): ", True)
    if start_date:
        parse_date(start_date)
    if not args.non_interactive:
        if args.scenario == "idle":
            prompt(f"EU IV must remain closed. Press Enter to capture {args.seconds}s of idle activity: ")
        elif args.scenario.startswith("speed"):
            prompt(f"Keep EU IV paused at the start date. Press Enter, return to the game, then start {args.scenario} at the first sound: ")
        else:
            prompt("Keep GOG EU IV paused. Press Enter and switch back to the game: ")
        if args.scenario != "idle":
            print(f"Capture begins after at least a {args.lead_in}s lead-in. Keep EU IV in the foreground.", flush=True)
            time.sleep(args.lead_in)
    display = (display_mode() if args.scenario != "idle" and not args.non_interactive
               else display_before_focus)
    display_capture_phase = ("after_game_focus" if args.scenario != "idle" and not args.non_interactive
                             else "preflight")
    if args.refresh_hz is not None and display.get("verified"):
        if abs(display["refresh_hz"] - args.refresh_hz) > 1:
            raise BenchmarkError(f"Refresh mismatch: requested {args.refresh_hz} Hz, observed {display['refresh_hz']} Hz")
    elif args.refresh_hz is not None:
        print(f"Warning: display refresh could not be verified: {display}", file=sys.stderr)
    if pid != running_game_pid(args.scenario):
        raise BenchmarkError("EU IV process changed before capture")
    now = dt.datetime.now(dt.timezone.utc)
    name = f"{now:%Y%m%dT%H%M%SZ}-{args.scenario}-t{args.trial}"
    output_root = Path(args.output).expanduser().resolve()
    save_digest = sha256(save) if args.scenario != "idle" else None
    save_backup = backup_save(save, output_root, save_digest) if save_digest else None
    run_dir = output_root / name
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata = {"status": "capturing", "scenario": args.scenario, "trial": args.trial,
                "seconds_requested": args.seconds, "started_utc": now.isoformat(),
                "gog": identity, "game_pid": pid, "save_path": str(save) if args.scenario != "idle" else None,
                "save_sha256": save_digest, "save_backup": str(save_backup) if save_backup else None,
                "allow_autosave": args.allow_autosave,
                "save_confirmed": args.scenario != "idle", "settings": settings,
                "dlc_mods": mods, "power": power, "display": display,
                "multithreaded_gl_source_run": str(mp_run) if mp_run else None,
                "display_before_focus": display_before_focus,
                "display_capture_phase": display_capture_phase,
                "refresh_hz_requested": args.refresh_hz, "power_mode_requested": args.power_mode,
                "start_date": start_date, "end_date": args.end_date, "fps_observed": args.fps}
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str) + "\n")
    count = args.seconds
    pm_command = ["sudo", "-n", "powermetrics", "-i", "1000", "-n", str(count),
                  "-s", "tasks,cpu_power,gpu_power,thermal", "-f", "plist", "--show-process-gpu"]
    process_rows = []
    save_events = []
    save_stat = save.stat() if args.scenario != "idle" else None
    save_signature = ((save_stat.st_ino, save_stat.st_size, save_stat.st_mtime_ns)
                      if save_stat else None)
    started = time.monotonic()
    with (run_dir / "powermetrics.pliststream").open("wb") as raw, (run_dir / "powermetrics.stderr").open("wb") as errors:
        if args.scenario != "idle" and not args.non_interactive:
            cue()
        pm = subprocess.Popen(pm_command, stdout=raw, stderr=errors)
        try:
            while pm.poll() is None:
                if save_signature is not None:
                    try:
                        current_stat = save.stat()
                        current_signature = (current_stat.st_ino, current_stat.st_size,
                                             current_stat.st_mtime_ns)
                    except FileNotFoundError:
                        current_signature = None
                    if current_signature != save_signature:
                        save_events.append({"elapsed_s": round(time.monotonic() - started, 3),
                                            "exists": current_signature is not None,
                                            "size_bytes": current_stat.st_size if current_signature else None})
                        save_signature = current_signature
                if pid is not None:
                    result = subprocess.run(["ps", "-p", str(pid), "-o", "pid=,%cpu=,rss="],
                                            capture_output=True, text=True, check=False)
                    fields = result.stdout.split()
                    if result.returncode or len(fields) != 3 or int(fields[0]) != pid:
                        pm.terminate()
                        raise BenchmarkError("GOG EU IV exited during capture")
                    process_rows.append({"elapsed_s": round(time.monotonic() - started, 3),
                                         "pid": pid, "cpu_pct": float(fields[1]), "rss_kb": int(fields[2])})
                time.sleep(1)
        except (BenchmarkError, OSError, KeyboardInterrupt) as exc:
            pm.terminate()
            metadata["status"] = "incomplete"
            metadata["error"] = "Capture interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc)
            (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str) + "\n")
            raise BenchmarkError(metadata["error"]) from exc
        finally:
            try:
                pm.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pm.kill()
                pm.wait()
    metadata["elapsed_s"] = round(time.monotonic() - started, 3)
    metadata["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if args.scenario != "idle" and not args.non_interactive:
        cue()
    try:
        if pm.returncode:
            raise BenchmarkError(f"powermetrics failed: {(run_dir / 'powermetrics.stderr').read_text(errors='replace').strip()}")
        if pid is not None and running_game_pid(args.scenario) != pid:
            raise BenchmarkError("GOG EU IV process changed during capture")
        if args.scenario != "idle":
            metadata["save_events"] = save_events
            metadata["save_sha256_after"] = sha256(save) if save.is_file() else None
            metadata["save_changed"] = metadata["save_sha256_after"] != metadata["save_sha256"]
            if metadata["save_changed"] and not args.allow_autosave:
                raise BenchmarkError("Benchmark save changed during capture; restore a fixed fixture before repeating")
        raw_samples = read_plist_stream(run_dir / "powermetrics.pliststream")
        if len(raw_samples) < max(1, int(0.8 * count)):
            raise BenchmarkError(f"Only {len(raw_samples)} of {count} expected power samples were captured")
        rows = [normalized_sample(sample, index) for index, sample in enumerate(raw_samples)]
        metadata["sampled_s"] = round(sum(float(row["interval_s"]) for row in rows if row["interval_s"] != ""), 3)
        write_csv(run_dir / "samples.csv", CSV_FIELDS, rows)
        write_csv(run_dir / "process.csv", ("elapsed_s", "pid", "cpu_pct", "rss_kb"), process_rows)
        if args.scenario.startswith("speed") and not metadata["end_date"] and not args.non_interactive:
            metadata["end_date"] = prompt("Pause the game now. In-game end date (YYYY.MM.DD): ", True)
        if metadata["end_date"]:
            parse_date(metadata["end_date"])
        if start_date and metadata["end_date"]:
            metadata["days_advanced"] = (parse_date(metadata["end_date"]) - parse_date(start_date)).days
            if metadata["days_advanced"] < 0:
                raise BenchmarkError("End date precedes start date")
            duration = metadata["sampled_s"] or metadata["elapsed_s"]
            metadata["days_per_second"] = round(metadata["days_advanced"] / duration, 4)
        metadata["samples"] = len(rows)
        metadata["status"] = "complete"
        print(f"Captured {len(rows)} samples: {run_dir}")
    except BenchmarkError as exc:
        metadata["status"] = "incomplete"
        metadata["error"] = str(exc)
        raise
    finally:
        (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str) + "\n")


def inspect(_: argparse.Namespace) -> None:
    identity = gog_identity()
    identity["display"] = display_mode()
    identity["power"] = power_state()
    identity["settings"] = game_settings()
    identity["dlc_mods"] = mod_settings()
    identity["default_save"] = {"path": str(DEFAULT_SAVE), "exists": DEFAULT_SAVE.is_file(),
                                "sha256": sha256(DEFAULT_SAVE) if DEFAULT_SAVE.is_file() else None}
    print(json.dumps(identity, indent=2, default=str))


def probe_display(args: argparse.Namespace) -> None:
    """Check the actual game-foreground display mode without power sampling."""
    pid = running_game_pid("paused")
    before = display_mode()
    prompt("Press Enter, then switch to GOG EU IV and leave it in the foreground: ")
    time.sleep(args.lead_in)
    if running_game_pid("paused") != pid:
        raise BenchmarkError("EU IV process changed during display probe")
    after = display_mode()
    cue()
    print(json.dumps({"before_game_focus": before, "with_game_foreground": after}, indent=2))
    if args.expect_hz is not None:
        if not after.get("verified"):
            raise BenchmarkError("Could not verify the display refresh rate with EU IV foreground")
        if abs(after["refresh_hz"] - args.expect_hz) > 1:
            raise BenchmarkError(f"Expected {args.expect_hz:g} Hz with EU IV foreground; observed {after['refresh_hz']:g} Hz")


def profile_cpu(args: argparse.Namespace) -> None:
    """Collect a short paused-game call-stack sample after the operator returns to EU IV."""
    pid = running_game_pid("paused")
    auth = subprocess.run(["sudo", "-v"], check=False)
    if auth.returncode:
        raise BenchmarkError("Administrator access is required for sample; run from an interactive Terminal")
    prompt("Keep GOG EU IV paused. Press Enter and switch back to the game: ")
    print(f"CPU profiling begins after a {args.lead_in}s lead-in; keep EU IV foreground until the second sound.", flush=True)
    time.sleep(args.lead_in)
    if running_game_pid("paused") != pid:
        raise BenchmarkError("EU IV process changed before CPU profiling")
    display = display_mode()
    now = dt.datetime.now(dt.timezone.utc)
    run_dir = Path(args.output).expanduser().resolve() / f"{now:%Y%m%dT%H%M%SZ}-paused-cpu"
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata = {"status": "capturing", "started_utc": now.isoformat(),
                "game_pid": pid, "seconds_requested": args.seconds,
                "display": display, "power": power_state(),
                "settings": game_settings(), "gog": gog_identity()}
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str) + "\n")
    try:
        cue()
        with (run_dir / "cpu-sample.txt").open("wb") as output, (run_dir / "sample.stderr").open("wb") as errors:
            result = subprocess.run(["sudo", "-n", "sample", str(pid), str(args.seconds), "5"],
                                    stdout=output, stderr=errors, check=False)
        cue()
        if result.returncode:
            raise BenchmarkError(f"sample failed: {(run_dir / 'sample.stderr').read_text(errors='replace').strip()}")
        if (run_dir / "cpu-sample.txt").stat().st_size == 0:
            raise BenchmarkError("sample returned no call stacks")
        metadata["status"] = "complete"
        metadata["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        print(f"Saved paused CPU profile: {run_dir / 'cpu-sample.txt'}")
    except (BenchmarkError, OSError) as exc:
        metadata["status"] = "incomplete"
        metadata["error"] = str(exc)
        raise
    finally:
        (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str) + "\n")


def dtrace_frame_probe(pid: int) -> str:
    """Find a frame-swap entry probe before asking the operator to focus the game."""
    candidates = (
        f"pid{pid}:eu4:Cocoa_GL_SwapWindow:entry",
        f"pid{pid}:eu4:_Cocoa_GL_SwapWindow:entry",
        f"pid{pid}:OpenGL:CGLFlushDrawable:entry",
        f"pid{pid}:OpenGL:_CGLFlushDrawable:entry",
    )
    failures = []
    for probe in candidates:
        result = subprocess.run(["sudo", "-n", "dtrace", "-l", "-p", str(pid), "-n", probe],
                                capture_output=True, text=True, check=False, timeout=15)
        if result.returncode == 0 and any(
            line.strip().endswith(" entry") and
            ("Cocoa_GL_SwapWindow" in line or "CGLFlushDrawable" in line)
            for line in result.stdout.splitlines()
        ):
            return probe
        if "cannot instrument translated processes" in result.stderr.lower():
            raise BenchmarkError("DTrace cannot instrument this GOG EU IV process because its x86_64 binary runs under Rosetta. More sudo privileges or another DTrace probe will not fix this.")
        failures.append(result.stderr.strip() or f"no matching probe for {probe}")
        if "privilege" in result.stderr.lower() or "operation not permitted" in result.stderr.lower():
            break
    raise BenchmarkError("DTrace could not expose an EU IV frame-swap probe: " + failures[-1])


def probe_fps(args: argparse.Namespace) -> None:
    """Count paused GOG EU IV swaps for a few seconds without altering the game."""
    pid = running_game_pid("paused")
    if subprocess.run(["sudo", "-v"], check=False).returncode:
        raise BenchmarkError("Administrator access is required for DTrace; run from an interactive Terminal")
    probe = dtrace_frame_probe(pid)
    print(f"DTrace frame-swap probe available: {probe}", flush=True)
    if args.check:
        print("Capability check passed. No frame capture was made.")
        return
    prompt("Keep GOG EU IV paused. Press Enter and switch back to the game: ")
    print(f"Frame counting begins after a {args.lead_in}s lead-in; keep EU IV foreground until the second sound.", flush=True)
    time.sleep(args.lead_in)
    if running_game_pid("paused") != pid:
        raise BenchmarkError("EU IV process changed before frame counting")
    display = display_mode()
    if not display.get("verified") or abs(display["refresh_hz"] - 120) > 1:
        raise BenchmarkError(f"Expected the original 120 Hz full-screen mode with EU IV foreground; observed {display}")
    now = dt.datetime.now(dt.timezone.utc)
    run_dir = Path(args.output).expanduser().resolve() / f"{now:%Y%m%dT%H%M%SZ}-paused-fps"
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata = {"status": "capturing", "started_utc": now.isoformat(),
                "game_pid": pid, "seconds_requested": args.seconds,
                "probe": probe, "display": display, "gog": gog_identity()}
    program = (f'BEGIN {{ start = timestamp; }}\n'
               f'{probe} {{ @frames = count(); }}\n'
               f'tick-{args.seconds}s {{ printa("frames=%@d\\n", @frames); '
               f'printf("elapsed_ns=%lld\\n", timestamp - start); exit(0); }}')
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str) + "\n")
    (run_dir / "frame-swaps.d").write_text(program + "\n")
    try:
        cue()
        try:
            with (run_dir / "dtrace.txt").open("wb") as output, (run_dir / "dtrace.stderr").open("wb") as errors:
                result = subprocess.run(["sudo", "-n", "dtrace", "-q", "-p", str(pid),
                                         "-s", str(run_dir / "frame-swaps.d")],
                                        stdout=output, stderr=errors, check=False, timeout=args.seconds + 20)
        finally:
            cue()
        if result.returncode:
            raise BenchmarkError(f"DTrace failed: {(run_dir / 'dtrace.stderr').read_text(errors='replace').strip()}")
        match = re.fullmatch(r"frames=(\d+)\s+elapsed_ns=(\d+)\s*", (run_dir / "dtrace.txt").read_text())
        if not match or int(match.group(1)) == 0 or int(match.group(2)) <= 0:
            raise BenchmarkError("DTrace did not count any frame swaps; FPS is unverified")
        metadata["swaps"] = int(match.group(1))
        metadata["elapsed_s"] = int(match.group(2)) / 1e9
        metadata["swaps_per_second"] = metadata["swaps"] / metadata["elapsed_s"]
        metadata["status"] = "complete"
        print(f"Approximate frame-swap rate: {metadata['swaps_per_second']:.1f}/s "
              f"({metadata['swaps']} swaps in {metadata['elapsed_s']:.2f}s)")
        print(f"Saved DTrace output: {run_dir / 'dtrace.txt'}")
    except (BenchmarkError, OSError, subprocess.TimeoutExpired) as exc:
        metadata["status"] = "incomplete"
        metadata["error"] = str(exc)
        raise
    finally:
        metadata["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str) + "\n")


def annotate(args: argparse.Namespace) -> None:
    path = Path(args.run).expanduser().resolve() / "metadata.json"
    metadata = json.loads(path.read_text())
    if metadata.get("status") != "complete":
        raise BenchmarkError("Only complete runs can be annotated")
    if args.start_date:
        parse_date(args.start_date)
        metadata["start_date"] = args.start_date
    if args.end_date:
        parse_date(args.end_date)
        metadata["end_date"] = args.end_date
    if args.fps is not None:
        metadata["fps_observed"] = args.fps
    if metadata.get("start_date") and metadata.get("end_date"):
        days = (parse_date(metadata["end_date"]) - parse_date(metadata["start_date"])).days
        if days < 0:
            raise BenchmarkError("End date precedes start date")
        metadata["days_advanced"] = days
        metadata["days_per_second"] = round(days / (metadata.get("sampled_s") or metadata["elapsed_s"]), 4)
    path.write_text(json.dumps(metadata, indent=2, default=str) + "\n")
    print(f"Updated {path}")


def reparse(args: argparse.Namespace) -> None:
    run_dir = Path(args.run).expanduser().resolve()
    metadata_path = run_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    if metadata.get("status") != "complete":
        raise BenchmarkError("Only complete runs can be reparsed automatically")
    current_settings = game_settings()
    if (not metadata.get("settings", {}).get("render_settings_sha256") and
            current_settings.get("sha256") == metadata.get("settings", {}).get("sha256")):
        metadata["settings"]["render_settings_sha256"] = current_settings["render_settings_sha256"]
    samples = read_plist_stream(run_dir / "powermetrics.pliststream")
    rows = [normalized_sample(sample, index) for index, sample in enumerate(samples)]
    if len(rows) < max(1, int(0.8 * metadata["seconds_requested"])):
        raise BenchmarkError("Raw stream contains too few samples")
    write_csv(run_dir / "samples.csv", CSV_FIELDS, rows)
    metadata["samples"] = len(rows)
    metadata["sampled_s"] = round(sum(float(row["interval_s"]) for row in rows if row["interval_s"] != ""), 3)
    if metadata.get("days_advanced") is not None:
        metadata["days_per_second"] = round(metadata["days_advanced"] /
                                            (metadata["sampled_s"] or metadata["elapsed_s"]), 4)
    metadata_path.write_text(json.dumps(metadata, indent=2, default=str) + "\n")
    print(f"Reparsed {len(rows)} samples in {run_dir}")


def load_runs(folder: Path) -> list[dict]:
    runs = []
    for metadata_path in sorted(folder.glob("*/metadata.json")):
        metadata = json.loads(metadata_path.read_text())
        if metadata.get("status") != "complete":
            continue
        if metadata.get("seconds_requested", 90) < 60:
            continue
        sample_path = metadata_path.parent / "samples.csv"
        if not sample_path.is_file():
            continue
        with sample_path.open(newline="") as source:
            rows = list(csv.DictReader(source))
        if not rows:
            continue
        process_path = metadata_path.parent / "process.csv"
        with process_path.open(newline="") as source:
            process_rows = list(csv.DictReader(source)) if process_path.is_file() else []
        def median(field: str, source: list[dict] = rows) -> float | None:
            values = [float(row[field]) for row in source if row.get(field) not in (None, "")]
            return statistics.median(values) if values else None
        runs.append({"run": metadata_path.parent.name, "scenario": metadata["scenario"],
                     "trial": metadata["trial"], "power_mode": metadata["power_mode_requested"],
                     "graphics_engine": ("multithreaded OpenGL" if metadata.get("multithreaded_gl_source_run")
                                         else "standard"),
                     "power_source": metadata["power"].get("source"),
                     "refresh_hz": metadata["display"].get("refresh_hz") or metadata.get("refresh_hz_requested"),
                     "refresh_verified": (metadata["display"].get("verified", False) and
                                          (metadata["scenario"] == "idle" or
                                           metadata.get("display_capture_phase") == "after_game_focus")),
                     "game_resolution": metadata["settings"].get("game_resolution"),
                     "settings_sha256": metadata["settings"].get("sha256"),
                     "render_settings_sha256": metadata["settings"].get("render_settings_sha256"),
                     "vsync": metadata["settings"].get("vsync"),
                     "mod_selection_sha256": metadata["dlc_mods"].get("sha256"),
                     "cpu_w": median("cpu_w"), "gpu_w": median("gpu_w"),
                     "combined_w": median("combined_w"), "gpu_active_pct": median("gpu_active_pct"),
                     "eu4_cpu_pct": median("cpu_pct", process_rows),
                     "days_per_second": metadata.get("days_per_second"),
                     "fps_observed": metadata.get("fps_observed"),
                     "thermal_pressure": ", ".join(sorted({row["thermal_pressure"] for row in rows if row["thermal_pressure"]})),
                     "gog_sha256": metadata["gog"]["executable_sha256"],
                     "save_sha256": metadata.get("save_sha256"),
                     "save_sha256_after": metadata.get("save_sha256_after"),
                     "save_changed": metadata.get("save_changed"),
                     "save_events": len(metadata.get("save_events", []))})
    return runs


def make_svg(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    width = max(760, 100 + len(rows) * 95)
    height = 460
    max_w = max((row["cpu_w"] or 0) + (row["gpu_w"] or 0) for row in rows) * 1.15 or 1
    plot_top, plot_bottom = 45, 350
    scale = (plot_bottom - plot_top) / max_w
    chunks = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
              '<rect width="100%" height="100%" fill="white"/>',
              '<text x="50" y="25" font-family="sans-serif" font-size="17">Median estimated CPU and GPU power by condition</text>',
              f'<line x1="55" y1="{plot_bottom}" x2="{width-20}" y2="{plot_bottom}" stroke="#333"/>']
    for i, row in enumerate(rows):
        x = 80 + i * 95
        gpu = row["gpu_w"] or 0
        cpu = row["cpu_w"] or 0
        gpu_h, cpu_h = gpu * scale, cpu * scale
        chunks.append(f'<rect x="{x}" y="{plot_bottom-gpu_h:.1f}" width="42" height="{gpu_h:.1f}" fill="#4f83cc"/>')
        chunks.append(f'<rect x="{x}" y="{plot_bottom-gpu_h-cpu_h:.1f}" width="42" height="{cpu_h:.1f}" fill="#e28b47"/>')
        label = f'{row["scenario"]} {row["refresh_hz"] or "?"}Hz {row["power_mode"]} {row["graphics_engine"]}'
        chunks.append(f'<text x="{x+20}" y="{plot_bottom+12}" text-anchor="end" transform="rotate(-35 {x+20} {plot_bottom+12})" font-family="sans-serif" font-size="10">{label}</text>')
    chunks.extend([f'<text x="{width-190}" y="28" fill="#e28b47" font-family="sans-serif" font-size="12">CPU</text>',
                   f'<text x="{width-135}" y="28" fill="#4f83cc" font-family="sans-serif" font-size="12">GPU</text>',
                   '</svg>'])
    path.write_text("\n".join(chunks) + "\n")


def make_efficiency_svg(rows: list[dict], path: Path) -> None:
    points = [row for row in rows if row["days_per_second"] is not None and
              (row["cpu_w"] or 0) + (row["gpu_w"] or 0) > 0]
    if not points:
        return
    width, height = 760, 420
    left, right, top, bottom = 75, 725, 45, 355
    max_power = max(row["cpu_w"] + row["gpu_w"] for row in points) * 1.15
    max_speed = max(row["days_per_second"] for row in points) * 1.15 or 1
    chunks = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
              '<rect width="100%" height="100%" fill="white"/>',
              '<text x="75" y="25" font-family="sans-serif" font-size="17">Simulation throughput versus estimated CPU + GPU power</text>',
              f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#333"/>',
              f'<line x1="{left}" y1="{bottom}" x2="{left}" y2="{top}" stroke="#333"/>',
              f'<text x="315" y="400" font-family="sans-serif" font-size="12">CPU + GPU watts (system-wide estimate)</text>',
              '<text x="16" y="220" transform="rotate(-90 16 220)" font-family="sans-serif" font-size="12">Game days / second</text>']
    for row in points:
        power = row["cpu_w"] + row["gpu_w"]
        x = left + power / max_power * (right - left)
        y = bottom - row["days_per_second"] / max_speed * (bottom - top)
        label = f'{row["scenario"]} {row["refresh_hz"] or "?"}Hz {row["power_mode"]} {row["graphics_engine"]}'
        chunks.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#2b796a"/>')
        chunks.append(f'<text x="{x+7:.1f}" y="{y-7:.1f}" font-family="sans-serif" font-size="10">{label}</text>')
    chunks.append('</svg>')
    path.write_text("\n".join(chunks) + "\n")


def classify(runs: list[dict]) -> str:
    """Require three repeat runs and non-overlapping power and activity ranges."""
    runs = [run for run in runs if run["graphics_engine"] == "standard"]
    if len({run["gog_sha256"] for run in runs}) != 1:
        return "Inconclusive: runs use different GOG executable builds."
    normal = [run for run in runs if run["power_mode"] == "normal" and run["refresh_verified"]]
    baseline_hz = [run["refresh_hz"] for run in normal if run["scenario"] == "paused"]
    if not baseline_hz:
        return "Inconclusive: no paused run has a verified display refresh rate."
    hz = Counter(baseline_hz).most_common(1)[0][0]
    base = [run for run in normal if run["refresh_hz"] == hz]
    conditions = {scenario: [run for run in base if run["scenario"] == scenario]
                  for scenario in ("idle", "paused", "speed5")}
    if any(len(group) < 3 for group in conditions.values()):
        return f"Inconclusive: idle, paused, and speed 5 each need three verified {hz} Hz Normal-mode runs."
    paused, speed5, idle = conditions["paused"], conditions["speed5"], conditions["idle"]
    controls = ("power_source", "game_resolution", "render_settings_sha256", "vsync",
                "mod_selection_sha256", "save_sha256")
    if any(len({run[key] for run in paused + speed5}) > 1 for key in controls):
        return "Inconclusive: paused and speed-5 runs differ in save, settings, mods, or power source."
    if any(run["power_source"] != paused[0]["power_source"] for run in idle):
        return "Inconclusive: idle and game runs used different power sources."
    def separated(high: list[dict], low: list[dict], field: str) -> bool:
        high_values = [run[field] for run in high]
        low_values = [run[field] for run in low]
        if any(value is None for value in high_values + low_values):
            return False
        idle_values = [run[field] for run in idle if run[field] is not None]
        noise = max(idle_values) - min(idle_values) if len(idle_values) >= 3 else 0
        return min(high_values) - max(low_values) > noise
    rendering = separated(paused, idle, "gpu_w") and separated(paused, idle, "gpu_active_pct")
    simulation = separated(speed5, paused, "cpu_w") and separated(speed5, paused, "eu4_cpu_pct")
    if rendering and simulation:
        return "Both contributions are supported: GPU power/activity rises with the paused game, and CPU power/EU IV activity rises at speed 5."
    if rendering:
        return "A rendering contribution is supported by paused GPU power/activity; speed-5 simulation evidence is inconclusive."
    if simulation:
        return "A simulation contribution is supported by speed-5 CPU power/EU IV activity; paused rendering evidence is inconclusive."
    return "Inconclusive: repeat-run ranges do not separate from controls strongly enough to attribute power."


def screening_observation(runs: list[dict]) -> str:
    """Describe a comparable first-pass paused/speed-5 pair without claiming attribution."""
    controls = ("gog_sha256", "save_sha256", "power_mode", "power_source", "graphics_engine",
                "refresh_hz", "game_resolution", "render_settings_sha256",
                "vsync", "mod_selection_sha256")
    paused = [run for run in runs if run["scenario"] == "paused" and run["refresh_verified"]]
    speed5 = [run for run in runs if run["scenario"] == "speed5" and run["refresh_verified"]]
    for base in reversed(paused):
        match = next((run for run in reversed(speed5)
                      if all(run[key] == base[key] for key in controls)), None)
        if match is None:
            continue
        needed = ("cpu_w", "gpu_w", "combined_w", "eu4_cpu_pct")
        if any(base[key] is None or match[key] is None for key in needed):
            continue
        return (f"One comparable paused/speed-5 pair at {base['refresh_hz']:g} Hz: "
                f"CPU power {match['cpu_w'] - base['cpu_w']:+.2f} W, "
                f"GPU power {match['gpu_w'] - base['gpu_w']:+.2f} W, "
                f"combined CPU + GPU power {match['combined_w'] - base['combined_w']:+.2f} W, "
                f"and EU IV CPU activity {match['eu4_cpu_pct'] - base['eu4_cpu_pct']:+.1f} percentage points. "
                "This is a screening signal; test a practical configuration change before deciding which comparisons merit repeats.")
    return "No comparable, in-game-refresh-verified paused/speed-5 pair is available yet."


def power_mode_observation(runs: list[dict]) -> str | None:
    """Summarize a first-pass speed-5 Low Power comparison on the same fixture."""
    controls = ("gog_sha256", "save_sha256", "power_source", "refresh_hz", "graphics_engine",
                "game_resolution", "render_settings_sha256", "vsync",
                "mod_selection_sha256")
    normal = [run for run in runs if run["scenario"] == "speed5" and
              run["power_mode"] == "normal" and run["refresh_verified"]]
    low = [run for run in runs if run["scenario"] == "speed5" and
           run["power_mode"] == "low" and run["refresh_verified"]]
    for candidate in reversed(low):
        base = next((run for run in reversed(normal)
                     if all(run[key] == candidate[key] for key in controls)), None)
        if base is None or any(run[key] is None or run[key] <= 0
                               for run in (base, candidate)
                               for key in ("combined_w", "days_per_second")):
            continue
        power_change = 100 * (candidate["combined_w"] / base["combined_w"] - 1)
        speed_change = 100 * (candidate["days_per_second"] / base["days_per_second"] - 1)
        efficiency_change = 100 * ((candidate["days_per_second"] / candidate["combined_w"]) /
                                   (base["days_per_second"] / base["combined_w"]) - 1)
        return (f"At speed 5, Low Power Mode changed median estimated CPU + GPU power "
                f"from {base['combined_w']:.2f} to {candidate['combined_w']:.2f} W "
                f"({power_change:+.1f}%), and measured throughput from "
                f"{base['days_per_second']:.2f} to {candidate['days_per_second']:.2f} days/s "
                f"({speed_change:+.1f}%). Estimated days per CPU + GPU joule changed "
                f"{efficiency_change:+.1f}%. This is a first-pass comparison; capture durations "
                "differ and end dates were entered manually.")
    return None


def display_configuration_observation(runs: list[dict]) -> str | None:
    """Compare complete paused configurations, explicitly retaining their confounding."""
    paused = [run for run in runs if run["scenario"] == "paused" and
              run["graphics_engine"] == "standard" and
              run["power_mode"] == "normal" and run["refresh_verified"]]
    old = next((run for run in reversed(paused) if run["refresh_hz"] == 120), None)
    new = next((run for run in reversed(paused) if run["refresh_hz"] == 60 and old and
                all(run[key] == old[key] for key in
                    ("gog_sha256", "save_sha256", "power_source", "mod_selection_sha256"))), None)
    if old is None or new is None or any(run[key] is None for run in (old, new)
                                       for key in ("cpu_w", "gpu_w", "combined_w")):
        return None
    return (f"Paused 60 Hz configuration versus 120 Hz baseline: GPU power "
            f"{old['gpu_w']:.2f} → {new['gpu_w']:.2f} W, CPU power "
            f"{old['cpu_w']:.2f} → {new['cpu_w']:.2f} W, and combined CPU + GPU power "
            f"{old['combined_w']:.2f} → {new['combined_w']:.2f} W. "
            "Window mode and game resolution also changed, so this measures the whole "
            "configuration rather than refresh rate alone; the combined saving is "
            "small enough to warrant caution before treating it as a repeatable gain.")


def report(args: argparse.Namespace) -> None:
    folder = Path(args.input).expanduser().resolve()
    runs = load_runs(folder)
    if not runs:
        raise BenchmarkError(f"No complete runs found under {folder}")
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    fields = tuple(runs[0].keys())
    write_csv(output / "runs.csv", fields, runs)
    groups = {}
    for run in runs:
        key = (run["scenario"], run["refresh_hz"], run["refresh_verified"],
               run["power_mode"], run["power_source"],
               run["graphics_engine"],
               run["gog_sha256"], run["save_sha256"], run["game_resolution"], run["render_settings_sha256"], run["vsync"],
               run["mod_selection_sha256"])
        groups.setdefault(key, []).append(run)
    summary = []
    for (scenario, hz, refresh_verified, mode, source, engine, exe_hash, save_hash, resolution, render_hash, vsync, mod_hash), group in sorted(groups.items(), key=lambda item: str(item[0])):
        def group_median(field: str) -> float | None:
            values = [float(run[field]) for run in group if run[field] is not None]
            return round(statistics.median(values), 3) if values else None
        summary.append({"scenario": scenario, "refresh_hz": hz, "power_mode": mode,
                        "graphics_engine": engine,
                        "power_source": source, "game_resolution": resolution,
                        "settings_sha256": group[0]["settings_sha256"] if len({run["settings_sha256"] for run in group}) == 1 else "varies",
                        "render_settings_sha256": render_hash, "vsync": vsync,
                        "mod_selection_sha256": mod_hash,
                        "trials": len(group), "refresh_verified": refresh_verified,
                        "cpu_w": group_median("cpu_w"), "gpu_w": group_median("gpu_w"),
                        "combined_w": group_median("combined_w"), "gpu_active_pct": group_median("gpu_active_pct"),
                        "eu4_cpu_pct": group_median("eu4_cpu_pct"),
                        "days_per_second": group_median("days_per_second"),
                        "soc_days_per_joule": None,
                        "fps_observed": group_median("fps_observed"),
                        "thermal_pressure": ", ".join(sorted({run["thermal_pressure"] for run in group if run["thermal_pressure"]})),
                        "gog_sha256": exe_hash,
                        "save_sha256": save_hash})
        latest = summary[-1]
        if latest["days_per_second"] is not None and (latest["cpu_w"] or 0) + (latest["gpu_w"] or 0) > 0:
            latest["soc_days_per_joule"] = round(latest["days_per_second"] / (latest["cpu_w"] + latest["gpu_w"]), 4)
    write_csv(output / "summary.csv", tuple(summary[0].keys()), summary)
    make_svg(summary, output / "power.svg")
    make_efficiency_svg(summary, output / "throughput.svg")
    lines = ["# EU IV GOG power report", "", "Power values are estimated system-wide SoC figures from powermetrics; they are not EU IV-only watts.", "",
             "## Screening observation", "", screening_observation(runs), ""]
    power_mode_screen = power_mode_observation(runs)
    if power_mode_screen:
        lines.extend([power_mode_screen, ""])
    display_screen = display_configuration_observation(runs)
    if display_screen:
        lines.extend([display_screen, ""])
    lines.extend([
             "## Formal attribution", "", classify(runs), "",
             "The three-repeat rule is a confidence threshold, not a required run count for screening configuration changes.", "",
             "## Measurements", "",
             "| Scenario | Hz | Mode | Graphics engine | Trials | CPU W | GPU W | EU IV CPU % | GPU active % | Thermal | Days/s | SoC days/J | FPS |",
             "|---|---:|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|"])
    def fmt(value: object) -> str:
        return "—" if value is None else str(value)
    for row in summary:
        hz = fmt(row["refresh_hz"]) + ("" if row["refresh_verified"] else "*")
        lines.append(f'| {row["scenario"]} | {hz} | {row["power_mode"]} | {row["graphics_engine"]} | {row["trials"]} | '
                     f'{fmt(row["cpu_w"])} | {fmt(row["gpu_w"])} | {fmt(row["eu4_cpu_pct"])} | '
                     f'{fmt(row["gpu_active_pct"])} | {fmt(row["thermal_pressure"])} | {fmt(row["days_per_second"])} | '
                     f'{fmt(row["soc_days_per_joule"])} | {fmt(row["fps_observed"])} |')
    lines.append("")
    if any(not row["refresh_verified"] for row in summary):
        lines.extend(["*Display refresh was not verified while EU IV was in the foreground; the listed rate may be from before the game regained focus or from the operator's requested mode.", ""])
    lines.extend(["Compare repeated runs with the idle control before attributing changes to rendering or simulation.",
                  "A paused GPU increase supports a rendering contribution; a speed-5 CPU increase supports a simulation contribution.",
                  "SoC days/J divides days/s by estimated CPU + GPU watts; it excludes other system components.",
                  "The power graph stacks CPU and GPU components, not total wall or battery power.", ""])
    (output / "report.md").write_text("\n".join(lines))
    print(f"Wrote {output / 'report.md'}, summary.csv, runs.csv, and available SVG plots")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect", help="Inspect the GOG binary, settings, display, and default save")
    display_probe = sub.add_parser("probe-display", help="Check the display mode after switching to GOG EU IV; no sudo or power capture")
    display_probe.add_argument("--lead-in", type=int, default=5)
    display_probe.add_argument("--expect-hz", type=float)
    cpu_profiler = sub.add_parser("profile-cpu", help="Sample paused GOG EU IV CPU call stacks for 10 seconds")
    cpu_profiler.add_argument("--seconds", type=int, default=10)
    cpu_profiler.add_argument("--lead-in", type=int, default=5)
    cpu_profiler.add_argument("--output", default=str(ROOT / "results"))
    fps_probe = sub.add_parser("probe-fps", help="Count paused GOG EU IV frame swaps with DTrace; --check tests support first")
    fps_probe.add_argument("--check", action="store_true", help="check DTrace probe availability without timing the game")
    fps_probe.add_argument("--seconds", type=int, default=10)
    fps_probe.add_argument("--lead-in", type=int, default=5)
    fps_probe.add_argument("--output", default=str(ROOT / "results"))
    record = sub.add_parser("record", help="Record one manually staged scenario")
    record.add_argument("--scenario", choices=SCENARIOS, required=True)
    record.add_argument("--trial", type=int, required=True)
    record.add_argument("--seconds", type=int, default=90)
    record.add_argument("--lead-in", type=int, default=5,
                        help="seconds to return to the game before sampling; ignored for idle")
    record.add_argument("--refresh-hz", type=float)
    record.add_argument("--power-mode", choices=("normal", "low"), default="normal")
    record.add_argument("--save", default=str(DEFAULT_SAVE))
    record.add_argument("--output", default=str(ROOT / "results"))
    record.add_argument("--start-date")
    record.add_argument("--end-date")
    record.add_argument("--fps", type=float)
    record.add_argument("--non-interactive", action="store_true")
    record.add_argument("--save-confirmed", action="store_true")
    record.add_argument("--allow-autosave", action="store_true")
    record.add_argument("--require-multithreaded-gl", action="store_true",
                        help="verify this process matches a completed multithreaded OpenGL frame-count run")
    annotation = sub.add_parser("annotate", help="Add dates or observed FPS after a run")
    annotation.add_argument("run")
    annotation.add_argument("--start-date")
    annotation.add_argument("--end-date")
    annotation.add_argument("--fps", type=float)
    reparse_parser = sub.add_parser("reparse", help="Regenerate CSV and timing from a complete raw run")
    reparse_parser.add_argument("run")
    report_parser = sub.add_parser("report", help="Summarize complete runs")
    report_parser.add_argument("--input", default=str(ROOT / "results"))
    report_parser.add_argument("--output", default=str(ROOT / "analysis/output"))
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            inspect(args)
        elif args.command == "probe-display":
            if args.lead_in < 0:
                raise BenchmarkError("Lead-in must be >= 0 seconds")
            probe_display(args)
        elif args.command == "profile-cpu":
            if args.seconds < 5 or args.lead_in < 0:
                raise BenchmarkError("Profile duration must be >= 5 seconds and lead-in >= 0")
            profile_cpu(args)
        elif args.command == "probe-fps":
            if args.seconds < 5 or args.lead_in < 0:
                raise BenchmarkError("FPS probe duration must be >= 5 seconds and lead-in >= 0")
            probe_fps(args)
        elif args.command == "record":
            if args.trial < 1 or args.seconds < 5 or args.lead_in < 0:
                raise BenchmarkError("Trial must be >= 1, duration >= 5 seconds, and lead-in >= 0")
            capture(args)
        elif args.command == "annotate":
            annotate(args)
        elif args.command == "reparse":
            reparse(args)
        else:
            report(args)
    except (BenchmarkError, OSError, ValueError, KeyboardInterrupt, subprocess.TimeoutExpired) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
