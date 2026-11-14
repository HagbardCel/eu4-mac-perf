"""Compact, validated scenario description for a dual-process campaign.

Both processes receive one scenario.  The description pins executable/content
identity, backend configuration, logical extent, start state, observation
interval, synchronization mode and comparison policy.  Unknown or malformed
fields fail closed; optional fields have explicit defaults.

Nothing here launches a process or reads a framebuffer.  It is the shared
contract between the CLI, the coordinator and an agent tool.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "gfx-dual-scenario/v1"
MODES = ("streaming", "lockstep")
EXPERIMENTS = ("differential", "consistency")
POLICIES = ("exact", "tolerant", "diagnostic")
PROFILES = ("early", "targeted", "qualification")
STARTS = ("frontend", "save")
DEFAULT_REGIONS = {
    "map": {"rect": [0, 700, 0, 1728], "policy": "structural"},
    "ui": {"rect": [700, 1117, 0, 1728], "policy": "tolerant"},
}


class ScenarioError(ValueError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _require(value: Any, kind: type, field: str, *, minimum: float | None = None,
             maximum: float | None = None) -> Any:
    if kind is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ScenarioError("type", f"{field} must be a number")
        value = float(value)
    elif type(value) is not kind:
        raise ScenarioError("type", f"{field} must be {kind.__name__}")
    if minimum is not None and value < minimum:
        raise ScenarioError("range", f"{field} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ScenarioError("range", f"{field} must be <= {maximum}")
    return value


def _optional(value: Any, kind: type, field: str, default: Any = None,
              *, minimum: float | None = None, maximum: float | None = None) -> Any:
    if value is None:
        return default
    return _require(value, kind, field, minimum=minimum, maximum=maximum)


def _check_keys(value: dict, allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ScenarioError("unknown_field", f"{field}: {', '.join(unknown)}")


def normalize(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ScenarioError("type", "scenario must be an object")
    if value.get("schema") not in (None, SCHEMA):
        raise ScenarioError("schema", f"expected {SCHEMA}")
    _check_keys(value, {
        "schema", "name", "executable_sha256", "executable", "args",
        "reference_backend", "candidate_backend", "logical_extent",
        "start", "game_config", "input_events", "settle", "observation",
        "comparison", "mode", "profile", "authorization", "digest", "experiment",
    }, "scenario")

    executable = value.get("executable")
    if executable is not None:
        executable = str(executable)
        if not executable.startswith("/"):
            raise ScenarioError("path", "executable must be absolute")
    sha = value.get("executable_sha256")
    if not isinstance(sha, str) or len(sha) != 64 or any(
        ch not in "0123456789abcdef" for ch in sha.lower()
    ):
        raise ScenarioError("identity", "executable_sha256 must be 64 hex characters")

    args = value.get("args", ["-enabletelemetry", "-gdpr-compliant"])
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ScenarioError("type", "args must be a list of strings")

    extent = value.get("logical_extent")
    if not isinstance(extent, list) or len(extent) != 2:
        raise ScenarioError("type", "logical_extent must be [width, height]")
    width = _require(extent[0], int, "logical_extent[0]", minimum=64, maximum=8192)
    height = _require(extent[1], int, "logical_extent[1]", minimum=64, maximum=8192)

    start = value.get("start", {"kind": "frontend"})
    if not isinstance(start, dict):
        raise ScenarioError("type", "start must be an object")
    _check_keys(start, {"kind", "save", "pause", "game_speed"}, "start")
    kind = start.get("kind", "frontend")
    if kind not in STARTS:
        raise ScenarioError("enum", f"start.kind must be one of {STARTS}")
    save = start.get("save")
    if kind == "save":
        if not isinstance(save, str) or not save:
            raise ScenarioError("missing", "start.save is required for a save start")
    elif save is not None:
        raise ScenarioError("invalid", "start.save is only valid for a save start")
    pause = _optional(start.get("pause"), bool, "start.pause", True)
    speed = _optional(start.get("game_speed"), int, "start.game_speed", 1,
                      minimum=0, maximum=5)

    game_config = value.get("game_config", {})
    if not isinstance(game_config, dict):
        raise ScenarioError("type", "game_config must be an object")
    for key, item in game_config.items():
        if not isinstance(key, str) or not isinstance(item, (str, int, float, bool)):
            raise ScenarioError("type", "game_config values must be scalar")

    events = value.get("input_events", [])
    if not isinstance(events, list):
        raise ScenarioError("type", "input_events must be a list")
    for index, event in enumerate(events):
        if not isinstance(event, dict) or not isinstance(event.get("at_frame"), int):
            raise ScenarioError("type", f"input_events[{index}] needs an integer at_frame")
        if event.get("at_frame", -1) < 0:
            raise ScenarioError("range", f"input_events[{index}].at_frame must be >= 0")
        if not isinstance(event.get("event"), str):
            raise ScenarioError("type", f"input_events[{index}] needs an event name")

    settle = value.get("settle", {})
    if not isinstance(settle, dict):
        raise ScenarioError("type", "settle must be an object")
    _check_keys(settle, {"min_frames", "stable_frames", "timeout_seconds",
                         "min_draws", "draw_tolerance"}, "settle")
    min_frames = _optional(settle.get("min_frames"), int, "settle.min_frames", 3,
                           minimum=1, maximum=10000)
    stable_frames = _optional(settle.get("stable_frames"), int, "settle.stable_frames",
                              2, minimum=1, maximum=10000)
    settle_timeout = _optional(settle.get("timeout_seconds"), float,
                               "settle.timeout_seconds", 90.0, minimum=1.0,
                               maximum=3600.0)
    settle_min_draws = _optional(settle.get("min_draws"), int, "settle.min_draws",
                                 0, minimum=0, maximum=100000)
    settle_draw_tolerance = _optional(settle.get("draw_tolerance"), float,
                                      "settle.draw_tolerance", 0.25,
                                      minimum=0.0, maximum=1.0)

    observation = value.get("observation", {})
    if not isinstance(observation, dict):
        raise ScenarioError("type", "observation must be an object")
    _check_keys(observation, {
        "max_frames", "max_runtime_seconds", "ring", "capture_every_frame",
        "preview", "dump_on_stop", "stall_seconds",
    }, "observation")
    max_frames = _optional(observation.get("max_frames"), int, "observation.max_frames",
                           100, minimum=1, maximum=100000)
    max_runtime = _optional(observation.get("max_runtime_seconds"), float,
                            "observation.max_runtime_seconds", 180.0,
                            minimum=5.0, maximum=3600.0)
    ring = _optional(observation.get("ring"), int, "observation.ring", 8,
                     minimum=0, maximum=32)
    capture_every = _optional(observation.get("capture_every_frame"), bool,
                              "observation.capture_every_frame", False)
    preview = _optional(observation.get("preview"), bool, "observation.preview", True)
    dump_on_stop = _optional(observation.get("dump_on_stop"), int,
                             "observation.dump_on_stop", 4, minimum=0, maximum=32)
    stall_seconds = _optional(observation.get("stall_seconds"), float,
                              "observation.stall_seconds", 45.0,
                              minimum=5.0, maximum=1800.0)

    comparison = value.get("comparison", {})
    if not isinstance(comparison, dict):
        raise ScenarioError("type", "comparison must be an object")
    _check_keys(comparison, {
        "policy", "max_channel_error", "max_mismatched_fraction",
        "max_mean_abs_error", "regions", "require_aligned",
    }, "comparison")
    policy = comparison.get("policy", "exact")
    if policy not in POLICIES:
        raise ScenarioError("enum", f"comparison.policy must be one of {POLICIES}")
    max_channel = _optional(comparison.get("max_channel_error"), int,
                            "comparison.max_channel_error", 0, minimum=0, maximum=255)
    max_fraction = _optional(comparison.get("max_mismatched_fraction"), float,
                             "comparison.max_mismatched_fraction", 0.0,
                             minimum=0.0, maximum=1.0)
    max_mean = _optional(comparison.get("max_mean_abs_error"), float,
                         "comparison.max_mean_abs_error", 0.0,
                         minimum=0.0, maximum=255.0)
    regions = comparison.get("regions", dict(DEFAULT_REGIONS))
    if not isinstance(regions, dict) or not regions:
        raise ScenarioError("type", "comparison.regions must be a non-empty object")
    normalized_regions = {}
    for name, region in regions.items():
        if not isinstance(region, dict) or not isinstance(region.get("rect"), list) \
                or len(region["rect"]) != 4 \
                or not all(type(item) is int for item in region["rect"]):
            raise ScenarioError("type", f"comparison.regions.{name}.rect must be four integers")
        y0, y1, x0, x1 = region["rect"]
        if not (0 <= y0 < y1 <= height and 0 <= x0 < x1 <= width):
            raise ScenarioError("range", f"comparison.regions.{name} is outside the extent")
        normalized_regions[name] = {
            "rect": [y0, y1, x0, x1],
            "policy": region.get("policy", "tolerant"),
        }
    require_aligned = _optional(comparison.get("require_aligned"), bool,
                                "comparison.require_aligned", True)

    mode = value.get("mode", "streaming")
    if mode not in MODES:
        raise ScenarioError("enum", f"mode must be one of {MODES}")
    wait_profile = value.get("profile", "targeted")
    if wait_profile not in PROFILES:
        raise ScenarioError("enum", f"profile must be one of {PROFILES}")

    experiment = value.get("experiment", "differential")
    if experiment not in EXPERIMENTS:
        raise ScenarioError("enum", f"experiment must be one of {EXPERIMENTS}")
    reference_backend = value.get("reference_backend", "opengl")
    candidate_backend = value.get("candidate_backend", "metal")
    if reference_backend not in ("opengl", "metal") or candidate_backend not in (
            "opengl", "metal"):
        raise ScenarioError("backend", "backends must be opengl or metal")
    if experiment == "differential":
        if reference_backend != "opengl" or candidate_backend != "metal":
            raise ScenarioError("backend", "reference must be opengl and candidate metal")
    elif reference_backend != candidate_backend:
        raise ScenarioError(
            "backend", "consistency experiments require the same backend on both sides"
        )

    normalized = {
        "schema": SCHEMA,
        "name": str(value.get("name") or "unnamed"),
        "executable": executable,
        "executable_sha256": sha.lower(),
        "args": args,
        "experiment": experiment,
        "reference_backend": reference_backend,
        "candidate_backend": candidate_backend,
        "logical_extent": [width, height],
        "start": {
            "kind": kind,
            "save": save,
            "pause": pause,
            "game_speed": speed,
        },
        "game_config": game_config,
        "input_events": events,
        "settle": {
            "min_frames": min_frames,
            "stable_frames": stable_frames,
            "timeout_seconds": settle_timeout,
            "min_draws": settle_min_draws,
            "draw_tolerance": settle_draw_tolerance,
        },
        "observation": {
            "max_frames": max_frames,
            "max_runtime_seconds": max_runtime,
            "ring": ring,
            "capture_every_frame": capture_every,
            "preview": preview,
            "dump_on_stop": dump_on_stop,
            "stall_seconds": stall_seconds,
        },
        "comparison": {
            "policy": policy,
            "max_channel_error": max_channel,
            "max_mismatched_fraction": max_fraction,
            "max_mean_abs_error": max_mean,
            "regions": normalized_regions,
            "require_aligned": require_aligned,
        },
        "mode": mode,
        "profile": wait_profile,
        "authorization": value.get("authorization"),
    }
    normalized["digest"] = scenario_digest(normalized)
    return normalized


def scenario_digest(value: dict) -> str:
    canonical = json.dumps({key: item for key, item in value.items() if key != "digest"},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load(path: Path) -> dict:
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ScenarioError("unreadable", str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise ScenarioError("malformed", str(exc)) from exc
    return normalize(value)


def write_template(path: Path, *, executable_sha256: str, extent: tuple[int, int] = (1728, 1117),
                   max_frames: int = 100) -> dict:
    value = normalize({
        "schema": SCHEMA,
        "name": "title-frontend",
        "executable_sha256": executable_sha256,
        "logical_extent": list(extent),
        "start": {"kind": "frontend", "pause": True},
        "settle": {"min_frames": 5, "stable_frames": 3},
        "observation": {"max_frames": max_frames, "ring": 8, "dump_on_stop": 4},
        "comparison": {"policy": "exact"},
        "mode": "streaming",
        "profile": "targeted",
    })
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return value
