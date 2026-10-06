#!/usr/bin/env python3
"""Unattended, paused GOG EU IV baseline capture for the pinned Venice fixture."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import plistlib
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import eu4_benchmark as base
import eu4_diagnostic as diagnostic
from fixture_manager import FixtureManager


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/venice_paused.eu4"
FIXTURE_MANIFEST = ROOT / "fixtures/venice_paused.json"
PROBE_SOURCE = Path(__file__).with_name("eu4_auto_probe.c")
PROBE = ROOT / "benchmark/.build/libeu4_auto_probe.dylib"
FOCUS_SOURCE = Path(__file__).with_name("mac_game_focus.m")
FOCUS = ROOT / "benchmark/.build/mac_game_focus"
HELPER_SOURCE = Path(__file__).with_name("powermetrics_helper")
HELPER_INSTALLED = Path("/usr/local/libexec/eu4-powermetrics")
POWERMETRICS_INSTALL_HINT = "sudo sh benchmark/install_powermetrics_helper.sh"
PILLOW_INSTALL_HINT = (
    "python3 -m venv .venv && . .venv/bin/activate && "
    "pip install -r benchmark/requirements.txt"
)


def pillow_runtime_status() -> dict:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return {
            "status": "missing",
            "message": f"Pillow is required for scene alignment ({PILLOW_INSTALL_HINT})",
            "install_hint": PILLOW_INSTALL_HINT,
        }
    return {"status": "ready", "install_hint": PILLOW_INSTALL_HINT}
HARNESS_SOURCE = ROOT / "tests/idle_pacer_harness.cpp"
HARNESS = ROOT / "benchmark/.build/auto_probe_harness"
SCENE = ROOT / "fixtures/venice_scene.png"
SCENE_MANIFEST = ROOT / "fixtures/venice_scene.json"
READY_TIMEOUT_S = 180
VENICE_READY_TIMEOUT_MSG = "Venice paused readiness was not verified within 180 seconds"
WARMUP_S = 90
WARMUP_EXTENSION_S = 60
MEASURE_S = 30


def fixture_manifest() -> dict:
    expected = json.loads(FIXTURE_MANIFEST.read_text())
    if base.sha256(FIXTURE) != expected["save_sha256"]:
        raise base.BenchmarkError("Pinned Venice fixture checksum changed")
    with zipfile.ZipFile(FIXTURE) as archive:
        meta = archive.read("meta").decode(errors="replace")
        head = archive.open("gamestate").read(400).decode(errors="replace")
    if (f'player="{expected["country"]}"' not in meta or
            f'date={expected["date"]}' not in meta or
            f'speed={expected["speed"]}' not in head):
        raise base.BenchmarkError("Venice fixture metadata or paused speed changed")
    return expected


def build() -> None:
    PROBE.parent.mkdir(parents=True, exist_ok=True)
    commands = (
        ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
         "-dynamiclib", "-framework", "OpenGL", "-o", str(PROBE), str(PROBE_SOURCE)],
        ["clang", "-O2", "-Wall", "-Wextra", "-Werror",
         "-Wno-deprecated-declarations", "-fobjc-arc", "-framework", "AppKit",
         "-framework", "CoreGraphics",
         "-o", str(FOCUS), str(FOCUS_SOURCE)],
        ["clang++", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
         "-Wl,-export_dynamic", "-framework", "OpenGL", "-o", str(HARNESS),
         str(HARNESS_SOURCE)],
    )
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise base.BenchmarkError(f"Build failed: {result.stderr.strip()}")
    if base.command("lipo", "-archs", str(PROBE)).strip() != "x86_64":
        raise base.BenchmarkError("Readiness probe is not x86_64")


def powermetrics_helper_status() -> dict:
    """Non-throwing readiness probe for the reviewed root-owned powermetrics helper."""
    source_sha = base.sha256(HELPER_SOURCE)
    if not HELPER_INSTALLED.is_file():
        return {
            "status": "missing",
            "message": f"Install the reviewed root-owned powermetrics helper first ({POWERMETRICS_INSTALL_HINT})",
            "install_hint": POWERMETRICS_INSTALL_HINT,
            "helper_path": str(HELPER_INSTALLED),
            "source_sha256": source_sha,
        }
    stat = HELPER_INSTALLED.stat()
    if stat.st_uid != 0 or stat.st_mode & 0o022:
        return {
            "status": "invalid_permissions",
            "message": f"Powermetrics helper permissions are wrong; reinstall ({POWERMETRICS_INSTALL_HINT})",
            "install_hint": POWERMETRICS_INSTALL_HINT,
            "helper_path": str(HELPER_INSTALLED),
            "source_sha256": source_sha,
        }
    installed_sha = base.sha256(HELPER_INSTALLED)
    if installed_sha != source_sha:
        return {
            "status": "stale",
            "message": f"Installed powermetrics helper differs from the repo copy; reinstall ({POWERMETRICS_INSTALL_HINT})",
            "install_hint": POWERMETRICS_INSTALL_HINT,
            "helper_path": str(HELPER_INSTALLED),
            "installed_sha256": installed_sha,
            "source_sha256": source_sha,
        }
    check = subprocess.run(
        ["sudo", "-n", "-l", str(HELPER_INSTALLED)],
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode:
        return {
            "status": "sudo_denied",
            "message": (
                "Passwordless sudo is unavailable for the exact powermetrics helper; "
                f"run {POWERMETRICS_INSTALL_HINT} from your user session"
            ),
            "install_hint": POWERMETRICS_INSTALL_HINT,
            "helper_path": str(HELPER_INSTALLED),
            "source_sha256": source_sha,
        }
    return {
        "status": "ready",
        "helper_path": str(HELPER_INSTALLED),
        "source_sha256": source_sha,
        "install_hint": POWERMETRICS_INSTALL_HINT,
    }


def preflight(require_privilege: bool = False) -> dict:
    expected = fixture_manifest()
    gog = base.gog_identity()
    if gog["executable_sha256"] != expected["eu4_sha256"]:
        raise base.BenchmarkError("Installed GOG executable differs from the pinned fixture")
    settings = base.game_settings()
    mods = base.mod_settings()
    display = base.display_mode()
    power = base.power_state()
    if (settings.get("render_settings_sha256") != expected["render_settings_sha256"] or
            settings.get("autosave") != '"NEVER"' or
            settings.get("game_resolution") != expected["resolution"] or
            settings.get("fullScreen") != "yes" or settings.get("borderless") != "no"):
        raise base.BenchmarkError("GOG render settings or autosave differ from the fixture")
    if mods.get("sha256") != expected["dlc_load_sha256"]:
        raise base.BenchmarkError("GOG DLC/mod selection differs from the fixture")
    if require_privilege and (not display.get("verified") or
            abs(display.get("refresh_hz", 0)-expected["refresh_hz"]) > 1 or
            power.get("mode") != expected["power_mode"]):
        raise base.BenchmarkError("Expected verified 120 Hz display and Normal power mode")
    build()
    symbols = base.command("nm", "-nm", str(base.GOG_EXE))
    for symbol in ("__ZN12CApplication14AccessInstanceEv",
                   "__ZNK10CGameSpeed16IsActuallyPausedEv", "__ZTV12CInGameIdler"):
        if symbol not in symbols:
            raise base.BenchmarkError(f"Version-pinned pause symbol is missing: {symbol}")
    if display.get("verified"):
        with tempfile.TemporaryDirectory(prefix="eu4-auto-probe-") as temporary:
            root = Path(temporary)
            (root / "control.bin").write_bytes(bytes(4096))
            env = {**os.environ, "DYLD_INSERT_LIBRARIES": str(PROBE),
                   "EU4_AUTO_PROBE_LOG": str(root / "probe.csv"),
                   "EU4_PACE_CONTROL": str(root / "control.bin")}
            test = subprocess.run([str(HARNESS)], env=env, capture_output=True,
                                  text=True, check=False, timeout=20)
            observed = probe_rows(root / "probe.csv",
                                  {"monotonic_ns": time.monotonic_ns(),
                                   "wall_ns": time.time_ns()})
            if (test.returncode or len(observed) < 4 or
                    not any(r["paused_swaps"] > 0 for r in observed) or
                    not any(r["paused_swaps"] == 0 for r in observed)):
                raise base.BenchmarkError("Offline readiness probe did not observe paused and unpaused rendering")
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    if (launcher.get("exePath") != "./eu4.app/Contents/MacOS/eu4" or
            not isinstance(launcher.get("exeArgs"), list) or
            not all(isinstance(arg, str) for arg in launcher["exeArgs"])):
        raise base.BenchmarkError("Unexpected GOG launcher arguments")
    if require_privilege:
        status = powermetrics_helper_status()
        if status["status"] != "ready":
            raise base.BenchmarkError(status.get("message") or "Powermetrics helper is not ready")
    return {"fixture": expected, "gog": gog, "settings": settings, "dlc_mods": mods,
            "display": display, "power": power, "launcher_args": launcher["exeArgs"],
            "probe_sha256": base.sha256(PROBE), "probe_source_sha256": base.sha256(PROBE_SOURCE),
            "focus_source_sha256": base.sha256(FOCUS_SOURCE),
            "powermetrics_helper_sha256": base.sha256(HELPER_SOURCE)}


def probe_rows(path: Path, anchor: dict) -> list[dict]:
    if not path.is_file():
        return []
    output = []
    with path.open(newline="") as source:
        for row in csv.reader(source):
            if len(row) != 6 or row[0] != "S":
                continue
            try:
                clock_ns, wall_ns, interval_ns, swaps, paused = map(int, row[1:])
                if interval_ns <= 0 or swaps < 0 or not 0 <= paused <= swaps:
                    continue
                output.append({"clock_ns": clock_ns, "wall_ns": wall_ns,
                               "monotonic_ns": anchor["monotonic_ns"] + wall_ns-anchor["wall_ns"],
                               "interval_ns": interval_ns, "swaps": swaps,
                               "paused_swaps": paused, "swaps_s": swaps*1e9/interval_ns})
            except ValueError:
                continue
    if output:
        offsets = [r["wall_ns"]-r["clock_ns"] for r in output]
        if max(offsets)-min(offsets) > 100_000_000:
            raise base.BenchmarkError("Readiness-probe wall/uptime clocks diverged")
    return output


class PowerTail:
    def __init__(self, path: Path):
        self.path, self.offset, self.partial = path, 0, b""
        self.samples: list[dict] = []

    def _ingest(self, data: bytes) -> None:
        parts = (self.partial + data).split(b"\0")
        self.partial = parts.pop()
        for part in parts:
            if part.strip():
                try:
                    item = plistlib.loads(part.strip())
                except (ValueError, TypeError, plistlib.InvalidFileException) as exc:
                    raise base.BenchmarkError(f"Malformed powermetrics sample: {exc}") from exc
                if isinstance(item, dict):
                    self.samples.append(item)

    def poll(self) -> list[dict]:
        if not self.path.exists():
            return self.samples
        with self.path.open("rb") as source:
            source.seek(self.offset)
            new = source.read()
            self.offset += len(new)
        self._ingest(new)
        return self.samples

    def finish(self) -> dict[str, object]:
        """Drain trailing pliststream bytes after the powermetrics producer exits."""
        status = "clean"
        trailing_note = None
        if self.path.is_file():
            with self.path.open("rb") as source:
                source.seek(self.offset)
                new = source.read()
                self.offset += len(new)
            if new:
                before = len(self.samples)
                try:
                    self._ingest(new)
                except base.BenchmarkError:
                    raise
                if len(self.samples) > before and not self.partial.strip():
                    status = "appended"
                elif self.partial.strip():
                    try:
                        item = plistlib.loads(self.partial.strip())
                    except (ValueError, TypeError, plistlib.InvalidFileException):
                        status = "incomplete"
                        trailing_note = f"{len(self.partial)} trailing bytes not a complete plist"
                    else:
                        if isinstance(item, dict):
                            self.samples.append(item)
                        self.partial = b""
                        status = "appended"
            elif self.partial.strip():
                try:
                    item = plistlib.loads(self.partial.strip())
                except (ValueError, TypeError, plistlib.InvalidFileException):
                    status = "incomplete"
                    trailing_note = f"{len(self.partial)} trailing bytes not a complete plist"
                else:
                    if isinstance(item, dict):
                        self.samples.append(item)
                    self.partial = b""
                    status = "appended"
        return {
            "status": status,
            "trailing_partial_bytes": len(self.partial),
            "trailing_note": trailing_note,
            "sample_count": len(self.samples),
        }


def cpu_samples(raw: list[dict], pid: int, anchor: dict, start_ns: int = 0) -> list[tuple[int, float]]:
    values = []
    for item in raw:
        timestamp = item.get("timestamp")
        if not isinstance(timestamp, dt.datetime):
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
        when = anchor["monotonic_ns"]+int(timestamp.timestamp()*1e9)-anchor["wall_ns"]
        if when < start_ns:
            continue
        task = next((task for task in item.get("tasks", []) if task.get("pid") == pid), None)
        value = task.get("cputime_ms_per_s") if task else None
        if isinstance(value, (int, float)) and value > 0:
            values.append((when, float(value)))
    return values


def cpu_samples_between(
    raw: list[dict],
    pid: int,
    anchor: dict,
    start_ns: int,
    end_ns: int,
) -> list[tuple[int, float]]:
    return [
        (when, value)
        for when, value in cpu_samples(raw, pid, anchor, start_ns)
        if when <= end_ns
    ]


@dataclass(frozen=True)
class FocusState:
    frontmost: bool
    interior: bool
    activated: bool
    parked: bool

    @property
    def repaired(self) -> bool:
        return self.activated or self.parked


def ensure_game_focus_interior(
    pid: int,
    *,
    allow_activate: bool = True,
    allow_park: bool = True,
) -> FocusState:
    activated = False
    parked = False
    frontmost = focus("check", pid)
    if not frontmost and allow_activate:
        focus("activate", pid)
        activated = True
        frontmost = focus("check", pid)
    if not frontmost:
        return FocusState(False, False, activated, parked)
    interior = focus("interior", pid)
    if not interior and allow_park:
        focus("park", pid)
        parked = True
        interior = focus("interior", pid)
    return FocusState(frontmost, interior, activated, parked)


def readiness_predicates(
    *,
    logged: bool,
    frontmost: bool,
    pointer_interior: bool,
    rows: list[dict],
    now_ns: int,
    power: list[tuple[int, float]],
    gate_swap_stability: bool = False,
    gate_power_stability: bool = False,
) -> dict[str, bool | int]:
    probe_fresh = (
        len(rows) >= 1
        and 0 <= now_ns - rows[-1]["monotonic_ns"] <= 2_000_000_000
    )
    paused_rows_ok = len(rows) >= 10 and all(
        r["swaps_s"] >= 20 and r["paused_swaps"] >= 0.95 * r["swaps"] for r in rows[-10:]
    )
    swap_rates = [r["swaps_s"] for r in rows[-10:]] if len(rows) >= 10 else []
    power_values = [v for _, v in power]
    swap_rate_stable = stable(swap_rates, 10, 0.10) if len(swap_rates) >= 10 else False
    power_stable = stable(power_values, 10, 0.10) if len(power_values) >= 10 else False
    ready_core = (
        logged
        and frontmost
        and pointer_interior
        and len(rows) >= 10
        and probe_fresh
        and paused_rows_ok
    )
    ready_to_pass = ready_core
    if gate_swap_stability:
        ready_to_pass = ready_to_pass and swap_rate_stable
    if gate_power_stability:
        ready_to_pass = ready_to_pass and power_stable
    return {
        "game_log_ready": logged,
        "frontmost": frontmost,
        "pointer_interior": pointer_interior,
        "probe_sample_count": len(rows),
        "probe_fresh": probe_fresh,
        "paused_rows_ok": paused_rows_ok,
        "swap_rate_stable": swap_rate_stable,
        "power_sample_count": len(power_values),
        "power_stable": power_stable,
        "ready_to_pass": ready_to_pass,
    }


def stable(values: list[float], needed: int, limit: float) -> bool:
    return (len(values) >= needed and statistics.mean(values) > 0 and
            statistics.pstdev(values[-needed:])/statistics.mean(values[-needed:]) <= limit)


def fresh_game_log(path: Path, initial: bytes) -> str:
    if not path.is_file():
        return ""
    data = path.read_bytes()
    offset = len(initial) if data.startswith(initial) else 0
    return data[offset:].decode(errors="replace")


def game_log_markers(text: str) -> dict[str, bool]:
    return {
        "human_player": "Human Player set as primary local" in text,
        "venice": "Venice" in text,
        "launching_singleplayer": "Launching SINGLEPLAYER-game" in text,
        "start_date": "Start-date: 1444.11.11" in text,
        "end_restore_device_objects": "End RestoreDeviceObjects" in text,
    }


def game_log_ready(text: str) -> bool:
    markers = game_log_markers(text)
    return all(markers.values())


def collect_pre_ready_diagnostics(
    probe: Path,
    anchor: dict,
    raw: PowerTail,
    game: subprocess.Popen,
    game_log: Path,
    old_log: bytes,
    *,
    pid: int,
    submission_snapshot: dict | None = None,
) -> dict:
    rows = probe_rows(probe, anchor)
    now_ns = time.monotonic_ns()
    intervals_s: list[float] = []
    if len(rows) >= 2:
        monos = [r["monotonic_ns"] for r in rows]
        intervals_s = [(monos[i] - monos[i - 1]) / 1e9 for i in range(1, len(monos))]
    anchor_mono = int(anchor["monotonic_ns"])
    first_offset = (rows[0]["monotonic_ns"] - anchor_mono) / 1e9 if rows else None
    last_offset = (rows[-1]["monotonic_ns"] - anchor_mono) / 1e9 if rows else None
    swaps_rates = [r["swaps_s"] for r in rows]
    log_text = fresh_game_log(game_log, old_log)
    markers = game_log_markers(log_text)
    power = cpu_samples(raw.poll(), pid, anchor, now_ns - 12_000_000_000)[-10:]
    payload: dict = {
        "timeout_window_seconds": READY_TIMEOUT_S,
        "probe_row_count": len(rows),
        "probe_span_seconds": round((rows[-1]["monotonic_ns"] - rows[0]["monotonic_ns"]) / 1e9, 3) if len(rows) >= 2 else 0.0,
        "first_probe_offset_seconds": round(first_offset, 3) if first_offset is not None else None,
        "last_probe_offset_seconds": round(last_offset, 3) if last_offset is not None else None,
        "last_probe_age_seconds": round((now_ns - rows[-1]["monotonic_ns"]) / 1e9, 3) if rows else None,
        "max_probe_interval_seconds": round(max(intervals_s), 3) if intervals_s else None,
        "min_probe_interval_seconds": round(min(intervals_s), 3) if intervals_s else None,
        "median_probe_interval_seconds": round(statistics.median(intervals_s), 3) if intervals_s else None,
        "median_swaps_s": round(statistics.median(swaps_rates), 3) if swaps_rates else None,
        "min_swaps_s": round(min(swaps_rates), 3) if swaps_rates else None,
        "max_swaps_s": round(max(swaps_rates), 3) if swaps_rates else None,
        "paused_swaps_total": sum(r["paused_swaps"] for r in rows),
        "swaps_total": sum(r["swaps"] for r in rows),
        "last_probe_rows": rows[-10:],
        "game_log_ready": game_log_ready(log_text),
        "game_log_markers": markers,
        "recent_cpu_ms_s": [round(v, 2) for _, v in power],
        "process_alive": game.poll() is None,
    }
    if submission_snapshot is not None:
        payload["control_snapshot"] = submission_snapshot
    return payload


def best_effort_record_pre_ready_diagnostics(
    manifest: dict,
    *,
    run_dir: Path,
    game: subprocess.Popen | None,
    probe: Path,
    tail: PowerTail,
    anchor: dict,
    game_log: Path,
    old_log: bytes,
    submission_snapshot: dict | None,
    wait_diag: dict | None = None,
) -> None:
    try:
        if game is None:
            manifest["pre_ready_diagnostics"] = {"status": "skipped", "reason": "game handle missing"}
            return
        payload = collect_pre_ready_diagnostics(
            probe,
            anchor,
            tail,
            game,
            game_log,
            old_log,
            pid=game.pid,
            submission_snapshot=submission_snapshot,
        )
        if wait_diag:
            if wait_diag.get("last_predicates"):
                payload["readiness_predicates"] = wait_diag["last_predicates"]
            counters = wait_diag.get("counters")
            if counters:
                payload["readiness_wait_counters"] = counters
        manifest["pre_ready_diagnostics"] = payload
    except Exception as diagnostic_error:
        manifest["pre_ready_diagnostics"] = {
            "status": "collection_failed",
            "error": str(diagnostic_error),
        }
    try:
        if game is not None and game.poll() is None:
            capture_scene(run_dir / "pre-ready-timeout.png")
    except Exception as screenshot_error:
        manifest["pre_ready_screenshot_error"] = str(screenshot_error)


def focus(action: str, pid: int) -> bool:
    return subprocess.run([str(FOCUS), action, str(pid)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=False).returncode == 0


def capture_scene(path: Path) -> None:
    result = subprocess.run(["screencapture", "-x", "-t", "png", str(path)],
                            capture_output=True, text=True, check=False)
    if result.returncode or not path.is_file() or path.stat().st_size < 100_000:
        raise base.BenchmarkError(f"Could not capture the ready EU IV scene: {result.stderr.strip()}")


def scene_difference(reference: Path, observed: Path) -> dict:
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError as exc:
        raise base.BenchmarkError(
            f"Pillow is required for scene alignment ({PILLOW_INSTALL_HINT})",
        ) from exc
    with Image.open(reference) as first, Image.open(observed) as second:
        if first.size != second.size:
            raise base.BenchmarkError("EU IV scene resolution differs from the registered fixture")
        width, height = first.size
        lhs = first.convert("RGB").resize((160, 100))
        rhs = second.convert("RGB").resize((160, 100))
        scores = []
        # Compare the map interior, allowing a small startup camera offset.
        for dy in range(-8, 9):
            for dx in range(-8, 9):
                x0, x1 = max(32, 32+dx), min(128, 128+dx)
                y0, y1 = max(16, 16+dy), min(80, 80+dy)
                a = lhs.crop((x0, y0, x1, y1))
                b = rhs.crop((x0-dx, y0-dy, x1-dx, y1-dy))
                delta = statistics.mean(ImageStat.Stat(ImageChops.difference(a, b)).mean)
                scores.append((delta, abs(dx)+abs(dy), dx, dy))
        delta, _, dx, dy = min(scores)
        return {"mean_rgb_delta": round(delta, 2),
                "shift_x_px": round(dx*width/160),
                "shift_y_px": round(dy*height/100)}


def verify_scene(path: Path, bootstrap: bool) -> dict | None:
    if not SCENE.is_file() or not SCENE_MANIFEST.is_file():
        if bootstrap:
            return None
        raise base.BenchmarkError("Register a reviewed bootstrap scene before unattended baselines")
    manifest = json.loads(SCENE_MANIFEST.read_text())
    if base.sha256(SCENE) != manifest.get("sha256"):
        raise base.BenchmarkError("Registered scene reference changed")
    alignment = scene_difference(SCENE, path)
    from PIL import Image
    with Image.open(SCENE) as image:
        width, height = image.size
    if (alignment["mean_rgb_delta"] > 15 or
            abs(alignment["shift_x_px"]) > .04*width or
            abs(alignment["shift_y_px"]) > .09*height):
        raise base.BenchmarkError(f"Map/camera scene differs from reference: {alignment}")
    return alignment


def register_scene(run_dir: Path) -> Path:
    metadata = json.loads((run_dir / "manifest.json").read_text())
    source = run_dir / "ready-scene.png"
    if metadata.get("status") not in ("complete", "complete_unverified_scene") or not source.is_file():
        raise base.BenchmarkError("Register only a complete, reviewed bootstrap run")
    if SCENE.exists() or SCENE_MANIFEST.exists():
        raise base.BenchmarkError("A scene reference is already registered")
    shutil.copy2(source, SCENE)
    SCENE_MANIFEST.write_text(json.dumps({"sha256": base.sha256(SCENE),
                                          "source_run": str(run_dir)}, indent=2)+"\n")
    return SCENE


def mark(path: Path, event: str, **fields: object) -> dict:
    item = {"event": event, "monotonic_ns": time.monotonic_ns(),
            "wall_ns": time.time_ns(), **fields}
    with path.open("a") as output:
        output.write(json.dumps(item, sort_keys=True)+"\n")
    return item


def wait_until_ready(
    game: subprocess.Popen,
    probe: Path,
    raw: PowerTail,
    anchor: dict,
    game_log: Path,
    old_log: bytes,
    *,
    wait_diag: dict | None = None,
) -> dict:
    deadline = time.monotonic() + READY_TIMEOUT_S
    counters = {
        "activation_attempts": 0,
        "pointer_reparks": 0,
        "frontmost_false_iterations": 0,
        "pointer_noninterior_iterations": 0,
    }
    if wait_diag is not None:
        wait_diag.setdefault("counters", counters)
        counters = wait_diag["counters"]
    while time.monotonic() < deadline:
        if game.poll() is not None:
            raise base.BenchmarkError("GOG EU IV exited before the Venice scene became ready")
        focus_state = ensure_game_focus_interior(game.pid)
        if focus_state.activated:
            counters["activation_attempts"] += 1
        if focus_state.parked:
            counters["pointer_reparks"] += 1
        if not focus_state.frontmost:
            counters["frontmost_false_iterations"] += 1
        if focus_state.frontmost and not focus_state.interior:
            counters["pointer_noninterior_iterations"] += 1
        now_ns = time.monotonic_ns()
        rows = probe_rows(probe, anchor)[-10:]
        power = cpu_samples_between(
            raw.poll(), game.pid, anchor, now_ns - 12_000_000_000, now_ns
        )[-10:]
        logged = game_log_ready(fresh_game_log(game_log, old_log))
        predicates = readiness_predicates(
            logged=logged,
            frontmost=focus_state.frontmost,
            pointer_interior=focus_state.interior,
            rows=rows,
            now_ns=now_ns,
            power=power,
        )
        if wait_diag is not None:
            wait_diag["last_predicates"] = predicates
        if predicates["ready_to_pass"]:
            return {
                "ready_ns": time.monotonic_ns(),
                "swap_rate": round(statistics.median(r["swaps_s"] for r in rows), 2),
                "cpu_ms_s": round(statistics.median(v for _, v in power), 2)
                if power
                else None,
            }
        time.sleep(1)
    raise base.BenchmarkError(VENICE_READY_TIMEOUT_MSG)


def warm_up(game: subprocess.Popen, probe: Path, raw: PowerTail, anchor: dict) -> dict:
    started = time.monotonic_ns()
    deadline = time.monotonic() + WARMUP_S
    extension_deadline = deadline + WARMUP_EXTENSION_S
    last_focus_repair = 0.0
    while time.monotonic() < extension_deadline:
        if game.poll() is not None:
            raise base.BenchmarkError("GOG EU IV exited during warm-up")
        focus_state = ensure_game_focus_interior(game.pid)
        if focus_state.repaired:
            last_focus_repair = time.monotonic()
        if not focus_state.interior:
            raise base.BenchmarkError("EU IV lost focus or the pointer left the safe interior during warm-up")
        now_ns = time.monotonic_ns()
        now_mono = time.monotonic()
        rows = probe_rows(probe, anchor)[-15:]
        power = cpu_samples_between(
            raw.poll(), game.pid, anchor, now_ns - 17_000_000_000, now_ns
        )[-15:]
        quiet_ok = last_focus_repair == 0.0 or (now_mono - last_focus_repair) >= 15.0
        if (
            quiet_ok
            and now_mono >= deadline
            and len(rows) == 15
            and 0 <= now_ns - rows[-1]["monotonic_ns"] <= 2_000_000_000
            and all(r["swaps_s"] >= 20 and r["paused_swaps"] >= 0.95 * r["swaps"] for r in rows)
            and stable([r["swaps_s"] for r in rows], 15, 0.05)
            and stable([v for _, v in power], 15, 0.05)
        ):
            return {"start_ns": started, "end_ns": time.monotonic_ns()}
        time.sleep(1)
    raise base.BenchmarkError("Paused EU IV did not stabilize after 150 seconds of warm-up")


def summarize(
    run_dir: Path,
    phases: list[dict],
    anchor: dict,
    pid: int,
    raw_power: list[dict],
    *,
    swap_warmup_ns: int = 1_000_000_000,
    min_swap_samples: int = 25,
    min_power_samples: int = 25,
    write_shared_summary: bool = True,
) -> dict:
    rows = probe_rows(run_dir / "auto-probe.csv", anchor)
    phase = phases[0]
    selected = [
        r
        for r in rows
        if phase["start_ns"] + swap_warmup_ns <= r["monotonic_ns"] < phase["end_ns"]
    ]
    if len(selected) < min_swap_samples or any(
        r["swaps_s"] < 20 or r["paused_swaps"] < .95 * r["swaps"] for r in selected
    ):
        raise base.BenchmarkError(
            f"Insufficient fully paused swap samples in phase "
            f"(need {min_swap_samples}, got {len(selected)})"
        )
    phase_name = phase.get("name") or "measure"
    power_by_phase = diagnostic.summarize_power(raw_power, phases, anchor, pid)
    power = power_by_phase.get(phase_name) or power_by_phase.get("measure")
    if power is None:
        raise base.BenchmarkError(f"No power summary for phase {phase_name}")
    cpu_count = len(
        [
            value
            for when, value in cpu_samples(raw_power, pid, anchor, phase["start_ns"] + swap_warmup_ns)
            if when < phase["end_ns"]
        ]
    )
    if (
        power.get("samples", 0) < min_power_samples
        or cpu_count < min_power_samples
        or power.get("eu4_cputime_ms_per_s") is None
    ):
        raise base.BenchmarkError(
            f"Insufficient aligned EU IV CPU and power samples "
            f"(need {min_power_samples}, power={power.get('samples', 0)}, cpu={cpu_count})"
        )
    swaps = statistics.median(r["swaps_s"] for r in selected)
    result = {"swap_samples": len(selected), "median_swaps_s": round(swaps, 3),
              "eu4_cpu_ms_per_s": power["eu4_cputime_ms_per_s"],
              "cpu_w": power["cpu_w"], "gpu_w": power["gpu_w"],
              "combined_w": power["combined_w"], "power_samples": power["samples"],
              "eu4_cpu_ms_per_swap": round(power["eu4_cputime_ms_per_s"]/swaps, 3),
              "joules_per_swap": round(power["combined_w"]/swaps, 4)}
    summary_path = run_dir / f"summary-{phase_name}.json"
    summary_path.write_text(json.dumps(result, indent=2) + "\n")
    if write_shared_summary:
        (run_dir / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
        (run_dir / "summary.md").write_text(
        "# Unattended paused Venice baseline\n\n"
        "| EU IV CPU ms/s | CPU W | GPU W | Combined W | Swaps/s | CPU ms/swap | J/swap |\n"
        "|---:|---:|---:|---:|---:|---:|---:|\n"
        f"| {result['eu4_cpu_ms_per_s']} | {result['cpu_w']} | {result['gpu_w']} | "
        f"{result['combined_w']} | {result['median_swaps_s']} | "
        f"{result['eu4_cpu_ms_per_swap']} | {result['joules_per_swap']} |\n\n"
        "CPU/GPU watts are system-wide SoC estimates; swaps are an in-process proxy.\n")
    return result


def stop_process(process: subprocess.Popen | None, timeout: float = 10) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run(output_root: Path, evidence: dict, bootstrap: bool = False) -> Path:
    base.running_game_pid("idle")
    with FixtureManager(output_root, base.USER_DATA, FIXTURE,
                        evidence["fixture"]["save_sha256"]) as fixture:
        fixture.recover()
        run_dir = output_root / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-autonomous"
        run_dir.mkdir(parents=True, exist_ok=False)
        metadata = {"status": "starting", "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "evidence": evidence, "git_commit": None, "run_dir": str(run_dir)}
        manifest = run_dir / "manifest.json"
        manifest.write_text(json.dumps(metadata, indent=2)+"\n")
        events = run_dir / "events.jsonl"
        anchor = mark(events, "clock_anchor")
        metadata["clock_anchor"] = anchor
        game_log = base.USER_DATA / "logs/game.log"
        old_log = game_log.read_bytes() if game_log.is_file() else b""
        game = pm = None
        installed = False
        try:
            working_save = fixture.install()
            installed = True
            metadata["working_save"] = str(working_save)
            pm_path = run_dir / "powermetrics.pliststream"
            with pm_path.open("wb") as raw, (run_dir / "powermetrics.stderr").open("wb") as errors:
                pm = subprocess.Popen(["sudo", "-n", str(HELPER_INSTALLED)],
                                      stdout=raw, stderr=errors)
                time.sleep(2)
                if pm.poll() is not None:
                    raise base.BenchmarkError("Powermetrics helper exited before EU IV launch")
                env = {**os.environ, "DYLD_INSERT_LIBRARIES": str(PROBE),
                       "EU4_AUTO_PROBE_LOG": str(run_dir / "auto-probe.csv")}
                with (run_dir / "game.stdout").open("wb") as stdout, \
                     (run_dir / "game.stderr").open("wb") as stderr:
                    game = subprocess.Popen(["./eu4", *evidence["launcher_args"],
                                             "--continuelastsave"], cwd=base.GOG_EXE.parent,
                                            env=env, stdout=stdout, stderr=stderr)
                metadata["game_pid"] = game.pid
                tail = PowerTail(pm_path)
                ready = wait_until_ready(game, run_dir / "auto-probe.csv", tail, anchor,
                                         game_log, old_log)
                metadata["readiness"] = ready
                mark(events, "ready", **ready)
                metadata["display_with_game"] = base.display_mode()
                if (not metadata["display_with_game"].get("verified") or
                        abs(metadata["display_with_game"]["refresh_hz"]-120)>1):
                    raise base.BenchmarkError("EU IV changed the display away from 120 Hz")
                metadata["warmup"] = warm_up(game, run_dir / "auto-probe.csv", tail, anchor)
                mark(events, "warmup_complete")
                capture_scene(run_dir / "ready-scene.png")
                metadata["scene_alignment"] = verify_scene(
                    run_dir / "ready-scene.png", bootstrap)
                start = mark(events, "phase_start", phase="measure")
                deadline = time.monotonic()+MEASURE_S
                while time.monotonic() < deadline:
                    if game.poll() is not None:
                        raise base.BenchmarkError("EU IV exited during measurement")
                    if pm.poll() is not None:
                        raise base.BenchmarkError("Powermetrics helper exited during measurement")
                    if not focus("interior", game.pid):
                        raise base.BenchmarkError("EU IV lost focus or the pointer left the safe interior during measurement")
                    time.sleep(min(1, max(0, deadline-time.monotonic())))
                end = mark(events, "phase_end", phase="measure")
                phase = {"name": "measure", "start_ns": start["monotonic_ns"],
                         "end_ns": end["monotonic_ns"]}
                metadata["phases"] = [phase]
                stop_process(pm, 5)
            tail.poll()
            metadata["summary"] = summarize(run_dir, [phase], anchor, game.pid,
                                             tail.samples)
            metadata["status"] = "complete_unverified_scene" if bootstrap and not SCENE.exists() else "complete"
        except (base.BenchmarkError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
            metadata["status"] = "incomplete"
            metadata["error"] = str(exc)
            raise
        finally:
            try:
                stop_process(pm, 3)
                if game is not None and game.poll() is None and focus("terminate", game.pid):
                    try:
                        game.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                stop_process(game, 10)
                if game is not None and game.poll() is None:
                    raise base.BenchmarkError("EU IV remained running; recovery journal was retained")
                if installed:
                    metadata["working_save_changed"] = fixture.finish(run_dir)
            except BaseException as cleanup_error:
                metadata["status"] = "incomplete"
                metadata["cleanup_error"] = str(cleanup_error)
                raise
            finally:
                metadata["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
                manifest.write_text(json.dumps(metadata, indent=2, default=str)+"\n")
        return run_dir


def assess_runs(output_root: Path, runs: list[Path]) -> dict:
    if len(runs) != 3 or len(set(path.resolve() for path in runs)) != 3:
        raise base.BenchmarkError("Reproducibility assessment requires three distinct runs")
    scene = json.loads(SCENE_MANIFEST.read_text())
    data = []
    baseline_evidence = None
    for path in runs:
        metadata = json.loads((path / "manifest.json").read_text())
        status = metadata.get("status")
        if status == "complete_unverified_scene":
            if (Path(scene["source_run"]).resolve() != path.resolve() or
                    base.sha256(path / "ready-scene.png") != scene["sha256"]):
                raise base.BenchmarkError(f"Bootstrap scene was not registered from {path}")
        elif status != "complete":
            raise base.BenchmarkError(f"Run is not complete: {path}")
        evidence = metadata["evidence"]
        if baseline_evidence is None:
            baseline_evidence = evidence
        elif evidence != baseline_evidence:
            raise base.BenchmarkError(f"Run settings or instrumentation differ: {path}")
        summary = json.loads((path / "summary.json").read_text())
        if summary != metadata.get("summary"):
            raise base.BenchmarkError(f"Run summary and manifest differ: {path}")
        if (summary["swap_samples"] < 20 or summary["power_samples"] < 20 or
                any(summary[key] <= 0 for key in
                    ("eu4_cpu_ms_per_s", "combined_w", "median_swaps_s"))):
            raise base.BenchmarkError(f"Run lacks sufficient valid samples: {path}")
        data.append(summary)
    limits = {"eu4_cpu_ms_per_s": .03, "combined_w": .05, "median_swaps_s": .03}
    variation = {key: (max(item[key] for item in data)/min(item[key] for item in data)-1)
                 for key in limits}
    summary = {key: {"median": statistics.median(item[key] for item in data),
                     "mean": statistics.mean(item[key] for item in data),
                     "standard_deviation": statistics.pstdev(item[key] for item in data)}
               for key in limits}
    result = {"runs": [str(path) for path in runs],
              "summary": summary, "variation_fraction": variation,
              "stable": all(variation[key] < limit for key, limit in limits.items())}
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "autonomous-reproducibility.json").write_text(json.dumps(result, indent=2)+"\n")
    return result


def repeat(output_root: Path, evidence: dict, count: int = 3) -> dict:
    runs = [run(output_root, evidence) for _ in range(count)]
    return assess_runs(output_root, runs)


def main() -> int:
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("Interrupted by SIGTERM")
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="build and inspect the pinned fixture without launching EU IV")
    run_parser = sub.add_parser("run", help="one unattended 30-second Venice baseline")
    run_parser.add_argument("--output", default=str(ROOT / "results"))
    run_parser.add_argument("--bootstrap", action="store_true",
                            help="capture an initial scene for review before registration")
    repeat_parser = sub.add_parser("repeat", help="three unattended runs and reproducibility report")
    repeat_parser.add_argument("--output", default=str(ROOT / "results"))
    assess_parser = sub.add_parser("assess", help="assess three existing complete runs")
    assess_parser.add_argument("run_dirs", nargs=3)
    assess_parser.add_argument("--output", default=str(ROOT / "results"))
    register_parser = sub.add_parser("register-scene", help="register a reviewed bootstrap screenshot")
    register_parser.add_argument("run_dir")
    args = parser.parse_args()
    try:
        if args.command == "register-scene":
            print(register_scene(Path(args.run_dir).expanduser().resolve()))
            return 0
        if args.command == "assess":
            runs = [Path(item).expanduser().resolve() for item in args.run_dirs]
            print(json.dumps(assess_runs(Path(args.output).expanduser().resolve(), runs), indent=2))
            return 0
        evidence = preflight(require_privilege=args.command != "preflight")
        if args.command == "preflight":
            print(json.dumps(evidence, indent=2))
        elif args.command == "run":
            print(run(Path(args.output).expanduser().resolve(), evidence, args.bootstrap))
        else:
            print(json.dumps(repeat(Path(args.output).expanduser().resolve(), evidence), indent=2))
    except (base.BenchmarkError, OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
