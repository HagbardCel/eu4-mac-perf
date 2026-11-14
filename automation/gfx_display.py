"""BetterDisplay virtual-display isolation for background EU4 sessions.

The session owns *one* reusable virtual screen (default ``EU4 Test``).  It is
created lazily through ``betterdisplaycli``, validated through CoreGraphics, and
never replaces the physical primary display.  A background launch is a normal
``gfx_launch`` process: the same executable, injected observers, process group,
lease and ledger.  Only two things are added:

* ``SDL_VIDEO_WINDOW_POS`` places the SDL window on the virtual screen before it
  becomes visible (windowed mode, so the renderer's logical extent is
  unchanged);
* the game's ``settings.txt`` is switched to windowed at the requested extent
  for the duration of the session, then restored byte-for-byte.

No global input event is ever sent, no unrelated window is moved, and nothing
is activated.  Unexpected EU4 activation is recorded as a background
qualification failure rather than corrected by stealing focus back.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE_SOURCE = ROOT / "benchmark" / "eu4_display_probe.m"
PROBE = ROOT / "benchmark" / ".build" / "eu4_display_probe"
ISOLATE_SOURCE = ROOT / "benchmark" / "eu4_display_isolate.m"
ISOLATE = ROOT / "benchmark" / ".build" / "libeu4_display_isolate.dylib"
PLACEMENT_SOURCE = ROOT / "benchmark" / "eu4_display_placement.c"
PLACEMENT = ROOT / "benchmark" / ".build" / "libeu4_display_placement.dylib"
SCHEMA = "gfx-display-session/v1"
DEFAULT_NAME = "EU4 Test"
DEFAULT_MODE = (1920, 1200)
DEFAULT_WINDOW = (1728, 1117)
DEFAULT_MARGIN = 27
PHYSICAL_LABEL = "Built-in Display"


class DisplayError(RuntimeError):
    """A display operation failed closed; ``kind`` is stable for tests."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# CoreGraphics / BetterDisplay probes
# ---------------------------------------------------------------------------


def build_probe(force: bool = False) -> Path:
    """Compile the read-only display probe; fail closed when unavailable."""
    if sys.platform != "darwin":
        raise DisplayError("platform", "display isolation requires macOS")
    if PROBE.is_file() and not force:
        if PROBE.stat().st_mtime >= PROBE_SOURCE.stat().st_mtime:
            return PROBE
    if not PROBE_SOURCE.is_file():
        raise DisplayError("probe_source", f"missing {PROBE_SOURCE}")
    PROBE.parent.mkdir(parents=True, exist_ok=True)
    built = subprocess.run(
        [
            "clang",
            "-O2",
            "-Wall",
            "-Wextra",
            "-fobjc-arc",
            "-framework",
            "AppKit",
            "-framework",
            "CoreGraphics",
            "-framework",
            "Foundation",
            "-o",
            str(PROBE),
            str(PROBE_SOURCE),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if built.returncode != 0:
        raise DisplayError(
            "probe_build", (built.stderr or "display probe build failed").strip()
        )
    return PROBE


def ensure_isolation_library(force: bool = False) -> Path:
    """Build the narrow AppKit guard used only for background launches."""
    if sys.platform != "darwin":
        raise DisplayError("platform", "display isolation requires macOS")
    if ISOLATE.is_file() and not force:
        if ISOLATE.stat().st_mtime >= ISOLATE_SOURCE.stat().st_mtime:
            return ISOLATE
    if not ISOLATE_SOURCE.is_file():
        raise DisplayError("isolate_source", f"missing {ISOLATE_SOURCE}")
    ISOLATE.parent.mkdir(parents=True, exist_ok=True)
    built = subprocess.run(
        [
            "clang", "-arch", "x86_64", "-dynamiclib", "-O2",
            "-Wall", "-Wextra", "-fblocks",
            "-fobjc-arc", "-framework", "AppKit", "-framework",
            "CoreGraphics", "-framework", "Foundation",
            "-o", str(ISOLATE), str(ISOLATE_SOURCE),
        ],
        capture_output=True, text=True, check=False, timeout=120,
    )
    if built.returncode != 0:
        raise DisplayError(
            "isolate_build", (built.stderr or "display guard build failed").strip()
        )
    return ISOLATE


def ensure_placement_library(force: bool = False) -> Path:
    """Build the SDL call-site placement dylib (x86_64, like EU4)."""
    if sys.platform != "darwin":
        raise DisplayError("platform", "display placement requires macOS")
    if PLACEMENT.is_file() and not force:
        if PLACEMENT.stat().st_mtime >= PLACEMENT_SOURCE.stat().st_mtime:
            return PLACEMENT
    PLACEMENT.parent.mkdir(parents=True, exist_ok=True)
    built = subprocess.run(
        [
            "clang", "-arch", "x86_64", "-dynamiclib", "-O2",
            "-Wall", "-Wextra", "-I", str(ROOT / "benchmark"),
            "-framework", "CoreGraphics", "-framework", "Foundation",
            "-o", str(PLACEMENT), str(PLACEMENT_SOURCE),
            str(ROOT / "benchmark" / "eu4_metal_boot_patch.c"),
        ],
        capture_output=True, text=True, check=False, timeout=120,
    )
    if built.returncode != 0:
        raise DisplayError(
            "placement_build", (built.stderr or "placement build failed").strip())
    return PLACEMENT


def _probe(operation: str, pid: int | None = None, *, timeout: float = 15.0) -> dict:
    executable = build_probe()
    argv = [str(executable), operation]
    if pid is not None:
        argv += ["--pid", str(pid)]
    ran = subprocess.run(
        argv, capture_output=True, text=True, check=False, timeout=timeout
    )
    if ran.returncode != 0:
        raise DisplayError(
            "probe_failed",
            f"{operation} exited {ran.returncode}: {(ran.stderr or '').strip()[-500:]}",
        )
    try:
        value = json.loads(ran.stdout)
    except json.JSONDecodeError as exc:
        raise DisplayError("probe_output", f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise DisplayError("probe_output", "probe did not return an object")
    return value


def snapshot(pid: int | None = None) -> dict:
    """Displays, on-screen windows (optionally one PID's) and focus."""
    return _probe("snapshot", pid)


def displays() -> list[dict]:
    return list(_probe("displays").get("displays") or [])


@dataclasses.dataclass(frozen=True)
class Display:
    display_id: int
    name: str
    origin_x: float
    origin_y: float
    width: float
    height: float
    pixel_width: int
    pixel_height: int
    hidpi: bool
    main: bool

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.origin_x, self.origin_y, self.width, self.height)

    def contains(self, rect: tuple[float, float, float, float]) -> bool:
        x, y, width, height = rect
        return (
            x >= self.origin_x
            and y >= self.origin_y
            and x + width <= self.origin_x + self.width
            and y + height <= self.origin_y + self.height
        )


def _display_from_record(record: dict, name: str) -> Display:
    return Display(
        display_id=int(record.get("display_id") or 0),
        name=name,
        origin_x=float(record.get("origin_x") or 0),
        origin_y=float(record.get("origin_y") or 0),
        width=float(record.get("width") or 0),
        height=float(record.get("height") or 0),
        pixel_width=int(record.get("pixel_width") or 0),
        pixel_height=int(record.get("pixel_height") or 0),
        hidpi=bool(record.get("hidpi")),
        main=bool(record.get("main")),
    )


def find_display(name: str = DEFAULT_NAME) -> Display | None:
    """Resolve a virtual display by identifier geometry.

    CoreGraphics does not expose the BetterDisplay name, so the display is
    resolved from BetterDisplay's identifiers (displayID) and then matched to
    the active CoreGraphics record.  Returns ``None`` when it is not connected.
    """
    identifier = _identifier(name)
    if identifier is None:
        return None
    display_id = identifier.get("displayID")
    try:
        display_id = int(display_id)
    except (TypeError, ValueError):
        return None
    payload = _probe("displays")
    for record in payload.get("displays") or []:
        if int(record.get("display_id") or 0) == display_id:
            return _display_from_record(record, name)
    return None


def main_display() -> Display:
    payload = _probe("displays")
    main_id = payload.get("main_display_id")
    for record in payload.get("displays") or []:
        if int(record.get("display_id") or 0) == int(main_id or 0):
            return _display_from_record(record, PHYSICAL_LABEL)
    raise DisplayError("main_display", "CoreGraphics did not expose a main display")


# ---------------------------------------------------------------------------
# BetterDisplay CLI
# ---------------------------------------------------------------------------


def _cli() -> str:
    executable = shutil.which("betterdisplaycli")
    if not executable:
        raise DisplayError(
            "betterdisplay_missing",
            "betterdisplaycli is not installed; background EU4 mode needs BetterDisplay",
        )
    return executable


def _run_cli(*arguments: str, timeout: float = 30.0) -> str:
    executable = _cli()
    ran = subprocess.run(
        [executable, *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    output = (ran.stdout or "").strip()
    error = (ran.stderr or "").strip()
    if ran.returncode != 0 or "Failed." in output or "Failed." in error:
        raise DisplayError(
            "betterdisplay_failed",
            f"{' '.join(arguments[:2])}: {(error or output)[-600:]}",
        )
    return output


def _identifiers() -> list[dict]:
    text = _run_cli("get", "-identifiers")
    # The CLI emits a JSON array without surrounding brackets.
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        try:
            value = json.loads("[" + text + "]")
        except json.JSONDecodeError as exc:
            raise DisplayError(
                "betterdisplay_output", f"unreadable identifiers: {exc}"
            ) from exc
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise DisplayError("betterdisplay_output", "identifiers are not a list")
    return [item for item in value if isinstance(item, dict)]


def _identifier(name: str) -> dict | None:
    wanted = name.strip().casefold()
    for item in _identifiers():
        if (
            str(item.get("deviceType") or "").casefold() == "virtualscreen"
            and str(item.get("name") or "").strip().casefold() == wanted
        ):
            return item
    return None


def virtual_screens() -> list[dict]:
    return [
        item
        for item in _identifiers()
        if str(item.get("deviceType") or "").casefold() == "virtualscreen"
    ]


def physical_changed(before: dict, after: dict) -> bool:
    """Whether the physical primary display identity/geometry moved."""
    keys = ("display_id", "origin_x", "origin_y", "width", "height")
    return any(before.get(key) != after.get(key) for key in keys)


def mode_list(name: str = DEFAULT_NAME) -> list[dict]:
    text = _run_cli("get", f"-name={name}", "-displayModeList")
    result: list[dict] = []
    for line in text.splitlines():
        match = re.match(
            r"\s*(\d+)\s*-\s*(\d+)x(\d+)\s+([\d.]+)Hz\s+(\d+)bpc\s*(.*)",
            line,
        )
        if not match:
            continue
        result.append(
            {
                "mode": int(match.group(1)),
                "width": int(match.group(2)),
                "height": int(match.group(3)),
                "refresh_hz": float(match.group(4)),
                "bpc": int(match.group(5)),
                "flags": [part.lower() for part in match.group(6).split()],
            }
        )
    return result


def _resolution_list(extra: tuple[int, int]) -> str:
    values = ["1280x720", "1728x1117", str(extra[0]) + "x" + str(extra[1])]
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return ",".join(seen)


def ensure_display(
    name: str = DEFAULT_NAME,
    *,
    width: int = DEFAULT_MODE[0],
    height: int = DEFAULT_MODE[1],
    hidpi: bool = False,
    timeout: float = 30.0,
) -> dict:
    """Create (once) and qualify the reusable virtual display.

    Returns a record with the qualified ``Display``, the observed physical main
    display and the CLI steps.  A missing CLI/app, an unexpected main-display
    change or a geometry mismatch raises :class:`DisplayError`; nothing is
    guessed and no physical setting is modified.
    """
    physical = main_display()
    physical_before = physical.as_dict()
    steps: list[dict] = []
    existing = find_display(name)
    if (
        existing is not None
        and (int(existing.width), int(existing.height)) == (width, height)
        and existing.hidpi == hidpi
        and not existing.main
    ):
        return {
            "schema": "gfx-display/v1",
            "name": name,
            "display": existing.as_dict(),
            "mode": next(
                (item for item in mode_list(name)
                 if item["width"] == width and item["height"] == height),
                None),
            "physical": physical_before,
            "physical_unchanged": True,
            "steps": [{"action": "reuse"}],
            "prepared_at": _now(),
        }
    identifier = _identifier(name)
    if identifier is None:
        _run_cli(
            "create",
            "-type=VirtualScreen",
            f"-virtualScreenName={name}",
            "-aspectWidth=16",
            "-aspectHeight=9",
            f"-virtualScreenHiDPI={'on' if hidpi else 'off'}",
        )
        steps.append({"action": "create", "name": name})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and _identifier(name) is None:
            time.sleep(0.25)
    _run_cli(
        "set",
        f"-name={name}",
        "-useResolutionList=on",
        f"-resolutionList={_resolution_list((width, height))}",
        f"-virtualScreenHiDPI={'on' if hidpi else 'off'}",
    )
    steps.append({"action": "configure", "resolutions": _resolution_list((width, height))})
    _run_cli("set", f"-name={name}", "-connected=on")
    steps.append({"action": "connect"})

    deadline = time.monotonic() + timeout
    selected: dict | None = None
    while time.monotonic() < deadline:
        modes = mode_list(name)
        for mode in modes:
            if mode["width"] == width and mode["height"] == height:
                selected = mode
                break
        if selected is not None:
            break
        time.sleep(0.25)
    if selected is None:
        raise DisplayError(
            "mode_missing", f"{name} exposes no {width}x{height} mode"
        )
    _run_cli(
        "set", f"-name={name}", f"-displayModeNumber={selected['mode']}"
    )
    steps.append({"action": "select_mode", "mode": selected})

    display = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        display = find_display(name)
        if display is not None and display.width > 0:
            break
        time.sleep(0.25)
    if display is None:
        raise DisplayError("display_offline", f"{name} did not become an active display")
    if display.main:
        raise DisplayError(
            "main_display_changed",
            f"{name} became the main display; the physical screen must stay primary",
        )
    if (int(display.width), int(display.height)) != (width, height):
        raise DisplayError(
            "geometry",
            f"{name} is {int(display.width)}x{int(display.height)}, "
            f"expected {width}x{height}",
        )
    if display.hidpi != hidpi:
        raise DisplayError(
            "hidpi",
            f"{name} HiDPI={display.hidpi}, expected {hidpi}",
        )
    physical_after = main_display().as_dict()
    if physical_changed(physical_before, physical_after):
        raise DisplayError(
            "physical_changed",
            "the physical primary display geometry changed while preparing the virtual display",
        )
    return {
        "schema": "gfx-display/v1",
        "name": name,
        "display": display.as_dict(),
        "mode": selected,
        "physical": physical_after,
        "physical_unchanged": True,
        "steps": steps,
        "prepared_at": _now(),
    }


# ---------------------------------------------------------------------------
# Window placement and observation
# ---------------------------------------------------------------------------


def window_origin(
    display: Display,
    window_size: tuple[int, int] = DEFAULT_WINDOW,
    *,
    margin: int = DEFAULT_MARGIN,
) -> tuple[int, int]:
    """Place the window inside the virtual screen with room for a title bar."""
    width, height = window_size
    # SDL's position is the window frame origin; leave a title-bar allowance.
    frame_height = height + 28
    if width + 2 * margin > display.width or frame_height + 2 * margin > display.height:
        raise DisplayError(
            "window_too_large",
            f"{width}x{height} does not fit {int(display.width)}x{int(display.height)}",
        )
    x = int(display.origin_x + margin)
    y = int(display.origin_y + margin)
    return (x, y)


def launch_environment(
    display: Display,
    *,
    window_size: tuple[int, int] = DEFAULT_WINDOW,
    margin: int = DEFAULT_MARGIN,
) -> dict:
    """Environment additions for a background launch (SDL and provenance)."""
    x, y = window_origin(display, window_size, margin=margin)
    return {
        "SDL_VIDEO_WINDOW_POS": f"{x},{y}",
        "EU4_DISPLAY_MODE": "background",
        "EU4_DISPLAY_NAME": display.name,
        "EU4_DISPLAY_ID": str(display.display_id),
        "EU4_DISPLAY_WINDOW_SIZE": f"{window_size[0]}x{window_size[1]}",
        "EU4_DISPLAY_MARGIN": str(margin),
    }


def _window_rect(window: dict) -> tuple[float, float, float, float] | None:
    try:
        return (
            float(window["x"]),
            float(window["y"]),
            float(window["width"]),
            float(window["height"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def verify_window(
    pid: int,
    display: Display,
    window_size: tuple[int, int] = DEFAULT_WINDOW,
    *,
    timeout: float = 60.0,
    interval: float = 0.5,
) -> dict:
    """Wait for the EU4 window and verify it is on the virtual display.

    Evidence records window bounds, the expected client extent, whether the
    process ever became frontmost, and the display identity at each sample.
    """
    deadline = time.monotonic() + timeout
    activation_events: list[dict] = []
    disconnects = 0
    window: dict | None = None
    samples = 0
    while time.monotonic() < deadline:
        samples += 1
        try:
            payload = snapshot(pid)
        except DisplayError:
            return {
                "ok": False,
                "reason": "probe_unavailable",
                "pid": pid,
                "samples": samples,
            }
        if int(payload.get("frontmost_pid") or 0) == pid:
            activation_events.append({"at": _now(), "frontmost_pid": pid})
        active_ids = {
            int(item.get("display_id") or 0)
            for item in payload.get("displays") or []
        }
        if display.display_id not in active_ids:
            disconnects += 1
            return {
                "ok": False,
                "reason": "display_disconnected",
                "pid": pid,
                "display": display.as_dict(),
                "samples": samples,
                "activations": activation_events,
            }
        owned = [
            item
            for item in payload.get("windows") or []
            if int(item.get("owner_pid") or 0) == pid
            and (rect := _window_rect(item)) is not None
            and rect[2] >= 100 and rect[3] >= 100
        ]
        if owned:
            window = max(
                owned,
                key=lambda item: (item["width"] or 0) * (item["height"] or 0),
            )
            break
        time.sleep(interval)
    if window is None:
        return {
            "ok": False,
            "reason": "window_timeout",
            "pid": pid,
            "display": display.as_dict(),
            "samples": samples,
            "activations": activation_events,
        }
    rect = _window_rect(window) or (0, 0, 0, 0)
    contained = display.contains(rect)
    # The drawable extent must not change.  Allow window decorations, but
    # reject a fullscreen window that silently adopted the display mode.
    width_ok = abs(rect[2] - window_size[0]) <= 64
    height_ok = window_size[1] <= rect[3] <= window_size[1] + 120
    expected_origin = window_origin(display, window_size)
    origin_error = (
        abs(rect[0] - expected_origin[0]) + abs(rect[1] - expected_origin[1])
    )
    ok = contained and width_ok and height_ok and not activation_events and disconnects == 0
    reason = None
    if activation_events:
        reason = "unexpected_activation"
    elif disconnects:
        reason = "display_disconnected"
    elif not contained:
        reason = "window_outside_display"
    elif not (width_ok and height_ok):
        reason = "window_too_small"
    return {
        "ok": ok,
        "reason": reason,
        "pid": pid,
        "display": display.as_dict(),
        "window": window,
        "window_rect": list(rect),
        "expected_client_size": [window_size[0], window_size[1]],
        "expected_origin": list(expected_origin),
        "origin_error": origin_error,
        "contained": contained,
        "activations": activation_events,
        "samples": samples,
        "physical_main_unchanged": True,
    }


class WindowMonitor:
    """Sample window/display/focus for the whole session without input."""

    def __init__(self, pid: int, display: Display,
                 window_size: tuple[int, int], *, interval: float = 0.5):
        self.pid = pid
        self.display = display
        self.window_size = window_size
        self.interval = interval
        self.samples = 0
        self.activations: list[dict] = []
        self.disconnects: list[dict] = []
        self.window_issues: list[dict] = []
        self.seen_window = False
        self._stop = False
        self._thread = None

    def start(self):
        import threading

        def run():
            while not self._stop:
                self.sample_once()
                time.sleep(self.interval)

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return self

    def sample_once(self) -> dict:
        self.samples += 1
        try:
            payload = snapshot(self.pid)
        except DisplayError as exc:
            self.window_issues.append({"at": _now(), "error": str(exc)})
            return {}
        if int(payload.get("frontmost_pid") or 0) == self.pid:
            self.activations.append({"at": _now(), "frontmost_pid": self.pid})
        active_ids = {
            int(item.get("display_id") or 0)
            for item in payload.get("displays") or []
        }
        if self.display.display_id not in active_ids:
            self.disconnects.append({
                "at": _now(), "display_id": self.display.display_id,
            })
        owned = [
            item for item in payload.get("windows") or []
            if int(item.get("owner_pid") or 0) == self.pid
            and (rect := _window_rect(item)) is not None
            and rect[2] >= 100 and rect[3] >= 100
        ]
        if owned:
            self.seen_window = True
            window = max(
                owned, key=lambda item: (item["width"] or 0) * (item["height"] or 0))
            rect = _window_rect(window) or (0, 0, 0, 0)
            if rect[2] < 800 or rect[3] < 600:
                return payload
            if not self.display.contains(rect):
                self.window_issues.append({
                    "at": _now(), "kind": "outside_display",
                    "window_rect": list(rect),
                })
            expected_w, expected_h = self.window_size
            if not (abs(rect[2] - expected_w) <= 64
                    and expected_h <= rect[3] <= expected_h + 120):
                self.window_issues.append({
                    "at": _now(), "kind": "dimension_changed",
                    "window_rect": list(rect),
                    "expected_size": [expected_w, expected_h],
                })
        elif self.seen_window:
            self.window_issues.append({"at": _now(), "kind": "window_absent"})
        return payload

    def stop(self) -> dict:
        self._stop = True
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval * 2))
        return self.summary()

    def summary(self) -> dict:
        return {
            "schema": "gfx-window-monitor/v1",
            "samples": self.samples,
            "activations": self.activations,
            "disconnects": self.disconnects,
            "window_issues": self.window_issues[:64],
            "unexpected_activation": bool(self.activations),
            "display_disconnected": bool(self.disconnects),
            "placement_failed": bool(self.window_issues),
        }


# ---------------------------------------------------------------------------
# settings.txt windowed preparation
# ---------------------------------------------------------------------------


def find_user_settings(user_data: Path | str) -> Path:
    path = Path(user_data) / "settings.txt"
    if not path.is_file():
        raise DisplayError("settings_missing", f"no settings.txt under {user_data}")
    return path


def _rewrite_graphics(text: str, width: int, height: int) -> tuple[str, dict]:
    lines = text.splitlines(keepends=True)
    in_graphics = False
    in_size = False
    depth = 0
    changed: dict = {"fullScreen": None, "size": [None, None]}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^graphics\s*=\s*\{", stripped):
            in_graphics = True
            depth = stripped.count("{") - stripped.count("}")
            continue
        if not in_graphics:
            continue
        if re.match(r"^size\s*=\s*\{", stripped):
            in_size = True
            continue
        if in_size and re.match(r"^x\s*=", stripped):
            changed["size"][0] = stripped
            lines[index] = re.sub(r"^(\s*x\s*=\s*)\d+", rf"\g<1>{width}", line)
            continue
        if in_size and re.match(r"^y\s*=", stripped):
            changed["size"][1] = stripped
            lines[index] = re.sub(r"^(\s*y\s*=\s*)\d+", rf"\g<1>{height}", line)
            continue
        if in_size and stripped.startswith("}"):
            in_size = False
            continue
        if re.match(r"^fullScreen\s*=", stripped):
            changed["fullScreen"] = stripped
            lines[index] = re.sub(r"^(\s*fullScreen\s*=\s*)\S+", r"\g<1>no", line)
        depth += stripped.count("{") - stripped.count("}")
        if depth <= 0:
            in_graphics = False
    return "".join(lines), changed


def _atomic_write(path: Path, payload: bytes) -> None:
    """Durably replace a shared configuration file."""
    temporary = path.with_name(path.name + ".harness-tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    try:
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        pass


def _graphics_values(text: str) -> dict:
    """Read the controlled graphics fields from a settings file."""
    values = {"fullScreen": None, "x": None, "y": None}
    in_graphics = False
    in_size = False
    depth = 0
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^graphics\s*=\s*\{", stripped):
            in_graphics = True
            depth = stripped.count("{") - stripped.count("}")
            continue
        if not in_graphics:
            continue
        if re.match(r"^size\s*=\s*\{", stripped):
            in_size = True
            depth += stripped.count("{") - stripped.count("}")
            continue
        if in_size and re.match(r"^x\s*=", stripped):
            values["x"] = stripped.split("=", 1)[1].strip()
        elif in_size and re.match(r"^y\s*=", stripped):
            values["y"] = stripped.split("=", 1)[1].strip()
        elif re.match(r"^fullScreen\s*=", stripped):
            values["fullScreen"] = stripped.split("=", 1)[1].strip()
        if in_size and stripped.startswith("}"):
            in_size = False
        depth += stripped.count("{") - stripped.count("}")
        if depth <= 0:
            in_graphics = False
    return values


def _restore_controlled_fields(current: str, original: str) -> str:
    """Return current with only fullscreen/size replaced by original values."""
    wanted = _graphics_values(original)
    lines = current.splitlines(keepends=True)
    in_graphics = False
    in_size = False
    depth = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^graphics\s*=\s*\{", stripped):
            in_graphics = True
            depth = stripped.count("{") - stripped.count("}")
            continue
        if not in_graphics:
            continue
        ending = "\n" if line.endswith("\n") else ""
        if re.match(r"^size\s*=\s*\{", stripped):
            in_size = True
            depth += stripped.count("{") - stripped.count("}")
            continue
        if in_size and re.match(r"^x\s*=", stripped) and wanted["x"] is not None:
            lines[index] = re.sub(r"^(\s*x\s*=\s*).*$", rf"\g<1>{wanted['x']}", line.rstrip("\n")) + ending
        elif in_size and re.match(r"^y\s*=", stripped) and wanted["y"] is not None:
            lines[index] = re.sub(r"^(\s*y\s*=\s*).*$", rf"\g<1>{wanted['y']}", line.rstrip("\n")) + ending
        elif re.match(r"^fullScreen\s*=", stripped) and wanted["fullScreen"] is not None:
            lines[index] = re.sub(r"^(\s*fullScreen\s*=\s*).*$", rf"\g<1>{wanted['fullScreen']}", line.rstrip("\n")) + ending
        if in_size and stripped.startswith("}"):
            in_size = False
        depth += stripped.count("{") - stripped.count("}")
        if depth <= 0:
            in_graphics = False
    return "".join(lines)


def _matches_windowed_intent(payload: bytes, width: int, height: int) -> bool:
    """Whether a rewritten settings file still expresses our windowed intent."""
    try:
        text = payload.decode("utf-8", errors="surrogateescape")
    except Exception:
        return False
    return (
        re.search(r"^\s*fullScreen\s*=\s*no\s*$", text, re.M) is not None
        and re.search(rf"^\s*x\s*=\s*{width}\s*$", text, re.M) is not None
        and re.search(rf"^\s*y\s*=\s*{height}\s*$", text, re.M) is not None
    )


def prepare_windowed_settings(
    user_data: Path | str,
    *,
    width: int = DEFAULT_WINDOW[0],
    height: int = DEFAULT_WINDOW[1],
    run_dir: Path | str | None = None,
) -> dict:
    """Switch EU4 to windowed at ``width``x``height`` and keep a restorable copy."""
    path = find_user_settings(user_data)
    original = path.read_bytes()
    text = original.decode("utf-8", errors="surrogateescape")
    rewritten, changed = _rewrite_graphics(text, width, height)
    if rewritten == text:
        return {
            "changed": False,
            "path": str(path),
            "original_sha256": _sha256_bytes(original),
            "settings_sha256": _sha256_bytes(original),
        }
    if run_dir is None:
        raise DisplayError(
            "settings_backup_required",
            "a session run directory is required to modify the shared settings",
        )
    backup_dir = Path(run_dir) / "display"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / "settings.txt.original"
    _atomic_write(backup, original)
    modified = rewritten.encode("utf-8", errors="surrogateescape")
    _atomic_write(path, modified)
    return {
        "changed": True,
        "path": str(path),
        "backup": str(backup),
        "original_sha256": _sha256_bytes(original),
        "settings_sha256": _sha256_bytes(modified),
        "previous": changed,
        "windowed": {"width": width, "height": height},
    }


def restore_settings(record: dict) -> dict:
    """Restore the exact bytes recorded by :func:`prepare_windowed_settings`."""
    if not record.get("changed"):
        return {"restored": False, "reason": "unchanged"}
    backup = record.get("backup")
    path = Path(record["path"])
    if not backup or not Path(backup).is_file():
        raise DisplayError("settings_backup_missing", f"cannot restore {path}")
    original = Path(backup).read_bytes()
    if _sha256_bytes(original) != record.get("original_sha256"):
        raise DisplayError("settings_backup_corrupt", f"backup digest mismatch for {path}")
    try:
        current = path.read_bytes()
    except OSError as exc:
        raise DisplayError("settings_unreadable", str(exc)) from exc
    current_digest = _sha256_bytes(current)
    original_digest = _sha256_bytes(original)
    if current_digest == original_digest:
        return {
            "restored": True,
            "reason": "already_original",
            "path": str(path),
            "sha256": original_digest,
        }
    if current_digest != record.get("settings_sha256"):
        try:
            current_values = _graphics_values(
                current.decode("utf-8", errors="surrogateescape"))
            original_values = _graphics_values(
                original.decode("utf-8", errors="surrogateescape"))
        except UnicodeError:
            current_values = original_values = None
        if current_values is not None and current_values == original_values:
            return {
                "restored": True,
                "reason": "controlled_fields_already_original",
                "preserved_unrelated_changes": current != original,
                "path": str(path),
                "sha256": current_digest,
                "original_sha256": original_digest,
            }
        windowed = record.get("windowed") or {}
        game_rewrite = (
            isinstance(windowed.get("width"), int)
            and isinstance(windowed.get("height"), int)
            and _matches_windowed_intent(
                current, windowed["width"], windowed["height"])
        )
        if not game_rewrite:
            # Another actor edited the shared file.  Do not overwrite their
            # work; preserve the backup and report the conflict.
            return {
                "restored": False,
                "conflict": True,
                "path": str(path),
                "backup": str(backup),
                "expected_sha256": record.get("settings_sha256"),
                "current_sha256": current_digest,
                "original_sha256": record.get("original_sha256"),
            }
        # The game preserved our windowed intent but may have changed other
        # fields.  Revert only the fields this harness controls.
        try:
            merged_text = _restore_controlled_fields(
                current.decode("utf-8", errors="surrogateescape"),
                original.decode("utf-8", errors="surrogateescape"),
            )
        except (UnicodeError, ValueError) as exc:
            raise DisplayError("settings_merge", str(exc)) from exc
        merged = merged_text.encode("utf-8", errors="surrogateescape")
        _atomic_write(path, merged)
        return {
            "restored": True,
            "reason": "controlled_fields_restored",
            "preserved_unrelated_changes": merged != original,
            "path": str(path),
            "sha256": _sha256_bytes(merged),
            "original_sha256": original_digest,
        }
    else:
        reason = "restored"
    _atomic_write(path, original)
    return {
        "restored": True,
        "reason": reason,
        "path": str(path),
        "sha256": _sha256_bytes(original),
    }


# ---------------------------------------------------------------------------
# Session object used by gfx_session / gfx_launch
# ---------------------------------------------------------------------------


class BackgroundSession:
    """Prepared background display state with a guaranteed restore path."""

    def __init__(self, display: Display, window_size: tuple[int, int],
                 *, focus_guard: bool = False):
        self.display = display
        self.window_size = window_size
        self.env = launch_environment(display, window_size=window_size)
        self.focus_guard = bool(focus_guard)
        self.isolation_library = (
            ensure_isolation_library() if self.focus_guard else None
        )
        self.placement_library = ensure_placement_library()
        self.record = {
            "focus_guard": self.focus_guard,
            "placement_library": str(self.placement_library),
            "placement_library_sha256": _sha256_bytes(
                self.placement_library.read_bytes()),
            "isolation_library": (
                str(self.isolation_library) if self.isolation_library else None
            ),
            "isolation_library_sha256": (
                _sha256_bytes(self.isolation_library.read_bytes())
                if self.isolation_library else None
            ),
        }
        self.settings: dict | None = None
        self.record: dict = {
            **getattr(self, "record", {}),
            "schema": SCHEMA,
            "mode": "background",
            "display": display.as_dict(),
            "window_size": list(window_size),
            "env": dict(self.env),
            "prepared_at": _now(),
        }

    def prepare_settings(self, user_data: Path | str, run_dir: Path | str) -> dict:
        self.settings = prepare_windowed_settings(
            user_data,
            width=self.window_size[0],
            height=self.window_size[1],
            run_dir=run_dir,
        )
        self.record["settings"] = self.settings
        return self.settings

    def restore(self) -> dict:
        result: dict = {"restored": False}
        if self.settings is not None:
            try:
                result = restore_settings(self.settings)
            except DisplayError as exc:
                result = {"restored": False, "error": str(exc), "kind": exc.kind}
        self.record["restored_at"] = _now()
        self.record["restore"] = result
        return result

    def as_dict(self) -> dict:
        return dict(self.record)


@dataclasses.dataclass(frozen=True)
class BackgroundPlan:
    """Validated background request; does not touch shared game settings."""

    user_data: str
    name: str
    window_size: tuple[int, int]
    width: int
    height: int
    focus_guard: bool

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "user_data": self.user_data,
            "window_size": list(self.window_size),
            "mode": [self.width, self.height],
            "focus_guard": self.focus_guard,
        }


def plan_background(
    user_data: Path | str,
    *,
    name: str = DEFAULT_NAME,
    window_size: tuple[int, int] = DEFAULT_WINDOW,
    width: int = DEFAULT_MODE[0],
    height: int = DEFAULT_MODE[1],
    focus_guard: bool = False,
) -> BackgroundPlan:
    """Validate the display request without modifying any shared file.

    This runs before the host-wide launch lease is acquired.  It checks the
    BetterDisplay service and the physical primary display, but creates or
    reconfigures nothing and never writes ``settings.txt``.
    """
    _cli()
    physical = main_display()
    if physical.main is not True:
        raise DisplayError("main_display", "the physical display is not primary")
    find_user_settings(user_data)
    return BackgroundPlan(
        user_data=str(Path(user_data)),
        name=name,
        window_size=tuple(window_size),
        width=int(width),
        height=int(height),
        focus_guard=bool(focus_guard),
    )


def activate_background(plan: BackgroundPlan, run_dir: Path | str) -> BackgroundSession:
    """Prepare the display, back up settings, then switch the game to windowed."""
    qualified = ensure_display(plan.name, width=plan.width, height=plan.height)
    display = Display(**qualified["display"])
    session = BackgroundSession(display, plan.window_size,
                                 focus_guard=plan.focus_guard)
    session.env["EU4_DISPLAY_WINDOW_TRACE"] = str(
        Path(run_dir) / "display" / "window-trace.jsonl")
    session.env["EU4_DISPLAY_TRACE"] = str(
        Path(run_dir) / "display" / "appkit-trace.jsonl")
    session.record["qualified"] = qualified
    try:
        session.prepare_settings(plan.user_data, run_dir)
        path = Path(run_dir) / "display" / "display-session.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(session.as_dict(), indent=2) + "\n", encoding="utf-8")
    except BaseException:
        # A settings write may have succeeded before a later step failed.
        session.restore()
        raise
    return session


def prepare_background(
    user_data: Path | str,
    run_dir: Path | str,
    *,
    name: str = DEFAULT_NAME,
    window_size: tuple[int, int] = DEFAULT_WINDOW,
    width: int = DEFAULT_MODE[0],
    height: int = DEFAULT_MODE[1],
    focus_guard: bool = False,
) -> BackgroundSession:
    """Compatibility wrapper: plan and activate in one call."""
    plan = plan_background(
        user_data, name=name, window_size=window_size, width=width,
        height=height, focus_guard=focus_guard,
    )
    return activate_background(plan, run_dir)
