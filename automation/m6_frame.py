#!/usr/bin/env python3
"""Complete-frame capture, translation, replay and differential validation.

The M6 workflow is draw-centric and reusable:

    capture    one authentic original-OpenGL frame (frame boundary, every
               draw's effective GL state/resources, seed and reference pixels)
    translate  GLSL 1.20 compiler inputs -> Metal Shading Language sources
    replay     execute the captured frame through the shared Metal encoder
    compare    full-frame and prefix differential with attribution
    report     coverage and missing-dependency diagnostics

Raw payloads live under ``.local/m6`` (gitignored). Only digests and
provenance belong in tracked evidence.
"""

from __future__ import annotations

_MISSING_CLEANUP = object()


import argparse
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from automation import (  # noqa: E402
    campaign_guard, gfx_observation, replay_host_dependencies)
from automation.gfx_artifacts import sha256_file as _sha256

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

TRACE_SCHEMA = "gfx-frame-trace/v1"
TARGETS = [
    "_GfxDraw",
    "_GfxDrawIndexed",
    "_GfxDrawIndexedInstanced",
    "_GfxClear",
    "_GfxPresent",
    # The main menu skips _GfxPresent while its present flag is clear but the
    # frontend calls CGraphics::PresentScene every rendered frame.
    "PresentScene",
]


class FrameError(RuntimeError):
    pass


def _is_sha256(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


# size and nanosecond mtime, so a replaced artifact is re-hashed; repeated
# graph builds over the same ten-frame campaign avoid re-reading gigabytes.
_BLOB_DIGESTS: dict[tuple[str, int, int, int, int], str] = {}


def _verified_blob_sha256(path: Path) -> str:
    stat = path.stat()
    # inode and ctime change on replacement even when a caller preserves size
    # and mtime (cp -p / os.utime); mtime alone would return a stale digest.
    key = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ino,
           stat.st_ctime_ns)
    cached = _BLOB_DIGESTS.get(key)
    if cached is not None:
        return cached
    digest = _sha256(path)
    if len(_BLOB_DIGESTS) > 8192:
        _BLOB_DIGESTS.clear()
    _BLOB_DIGESTS[key] = digest
    return digest



def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FrameError(f"unreadable JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FrameError(f"not a JSON object: {path}")
    return value


# ---------------------------------------------------------------------------
# Trace loading and validation
# ---------------------------------------------------------------------------



PREFIX_SCHEMA = gfx_observation.OBSERVATIONS_SCHEMA
PREFIX_OBSERVATION_SCHEMA = gfx_observation.OBSERVATION_SCHEMA


def _prefix_record_problems(record, index: int,
                            expected_event: int | None = None) -> list[str]:
    """Validate one observation's self-description against its selection."""
    if not isinstance(record, dict):
        return [f"prefix observation {index} is not an object"]
    problems: list[str] = []
    if not gfx_observation.is_supported_observation_schema(
            record.get("schema")):
        problems.append(f"prefix observation {index} has an unsupported schema")
    expected = index if expected_event is None else expected_event
    if record.get("event") != expected:
        problems.append(
            f"prefix observation {index} names event {record.get('event')!r}, "
            f"expected {expected}")
    timing = record.get("timing")
    if timing not in ("pre_event", "post_event"):
        problems.append(f"prefix observation {index} has an invalid timing")
    if record.get("origin") != "lower_left":
        problems.append(f"prefix observation {index} is not lower-left origin")
    pixel_format = record.get("pixel_format")
    colour_block = record.get("color") if isinstance(record.get("color"), dict) else {}
    if (pixel_format not in ("BGRA8", "RGBA8", "R32F")
            and colour_block.get("status") != "unsupported_format"):
        problems.append(f"prefix observation {index} has an unsupported "
                        "pixel format")
    width = record.get("width")
    height = record.get("height")
    if (not isinstance(width, int) or isinstance(width, bool)
            or not 0 < width <= 8192):
        problems.append(f"prefix observation {index} has an invalid width")
        width = None
    if (not isinstance(height, int) or isinstance(height, bool)
            or not 0 < height <= 8192):
        problems.append(f"prefix observation {index} has an invalid height")
        height = None
    stride = record.get("row_stride")
    if width is not None and stride != width * 4:
        problems.append(f"prefix observation {index} row stride does not "
                        "match its width")
    colour = record.get("color")
    if not isinstance(colour, dict):
        problems.append(f"prefix observation {index} has no colour image")
    elif colour.get("status") == "unsupported_format":
        if record.get("kind") != "clear":
            problems.append(
                f"prefix observation {index} has an unsupported colour "
                "format for a non-clear operation")
        if pixel_format is not None:
            problems.append(
                f"prefix observation {index} declares a pixel format for an "
                "unsupported colour target")
    else:
        name = colour.get("file")
        if (not isinstance(name, str) or not name
                or Path(name).name != name):
            problems.append(f"prefix observation {index} has an invalid "
                            "colour blob name")
        if width is not None and height is not None and colour.get(
                "bytes") != width * height * 4:
            problems.append(f"prefix observation {index} colour byte count "
                            "does not match its dimensions")
        if colour.get("pixel_format") not in (None, pixel_format):
            problems.append(f"prefix observation {index} colour pixel format "
                            "disagrees with the observation")
        scope = gfx_observation.format_scope(record)
        for problem in scope["problems"]:
            problems.append(f"prefix observation {index}: {problem}")
        if not _is_sha256(colour.get("sha256")):
            problems.append(f"prefix observation {index} has an invalid "
                            "colour digest")
    for member in ("depth", "stencil"):
        value = record.get(member)
        if not isinstance(value, dict):
            problems.append(f"prefix observation {index} has no {member} "
                            "status")
            continue
        status = value.get("status")
        if status not in ("captured", "absent", "not_used", "not_present"):
            problems.append(f"prefix observation {index} has an invalid "
                            f"{member} status")
        elif status == "captured":
            name = value.get("file")
            if (not isinstance(name, str) or not name
                    or Path(name).name != name):
                problems.append(f"prefix observation {index} has an invalid "
                                f"{member} blob name")
            if not _is_sha256(value.get("sha256")):
                problems.append(f"prefix observation {index} has an invalid "
                                f"{member} digest")
            if width is not None and height is not None:
                expected = (width * height * 4 if member == "depth"
                            else width * height)
                if value.get("bytes") != expected:
                    problems.append(
                        f"prefix observation {index} {member} byte count does "
                        "not match its dimensions")
    return problems


def iter_blob_members(value):
    """Yield dict members that name a frame blob file."""
    if isinstance(value, dict):
        if isinstance(value.get("file"), str) and value.get("file"):
            yield value
        for item in value.values():
            yield from iter_blob_members(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_blob_members(item)


def _producer_writes_color(producer: dict) -> bool:
    """Whether a graph producer can make its resource current for sampling.

    Mirrors ``g_frame_written`` in ``renderer/metal/eu4_gfx_frame_replay.mm``:
    draws and copy/resolve destinations write colour; a clear counts only when
    its flags include a colour write.
    """
    kind = producer.get("kind")
    if kind in ("draw", "resolve", "copy"):
        return True
    if kind == "clear":
        return "color" in (producer.get("writes") or [])
    return False


def _texture_is_generated(events, index: int, texture: dict,
                          resources: dict) -> bool:
    key = f"tex:{texture.get('name')}"
    return any(
        isinstance(producer.get("event"), int)
        and producer["event"] < index
        and _producer_writes_color(producer)
        for producer in (resources.get(key) or {}).get("producers") or [])


def event_input_blob_names(events, graph: dict | None = None) -> set[str]:
    """Explicit inventory of blobs the replay reads as renderer inputs.

    Event records also carry expected-output and provenance fields; only the
    paths the replay actually consumes are copied into a candidate input frame.
    A captured texture payload is skipped when its actual consumer samples an
    earlier in-frame Metal colour producer.  The same file is still included
    when another consumed input role (attribute, index, seed, uniform or
    external texture) uses it.  ``graph`` supplies producer identities; without
    it every captured texture remains an input.

    Full-frame replay reads no ``source_image``, no ``pre_image`` (that is
    isolated-event-only) and no original GLSL ``program.shaders``; translated
    shader artifacts are supplied separately.  Those entries are therefore not
    candidate inputs.
    """
    resources = (graph or {}).get("resources") or {}
    names: set[str] = set()

    def add(value) -> None:
        if isinstance(value, dict) and isinstance(value.get("file"), str)                 and value["file"]:
            names.add(Path(value["file"]).name)

    for index, event in enumerate(events):
        add(event.get("seed"))
        for attribute in event.get("attributes") or []:
            if isinstance(attribute, dict) and isinstance(
                    attribute.get("data"), str) and attribute["data"]:
                names.add(Path(attribute["data"]).name)
        add(event.get("indices"))
        program = event.get("program") or {}
        for uniform in program.get("uniforms") or []:
            add(uniform)
        for texture in event.get("textures") or []:
            if not isinstance(texture, dict) or _texture_is_generated(
                    events, index, texture, resources):
                continue
            for level in texture.get("levels") or []:
                add(level)
                add(level.get("compressed") if isinstance(level, dict) else None)
                for face in (level.get("faces") if isinstance(level, dict)
                             else None) or []:
                    add(face)
    return names


def event_blob_names(events) -> set[str]:
    """All blobs referenced by a trusted record (inputs or expected outputs)."""
    names: set[str] = set()
    for event in events:
        for member in iter_blob_members(event):
            name = member.get("file")
            if isinstance(name, str) and name:
                names.add(Path(name).name)
        for attribute in event.get("attributes") or []:
            if isinstance(attribute, dict) and isinstance(
                    attribute.get("data"), str) and attribute["data"]:
                names.add(Path(attribute["data"]).name)
    return names


def _blob_entry(member, *,
                role: str, identity: dict) -> dict | None:
    if not isinstance(member, dict):
        return None
    name = member.get("file")
    if not isinstance(name, str) or not name:
        return None
    return {
        "file": Path(name).name,
        "sha256": member.get("sha256") if isinstance(
            member.get("sha256"), str) else None,
        "role": role,
        "identity": identity,
    }


def expected_output_blobs(trace: dict) -> list[dict]:
    """Inventory expected observation/checkpoint/reference payloads.

    Each entry records its byte digest and the exact role it plays, so the
    overlap classifier can distinguish a compared post-event colour payload
    from an initial state, a depth/stencil diagnostic, a default checkpoint or
    the final-frame reference.
    """
    entries: list[dict] = []

    def add(member, role: str, identity: dict) -> None:
        entry = _blob_entry(member, role=role, identity=identity)
        if entry is not None:
            entries.append(entry)

    prefix = trace.get("_prefix") or {}
    for record in [prefix.get("initial"), *(prefix.get("post_events") or [])]:
        if not isinstance(record, dict):
            continue
        identity = {
            "kind": "observation",
            "event": record.get("event"),
            "timing": record.get("timing"),
            "resource": record.get("resource"),
        }
        add(record.get("color"), "color", identity)
        add(record.get("depth"), "depth", identity)
        add(record.get("stencil"), "stencil", identity)
    for event in trace.get("_events") or []:
        checkpoint = event.get("default_checkpoint")
        if not isinstance(checkpoint, dict):
            continue
        identity = {
            "kind": "default_checkpoint",
            "event": checkpoint.get("event"),
            "timing": checkpoint.get("timing"),
            "resource": checkpoint.get("resource"),
        }
        add(checkpoint.get("depth"), "depth", identity)
        add(checkpoint.get("stencil"), "stencil", identity)
        colour_file = checkpoint.get("file") or checkpoint.get("reference_file")
        colour_sha = checkpoint.get("sha256") or checkpoint.get(
            "reference_sha256")
        if isinstance(colour_file, str) and colour_file:
            add({"file": colour_file, "sha256": colour_sha}, "color",
                identity)
    frame = trace.get("frame") or {}
    reference = frame.get("reference")
    if isinstance(reference, str) and reference:
        entries.append({
            "file": Path(reference).name,
            "sha256": frame.get("reference_sha256"),
            "role": "final_reference",
            "identity": {"kind": "final_reference"},
        })
    return entries


def _seed_replaced_before_first_draw(events, index: int,
                                     graph: dict | None) -> bool:
    """Whether an earlier full colour clear makes the first draw's seed unused.

    The graph records the first draw's seed as a frame-boundary observation.
    When the same resource was fully colour-cleared earlier in this frame, the
    clear overwrites it before any sampling, so the captured seed is not
    previous-frame state the replay needs.
    """
    seed = events[index].get("seed")
    if not isinstance(seed, dict) or not seed.get("file"):
        return False
    resources = (graph or {}).get("resources") or {}
    for resource in resources.values():
        boundary = resource.get("boundary") or {}
        if (boundary.get("event") == index
                and boundary.get("file") == seed.get("file")):
            return any(
                isinstance(producer.get("event"), int)
                and producer["event"] < index
                and producer.get("cleared")
                and (producer.get("coverage") or {}).get("color_full")
                for producer in resource.get("producers") or [])
    return False


def _sanitized_events(trace: dict, graph: dict | None) -> list[dict]:
    """Event records the candidate replay consumes.

    The native host uploads only the first draw's seed.  Seeds on later draws
    are never consumed, and a first-draw seed already replaced by an earlier
    full colour clear is redundant; dropping both avoids copying payloads the
    replay cannot use.
    """
    events = [{key: value for key, value in event.items()
               if key != "default_checkpoint"}
              for event in trace.get("_events") or []]
    first_draw = next((index for index, event in enumerate(events)
                       if event.get("kind") == "draw"), None)
    for index, event in enumerate(events):
        if "seed" not in event:
            continue
        if index != first_draw or _seed_replaced_before_first_draw(
                events, index, graph):
            event.pop("seed", None)
    return events


def _input_blob_entries(events, frame_dir: Path | None,
                        graph: dict | None = None) -> list[dict]:
    entries = []
    for name in sorted(event_input_blob_names(events, graph)):
        entry = {"file": name, "sha256": None}
        if frame_dir is not None:
            path = Path(frame_dir) / "blobs" / name
            if path.is_file():
                entry["sha256"] = _sha256(path)
        entries.append(entry)
    return entries


def replay_input_overlap(trace: dict,
                         frame_dir: Path | str | None = None, *,
                         graph: dict | None = None) -> list[dict]:
    """Expected outputs whose bytes remain accessible as renderer inputs.

    Matching is by content digest, not filename, so a copied payload under a
    different name is still reported.  Inputs the replay regenerates from an
    earlier in-frame producer are not accessible inputs and are not reported;
    pass the same ``graph`` used for materialization so both agree.
    """
    events = _sanitized_events(trace, graph)
    expected = expected_output_blobs(trace)
    overlaps = []
    for item in _input_blob_entries(events, frame_dir, graph):
        digest = item.get("sha256")
        matches = [entry for entry in expected
                   if digest is not None and entry.get("sha256") == digest]
        if not matches:
            continue
        overlaps.append({
            "file": matches[0]["file"],
            "input_file": item["file"],
            "sha256": digest,
            "expected_by": [{
                "kind": entry["identity"]["kind"],
                "role": entry["role"],
                "event": entry["identity"].get("event"),
                "timing": entry["identity"].get("timing"),
                "resource": entry["identity"].get("resource"),
            } for entry in matches],
        })
    return overlaps


def materialize_replay_input(frame_dir: Path | str, trace: dict,
                             destination: Path | str, *,
                             graph: dict | None = None) -> dict:
    """Create a replay input frame without expected output pixels.

    The sanitized event representation is built first, then its explicit input
    inventory is derived from that representation.  Expected outputs are never
    copied, even when the same bytes are available under another input name;
    overlap is reported by content digest.  Texture payloads that an earlier
    in-frame event regenerates are omitted: the renderer samples its own
    render target, so accepting the captured bytes would both be redundant and
    make the checkpoint non-independent.  Pass ``graph`` from
    :func:`gfx_frame_graph.build` to derive that provenance.
    """
    frame_dir = Path(frame_dir).resolve()
    destination = Path(destination).resolve()
    events = trace.get("_events") or []
    if not events:
        raise FrameError("replay input frame has no events")
    sanitized_events = _sanitized_events(trace, graph)
    inputs = event_input_blob_names(sanitized_events, graph)
    overlap = replay_input_overlap(trace, frame_dir, graph=graph)
    destination.mkdir(parents=True, exist_ok=True)
    blobs = destination / "blobs"
    blobs.mkdir(exist_ok=True)
    for name in sorted(inputs):
        source = frame_dir / "blobs" / name
        if not source.is_file():
            raise FrameError(f"replay input blob is absent: {name}")
        shutil.copyfile(source, blobs / name)
    manifest = {key: value for key, value in trace.items()
                if not key.startswith("_")}
    for key in ("prefix", "default_checkpoint_events",
                "default_checkpoint_count", "default_checkpoint_event"):
        manifest.pop(key, None)
    frame_info = manifest.get("frame")
    if isinstance(frame_info, dict):
        # The candidate does not need expected-reference paths or digests.
        manifest["frame"] = {
            key: value for key, value in frame_info.items()
            if key not in ("reference", "reference_file", "reference_bytes",
                           "reference_sha256")
        }
    (destination / "frame-events.jsonl").write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n"
                for event in sanitized_events))
    (destination / "frame-trace.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    expected = expected_output_blobs(trace)
    return {
        "frame_dir": str(destination),
        "blob_count": len(inputs),
        "event_count": len(sanitized_events),
        "excluded": ["frame-reference", "default-checkpoint", "observation"],
        "expected_only_blobs": len([entry for entry in expected
                                    if entry["file"] not in inputs]),
        "shared_input_outputs": overlap,
    }


def load_prefix_observations(manifest: dict, frame_dir: Path) -> dict | None:
    """Load and verify the observation file declared by the manifest.

    Two selections share this representation: a contiguous prefix from event
    0 (with a proven initial state) and a bounded set of independently selected
    post-event observations (no initial-state anchor).  The file is
    authoritative; a legacy manifest may embed the same records only when they
    agree exactly.
    """
    block = manifest.get("prefix")
    if block is None:
        return None
    frame_dir = Path(frame_dir)
    if not isinstance(block, dict):
        raise FrameError("frame prefix block is not an object")
    if not gfx_observation.is_supported_observations_schema(
            block.get("schema")):
        raise FrameError("unexpected frame prefix schema")
    mode = str(block.get("mode") or "prefix")
    if mode not in ("prefix", "selected"):
        raise FrameError("frame observation mode is unsupported")
    if block.get("status") != "complete":
        raise FrameError(
            "frame observation coverage is incomplete: "
            + str(block.get("status") or "unknown"))
    count = block.get("count")
    if (not isinstance(count, int) or isinstance(count, bool) or count < 0
            or count > 4096):
        raise FrameError("frame observation count is invalid")
    name = block.get("observation_file")
    if (not isinstance(name, str) or not name or Path(name).name != name):
        raise FrameError("frame observation file name is invalid")
    path = frame_dir / name
    if not path.is_file():
        raise FrameError("frame observation file is absent")
    declared_bytes = block.get("observation_bytes")
    if path.stat().st_size != declared_bytes:
        raise FrameError("frame observation byte count does not match")
    digest = _sha256(path)
    if block.get("observation_sha256") != digest:
        raise FrameError("frame observation digest does not match")
    try:
        records = [json.loads(line) for line in path.read_text().splitlines()
                   if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FrameError(f"unreadable frame observations: {exc}") from exc
    embedded = block.get("observations")
    if embedded is not None and (
            not isinstance(embedded, list) or embedded != records):
        raise FrameError("frame observations disagree with the manifest")
    post = [record for record in records
            if isinstance(record, dict) and record.get("timing") == "post_event"]
    initial = [record for record in records
               if isinstance(record, dict)
               and record.get("timing") == "pre_event"]
    if block.get("post_event_count") != count or len(post) != count:
        raise FrameError("frame observation coverage is incomplete")
    if mode == "prefix":
        end = block.get("end")
        if (not isinstance(end, int) or isinstance(end, bool)
                or not 0 <= end < 4096):
            raise FrameError("frame prefix end event is invalid")
        if count != end + 1:
            raise FrameError(
                "frame prefix post-event count does not match its end")
        if block.get("initial") is not True or len(initial) != 1:
            raise FrameError("frame prefix has no initial state observation")
        if initial[0].get("event") != 0:
            raise FrameError("frame prefix initial observation is not event 0")
        selection = list(range(end + 1))
    else:
        if block.get("end") is not None:
            raise FrameError("selected observations must not declare a prefix end")
        if block.get("initial") not in (False, None) or initial:
            raise FrameError("selected observations must not claim an initial state")
        selection = block.get("events")
        if (not isinstance(selection, list) or not selection
                or any(not isinstance(value, int) or isinstance(value, bool)
                       or not 0 <= value < 4096 for value in selection)
                or selection != sorted(set(selection))):
            raise FrameError("selected observation events are invalid")
        if count != len(selection):
            raise FrameError(
                "selected observation count does not match its event set")
        end = None
    for index, record in enumerate(post):
        expected_event = selection[index]
        if not gfx_observation.is_supported_observation_schema(
                record.get("schema") if isinstance(record, dict) else None):
            raise FrameError(
                f"frame observation {index} has an unsupported schema")
        problems = _prefix_record_problems(record, index, expected_event)
        if problems:
            raise FrameError(problems[0])
    if initial:
        problems = _prefix_record_problems(initial[0], 0, 0)
        if problems:
            raise FrameError(problems[0])
    return {
        "schema": PREFIX_SCHEMA,
        "mode": mode,
        "end": end,
        "events": selection,
        "initial": initial[0] if initial else None,
        "post_events": post,
        "records": records,
        "observation_file": name,
        "observation_sha256": digest,
        "observation_bytes": declared_bytes,
        "budget": block.get("budget"),
    }


def load_trace(frame_dir: Path, *, verify_blobs: bool = True) -> dict:
    """Loads and validates one frame trace. Fails closed on any gap."""
    frame_dir = Path(frame_dir)
    manifest = _read_json(frame_dir / "frame-trace.json")
    if manifest.get("schema") != TRACE_SCHEMA:
        raise FrameError("unexpected frame trace schema")
    if manifest.get("complete") is not True:
        raise FrameError(
            f"frame trace is incomplete: {manifest.get('reason') or 'unknown'}"
        )
    events_path = frame_dir / "frame-events.jsonl"
    events: list[dict] = []
    try:
        for line in events_path.read_text().splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise FrameError("frame event is not an object")
            events.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FrameError(f"unreadable frame events: {exc}") from exc
    draws = [event for event in events if event.get("kind") == "draw"]
    if not draws:
        raise FrameError("frame trace contains no draws")
    if manifest.get("events") != len(events):
        raise FrameError(
            f"manifest declares {manifest.get('events')} events, file has {len(events)}"
        )
    frame = manifest.get("frame") or {}
    reference = frame_dir / str(frame.get("reference") or "")
    if not frame.get("reference") or not reference.is_file():
        raise FrameError("frame reference pixels are absent")
    if reference.stat().st_size != frame.get("reference_bytes"):
        raise FrameError("frame reference size does not match its declaration")
    if _sha256(reference) != frame.get("reference_sha256"):
        raise FrameError("frame reference sha256 does not match its declaration")
    prefix = load_prefix_observations(manifest, frame_dir)
    if prefix is not None:
        if prefix["mode"] == "prefix":
            if prefix["end"] >= len(events):
                raise FrameError("frame prefix extends beyond the event stream")
        elif prefix["events"] and prefix["events"][-1] >= len(events):
            raise FrameError(
                "selected observation extends beyond the event stream")
        for index, record in enumerate(prefix["post_events"]):
            event_index = int(record.get("event"))
            if event_index >= len(events):
                raise FrameError(
                    "observation event is beyond the event stream")
            event = events[event_index]
            if record.get("kind") != event.get("kind"):
                raise FrameError(
                    f"frame observation {index} kind disagrees with "
                    "the event stream")
    if verify_blobs:
        blobs: dict[str, str] = {}
        for event in events:
            members = list(_iter_blob_members(event))
            # Vertex attribute payloads use "data" rather than "file".
            for attribute in event.get("attributes") or []:
                if isinstance(attribute, dict) and attribute.get("data"):
                    members.append(
                        {
                            "file": attribute["data"],
                            "sha256": attribute.get("sha256"),
                        }
                    )
            for member in members:
                name = member.get("file")
                if not name:
                    continue
                if name in blobs:
                    continue
                path = frame_dir / "blobs" / str(name)
                if not path.is_file():
                    raise FrameError(f"missing blob: {name}")
                digest = _verified_blob_sha256(path)
                if member.get("sha256") and member["sha256"] != digest:
                    raise FrameError(f"blob digest mismatch: {name}")
                blobs[name] = digest
        if prefix is not None:
            for record in prefix["records"]:
                for member in _iter_blob_members(record):
                    blob_name = member.get("file")
                    if not blob_name or blob_name in blobs:
                        continue
                    path = frame_dir / "blobs" / str(blob_name)
                    if not path.is_file():
                        raise FrameError(f"missing prefix blob: {blob_name}")
                    blob_digest = _verified_blob_sha256(path)
                    if (member.get("sha256")
                            and member["sha256"] != blob_digest):
                        raise FrameError(
                            f"prefix blob digest mismatch: {blob_name}")
                    blobs[blob_name] = blob_digest
        manifest["_blob_count_verified"] = len(blobs)
    manifest["_prefix"] = prefix
    manifest["_events"] = events
    manifest["_draws"] = draws
    manifest["_frame_dir"] = str(frame_dir)
    return manifest


SEAL_SCHEMA = "gfx-frame-seal/v1"
SEAL_ABI_VERSION = 2


def load_seal(
    frame_dir: Path,
    *,
    trace: dict | None = None,
    verify_counts: bool = True,
    verify_blobs: bool = True,
    trusted_evidence: dict | None = None,
) -> dict:
    """Loads and validates the authoritative same-process frame seal.

    The seal is the handoff contract: a consumer may act only on a document
    whose ``sealed`` flag is true, whose trace manifest, GL reference and
    immutable event stream still match their declared digests, and whose
    ordered operation accounting matches the event stream on disk.  Referenced
    blobs are checked by the canonical ``load_trace`` verifier; pass an already
    verified ``trace`` to avoid hashing them twice.
    """
    frame_dir = Path(frame_dir)
    path = frame_dir / "frame-seal.json"
    seal = _read_json(path)
    if seal.get("schema") != SEAL_SCHEMA:
        raise FrameError("unexpected frame seal schema")
    if seal.get("sealed") is not True:
        raise FrameError(
            "frame is not sealed: "
            + str(seal.get("failure_reason") or "unknown")
        )
    if seal.get("complete") is not True:
        raise FrameError("sealed descriptor is not complete")
    try:
        struct_size = int(seal.get("struct_size"))
        abi_version = int(seal.get("abi_version"))
    except (TypeError, ValueError) as exc:
        raise FrameError("frame seal has no ABI identity") from exc
    if abi_version != SEAL_ABI_VERSION:
        raise FrameError(f"unsupported frame seal ABI {abi_version}")
    if struct_size < 2256 or struct_size > 65536:
        raise FrameError("frame seal descriptor size is invalid")
    if seal.get("trace_complete") is not True:
        raise FrameError("sealed trace manifest is not complete")
    if seal.get("trace_integrity") is not True:
        raise FrameError("sealed trace integrity is not validated")
    if seal.get("readback_ok") is not True:
        raise FrameError("sealed frame has no reference readback")
    if seal.get("event_stream_verified") is not True:
        raise FrameError("sealed event stream is not verified")
    try:
        capture_id = int(seal.get("capture_frame_id"))
        pid = int(seal.get("pid"))
    except (TypeError, ValueError) as exc:
        raise FrameError("frame seal has no numeric identity") from exc
    if capture_id <= 0 or pid <= 0 or not seal.get("session_id"):
        raise FrameError("frame seal identity is invalid")
    declared_dir = Path(str(seal.get("capture_dir") or ""))
    portable_capture = False
    case_dir = (frame_dir.parent.parent
                if frame_dir.parent.name == "frames"
                else frame_dir.parent)
    case_path = case_dir / "case.json"
    try:
        case = json.loads(case_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        case = None
    if isinstance(case, dict):
        schema = case.get("schema")
        if (schema == "gfx-strategy-b-case/v1"
                and case.get("capture") == "capture"
                and frame_dir.name == "capture"):
            portable_capture = True
        elif (schema == "gfx-strategy-b-case/v2"
              and frame_dir.parent.name == "frames"
              and not declared_dir.is_absolute()):
            try:
                relative = frame_dir.resolve().relative_to(
                    case_dir.resolve())
            except ValueError:
                relative = None
            if (relative is not None
                    and declared_dir.as_posix() == relative.as_posix()):
                portable_capture = True
    if not portable_capture and (
            not declared_dir.is_absolute()
            or declared_dir.resolve() != frame_dir.resolve()):
        if trusted_evidence is None:
            raise FrameError("frame seal capture directory does not match")
        # A verified copied snapshot keeps the original seal unchanged but
        # lives at a different absolute path.  The trusted-evidence record
        # binds the original capture directory and the copied byte manifest.
        original = str(trusted_evidence.get("original_capture_dir") or "")
        if (not original
                or Path(original).resolve() != declared_dir.resolve()):
            raise FrameError(
                "trusted evidence does not name the sealed capture directory")
        relative = trusted_evidence.get("frame_relative")
        if relative is not None:
            expected = Path(str(trusted_evidence.get("_root") or "")).resolve() / str(relative)
            if frame_dir.resolve() != expected:
                raise FrameError(
                    "trusted evidence frame is not the verified snapshot")

    def member(name: object, label: str) -> Path:
        text = str(name or "")
        path = Path(text)
        if not text or path.is_absolute() or path.name != text or "/" in text:
            raise FrameError(f"sealed {label} path is invalid")
        return frame_dir / path

    manifest_path = member(seal.get("manifest_file"), "manifest")
    if not manifest_path.is_file():
        raise FrameError("sealed trace manifest is absent")
    if _sha256(manifest_path) != seal.get("manifest_sha256"):
        raise FrameError("sealed trace manifest digest does not match")

    reference_path = member(seal.get("reference_file"), "reference")
    if not reference_path.is_file():
        raise FrameError("sealed reference pixels are absent")
    if reference_path.stat().st_size != seal.get("reference_bytes"):
        raise FrameError("sealed reference size does not match")
    if _sha256(reference_path) != seal.get("reference_sha256"):
        raise FrameError("sealed reference digest does not match")

    events_path = member(seal.get("event_stream_file"), "event stream")
    if not events_path.is_file():
        raise FrameError("sealed event stream is absent")
    if events_path.stat().st_size != seal.get("event_stream_bytes"):
        raise FrameError("sealed event stream size does not match")
    if _sha256(events_path) != seal.get("event_stream_sha256"):
        raise FrameError("sealed event stream digest does not match")

    if trace is None:
        trace = load_trace(frame_dir, verify_blobs=verify_blobs)
    else:
        if Path(str(trace.get("_frame_dir") or "")).resolve() != frame_dir.resolve():
            raise FrameError("supplied trace belongs to another frame directory")
        if verify_blobs and "_blob_count_verified" not in trace:
            raise FrameError("supplied trace was not verified for referenced blobs")
    events = trace.get("_events") or []
    if verify_counts:
        kinds = [event.get("kind") for event in events]
        counts = {
            "operation_count": len(events),
            "draw_count": kinds.count("draw"),
            "clear_count": kinds.count("clear"),
            "copy_count": kinds.count("resolve"),
        }
        for field, actual in counts.items():
            try:
                declared = int(seal.get(field, -1))
            except (TypeError, ValueError) as exc:
                raise FrameError(f"frame seal {field} is not an integer") from exc
            if declared != actual:
                raise FrameError(
                    f"frame seal {field} {seal.get(field)!r} != {actual}"
                )
        manifest = _read_json(manifest_path)
        if manifest.get("events") != len(events):
            raise FrameError("sealed manifest event count does not match")
        if manifest.get("complete") is not True:
            raise FrameError("sealed manifest is incomplete")
    seal["verification"] = {
        "event_stream": True,
        "manifest": True,
        "reference": True,
        "blobs": (
            trace.get("_blob_count_verified") if verify_blobs else None
        ),
        "counts": bool(verify_counts),
    }
    seal["_verified"] = True
    seal["_seal_path"] = str(path)
    seal["_frame_dir"] = str(frame_dir)
    return seal


def load_notification(output: Path) -> dict:
    """Loads the one completion notification emitted by the injected consumer."""
    output = Path(output)
    notification = _read_json(output / "frame-seal-notification.json")
    if notification.get("schema") != "gfx-frame-seal-notification/v1":
        raise FrameError("unexpected seal notification schema")
    if notification.get("notification_index") != 1:
        raise FrameError("more than one completion notification was emitted")
    if notification.get("verified_manifest") is not True:
        raise FrameError("consumer could not verify the sealed manifest")
    if notification.get("verified_reference") is not True:
        raise FrameError("consumer could not verify the sealed reference")
    return notification


def _iter_blob_members(value):
    """Yields every dict that declares a blob file, recursively."""
    if isinstance(value, dict):
        if isinstance(value.get("file"), str) and value.get("file"):
            yield value
        for item in value.values():
            yield from _iter_blob_members(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_blob_members(item)


def coverage_report(trace: dict) -> dict:
    """Actionable coverage and missing-dependency diagnostics."""
    events = trace.get("_events") or []
    draws = trace.get("_draws") or []
    problems: list[dict] = []
    shaders: set[tuple[str, str]] = set()
    textures: dict[str, dict] = {}
    stages_seen: set[str] = set()
    modes: dict[str, int] = {}
    entries: dict[str, int] = {}
    clear_count = 0
    resolve_count = 0
    copy_count = 0
    indexed = 0
    attributes = 0
    uniforms = 0
    for index, event in enumerate(events):
        kind = event.get("kind")
        if kind == "clear":
            clear_count += 1
            continue
        if kind in ("resolve", "copy"):
            if kind == "resolve":
                resolve_count += 1
            else:
                copy_count += 1
            from automation.gfx_frame_graph import copy_contract_problems

            for detail in copy_contract_problems(event, index):
                problems.append({
                    "event": index,
                    "issue": "invalid_transfer",
                    "kind": kind,
                    "detail": detail,
                })
            continue
        if kind != "draw":
            problems.append({"event": index, "issue": "unknown_event_kind", "kind": kind})
            continue
        draw = event.get("draw") or {}
        entry = str(draw.get("entry") or "unknown")
        entries[entry] = entries.get(entry, 0) + 1
        mode = draw.get("mode")
        modes[str(mode)] = modes.get(str(mode), 0) + 1
        if draw.get("indexed"):
            indexed += 1
        program = event.get("program") or {}
        draw_shaders = program.get("shaders") or []
        names = {str(item.get("stage")) for item in draw_shaders if item.get("file")}
        stages_seen |= names
        if "vertex" not in names or "pixel" not in names:
            problems.append(
                {
                    "event": index,
                    "issue": "missing_shader_stage",
                    "stages": sorted(names),
                    "program": program.get("name"),
                }
            )
        for item in draw_shaders:
            if item.get("file") and item.get("sha256"):
                shaders.add((str(item.get("stage")), str(item["sha256"])))
            if item.get("compile") is False:
                problems.append(
                    {
                        "event": index,
                        "issue": "shader_not_compiled",
                        "sha256": item.get("sha256"),
                    }
                )
        diagnostics = event.get("program_diagnostics") or {}
        if int(draw.get("count") or 0) > 0 and not (event.get("attributes") or []):
            problems.append(
                {
                    "event": index,
                    "issue": "draw_without_vertex_attributes",
                    "count": draw.get("count"),
                    "active_attributes": diagnostics.get("active_attributes"),
                    "enabled_bits": diagnostics.get("enabled_bits"),
                    "link": diagnostics.get("link"),
                }
            )
        for attribute in event.get("attributes") or []:
            attributes += 1
            if not attribute.get("data") and attribute.get("range_bytes"):
                problems.append(
                    {
                        "event": index,
                        "issue": "missing_vertex_bytes",
                        "location": attribute.get("location"),
                    }
                )
        for uniform in program.get("uniforms") or []:
            uniforms += 1
            if uniform.get("type") == "sampler":
                continue
            if not uniform.get("file"):
                problems.append(
                    {
                        "event": index,
                        "issue": "missing_uniform_bytes",
                        "name": uniform.get("name"),
                    }
                )
        for texture in event.get("textures") or []:
            key = str(texture.get("name"))
            textures[key] = {
                "name": texture.get("name"),
                "width": texture.get("width"),
                "height": texture.get("height"),
                "internal_format": texture.get("internal_format"),
                "levels": len(texture.get("levels") or []),
            }
            if texture.get("capture_error"):
                problems.append(
                    {
                        "event": index,
                        "issue": "texture_capture_error",
                        "name": texture.get("name"),
                        "internal_format": texture.get("internal_format"),
                    }
                )
            if not texture.get("levels"):
                problems.append(
                    {
                        "event": index,
                        "issue": "texture_without_levels",
                        "name": texture.get("name"),
                    }
                )
        target = event.get("target") or {}
        if not target.get("width") or not target.get("height"):
            problems.append({"event": index, "issue": "missing_target_extent"})
        if event.get("seed_error"):
            problems.append({"event": index, "issue": "seed_capture_failed"})
    seed = None
    for draw in draws:
        if draw.get("seed"):
            seed = draw.get("seed")
            break
    if not seed:
        problems.append({"event": 0, "issue": "missing_frame_seed"})
    return {
        "schema": "gfx-frame-coverage/v1",
        "event_count": len(events),
        "draw_count": len(draws),
        "clear_count": clear_count,
        "resolve_count": resolve_count,
        "copy_count": copy_count,
        "transfer_count": resolve_count + copy_count,
        "indexed_draw_count": indexed,
        "entries": entries,
        "primitive_modes": modes,
        "stages": sorted(stages_seen),
        "shader_pairs": len(shaders),
        "shaders": [
            {"stage": stage, "sha256": digest}
            for stage, digest in sorted(shaders)
        ],
        "texture_count": len(textures),
        "textures": sorted(textures.values(), key=lambda item: str(item.get("name"))),
        "attribute_count": attributes,
        "uniform_count": uniforms,
        "seed": {
            "width": (seed or {}).get("width"),
            "height": (seed or {}).get("height"),
            "sha256": (seed or {}).get("sha256"),
        },
        "frame": {
            "width": (trace.get("frame") or {}).get("width"),
            "height": (trace.get("frame") or {}).get("height"),
            "reference_sha256": (trace.get("frame") or {}).get("reference_sha256"),
        },
        "missing": problems,
        "ok": not problems,
    }


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def checkpoint_selection_error(event, max_draws) -> str | None:
    """Reject an invalid or unobservable default-framebuffer checkpoint.

    The selected event must be a non-negative index that the captured event
    stream can contain; the native tracer also fails closed when the event
    never reaches a draw/clear/copy pre-operation boundary.
    """
    if event is None:
        return None
    if event < 0:
        return "default checkpoint event must be non-negative"
    if max_draws <= 0 or event >= max_draws:
        return "default checkpoint event must be below --max-draws"
    return None


def checkpoint_events_selection_error(events, max_draws,
                                      max_count: int = 4) -> str | None:
    """Reject an invalid bounded set of default-framebuffer checkpoints.

    The set must be non-empty, strictly increasing (so duplicates and
    reordering are refused rather than normalised), in range, and within the
    externally enforced count budget.  The native tracer applies the same
    checks again, so a caller cannot bypass them.
    """
    if events is None:
        return None
    if not events:
        return "default checkpoint events must not be empty"
    if max_count <= 0:
        return "maximum checkpoint count must be positive"
    if len(events) > max_count:
        return (f"{len(events)} default checkpoints exceed the maximum of "
                f"{max_count}")
    previous = None
    for event in events:
        if not isinstance(event, int) or isinstance(event, bool):
            return "default checkpoint events must be integers"
        if event < 0:
            return "default checkpoint events must be non-negative"
        if max_draws <= 0 or event >= max_draws:
            return "default checkpoint events must be below --max-draws"
        if previous is not None and event <= previous:
            return ("default checkpoint events must be strictly increasing "
                    "and distinct")
        previous = event
    return None


def observation_selection_error(value, max_draws: int) -> str | None:
    """Validate a comma-separated post-event selection."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return "--observe-events must be a comma-separated list"
    parts = value.split(",")
    if not parts or any(not part for part in parts):
        return "--observe-events contains an empty element"
    selected: list[int] = []
    for part in parts:
        try:
            event = int(part, 10)
        except ValueError:
            return "--observe-events contains a non-integer"
        if event < 0 or event >= 4096:
            return "--observe-events is outside the native hard bound"
        if max_draws and event >= max_draws:
            return "--observe-events must be below --max-draws"
        if selected and event <= selected[-1]:
            return "--observe-events must be strictly increasing"
        selected.append(event)
    return None


def checkpoint_options_error(args) -> str | None:
    """Validate the checkpoint capture options before a live launch.

    Returns a human-readable refusal reason, or ``None`` when the selection is
    acceptable.  The native tracer repeats every check, so this is a usability
    guard rather than the only enforcement point.
    """
    checkpoint_event = getattr(args, "default_checkpoint_event", None)
    checkpoint_events = getattr(args, "default_checkpoint_events", None)
    problem = checkpoint_selection_error(checkpoint_event, args.max_draws)
    if problem is not None:
        return problem
    if checkpoint_events is not None:
        if checkpoint_event is not None:
            return ("--default-checkpoint-event and "
                    "--default-checkpoint-events are mutually exclusive")
        problem = checkpoint_events_selection_error(
            checkpoint_events, args.max_draws,
            int(getattr(args, "max_checkpoints", 4)))
        if problem is not None:
            return problem
    checkpoint_budget = int(getattr(args, "checkpoint_max_bytes",
                                    128 * 1024 * 1024))
    if (checkpoint_event is not None or checkpoint_events is not None) and \
            checkpoint_budget <= 0:
        return "--checkpoint-max-bytes must be positive"
    prefix_end = getattr(args, "prefix_end", None)
    observe_events = getattr(args, "observe_events", None)
    if prefix_end is not None and observe_events:
        return "--prefix-end and --observe-events are mutually exclusive"
    selection_problem = observation_selection_error(
        observe_events, int(getattr(args, "max_draws", 0) or 0))
    if selection_problem is not None:
        return selection_problem
    if observe_events and (
            int(getattr(args, "prefix_max_bytes", 0) or 0) <= 0 or
            int(getattr(args, "prefix_max_ns", 0) or 0) <= 0):
        return "--prefix-max-bytes and --prefix-max-ns must be positive"
    if prefix_end is not None:
        if prefix_end < 0:
            return "--prefix-end must be non-negative"
        if prefix_end >= 4096:
            return "--prefix-end must be below the native hard bound 4096"
        if prefix_end >= int(getattr(args, "max_draws", 0) or 0):
            return "--prefix-end must be below --max-draws"
        if int(getattr(args, "prefix_max_bytes", 0) or 0) <= 0:
            return "--prefix-max-bytes must be positive"
        if int(getattr(args, "prefix_max_ns", 0) or 0) <= 0:
            return "--prefix-max-ns must be positive"
    return None


def _reap_coordinator(coordinator) -> None:
    """Terminate and reap an owned shadow coordinator, never leaving a child."""
    if coordinator is None:
        return
    if coordinator.poll() is None:
        coordinator.terminate()
    try:
        coordinator.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        coordinator.kill()
        try:
            coordinator.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def _coordinator_record(shadow_dir: Path) -> dict | None:
    """Read a complete coordinator JSON object, or None while it is partial."""
    path = Path(shadow_dir) / "coordinator.json"
    try:
        report = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return report if isinstance(report, dict) else None


def _shadow_terminal_record(
    shadow_dir: Path,
    *,
    capture_id: str,
    run: Path | str,
    continuous: bool,
    max_frames: int | None,
) -> dict | None:
    """Return the coordinator record only when it is bound to this capture.

    Binding covers the per-capture identity, the resolved run root, the capture
    mode and, for a bounded sequence, the requested frame count and a
    contiguous frame prefix starting at frame 1.  A stale record from an earlier
    capture, a record for a later frame boundary and an unbound failure record
    are all rejected rather than consumed.  ``None`` means the original wait
    stays in force and the post-wait consumer must not apply the record.
    """
    if not capture_id:
        return None
    report = _coordinator_record(shadow_dir)
    if report is None:
        return None
    if report.get("schema") != "m7-shadow-coordinator/v1":
        return None
    if report.get("capture_id") != capture_id:
        return None
    if report.get("run") != str(Path(run).expanduser().resolve()):
        return None
    status = report.get("status")
    record_mode = report.get("mode")
    if continuous:
        if record_mode != "continuous":
            return None
        if not isinstance(max_frames, int) or isinstance(max_frames, bool):
            return None
        if report.get("max_frames") != max_frames:
            return None
    elif record_mode == "continuous":
        return None
    if status == "failed":
        # A bound failure is terminal: waiting for a later summary cannot make
        # this capture succeed.  The binding checks above still apply.
        return report
    if status != "ok":
        return None
    if continuous:
        frames = report.get("frames")
        if not isinstance(frames, list) or not frames:
            return None
        if len(frames) > max_frames:
            return None
        sequences = []
        for item in frames:
            if not isinstance(item, dict):
                return None
            sequence = item.get("sequence")
            if (not isinstance(sequence, int) or isinstance(sequence, bool)
                    or sequence < 1):
                return None
            sequences.append(sequence)
        if sequences != list(range(1, len(sequences) + 1)):
            return None
        return report if report.get("stop_reason") is not None else None
    seal = report.get("seal")
    if not isinstance(seal, dict) or seal.get("capture_frame_id") is None:
        return None
    if (report.get("pixel_verdict") is None
            and report.get("classification") is None):
        return None
    return report


def _shadow_terminal(shadow_dir: Path, **identity) -> bool:
    """True only for the complete terminal record bound to this capture."""
    return _shadow_terminal_record(shadow_dir, **identity) is not None


def capture(args: argparse.Namespace) -> int:
    import eu4_gfx_startup_trace as startup
    from automation import live_gfx

    output = Path(args.output).expanduser().resolve()
    if output.exists():
        print(f"capture output already exists: {output}", file=sys.stderr)
        return 2
    capture_guard = None
    reservation = None
    guard = campaign_guard.from_env()
    if guard is not None:
        try:
            # Entering controlled-capture mode and authorizing one launch are
            # separate operator decisions.  The capture tool never mints an
            # authorization: the operator issues one with the campaign token and
            # passes its identifier to this process.
            guard.require()
            status = guard.status()
            if status.get("mode") != campaign_guard.MODE_CONTROLLED:
                raise campaign_guard.CampaignRefusal(
                    "live_capture_disabled",
                    "this campaign is autonomous reconstruction; live capture "
                    "requires a controlled_capture campaign")
            reservation = (
                os.environ.get("EU4_CAMPAIGN_AUTHORIZATION")
                or os.environ.get("EU4_CAMPAIGN_RESERVATION") or "").strip()
            if not reservation:
                raise campaign_guard.CampaignRefusal(
                    "launch_authorization_required",
                    "a live launch needs an operator-issued authorization; run "
                    "`campaign_guard authorize-launch` with the campaign token "
                    "and export EU4_CAMPAIGN_AUTHORIZATION=<id>")
            if not guard.validate_authorization(reservation):
                raise campaign_guard.CampaignRefusal(
                    "launch_authorization",
                    f"{reservation} is not an open launch authorization for "
                    "this campaign")
            capture_guard = guard
        except campaign_guard.CampaignRefusal as exc:
            print(f"capture refused: {exc.kind}: {exc.detail}",
                  file=sys.stderr)
            return 3
    # The coordinator record is bound to this capture before any process is
    # started, so a stale record can never settle this launch.
    capture_id = str(uuid.uuid4())
    frame_dir = output / "frame"
    max_events = 1024
    evidence = startup.preflight_ops(
        TARGETS,
        max_events=max_events,
        duration_s=args.duration,
    )
    background_plan = None
    if getattr(args, "background", False):
        from automation import gfx_display, gfx_launch

        try:
            user_data = gfx_launch.preflight_user_data(startup.base.GOG_EXE)
            background_plan = gfx_display.plan_background(
                user_data["user_data"]
            )
        except (gfx_display.DisplayError, gfx_launch.LaunchError, OSError) as exc:
            print(f"capture refused: background display unavailable: {exc}",
                  file=sys.stderr)
            return 2
    token = None
    if args.campaign_run_root:
        try:
            token = live_gfx._claim_launch(
                Path(args.campaign_run_root),
                output,
                TARGETS,
                requested_timeout=args.timeout,
                executable_sha256=evidence.get("executable_sha256"),
            )
        except live_gfx.LaunchLimit as exc:
            print(f"campaign launch refused: {exc}", file=sys.stderr)
            return 3
    if args.boundary_fb < -1 or args.after_ms < 0 or args.max_draws < 1:
        print("capture refused: invalid selection value; --max-draws must be "
              "positive and the scene budget must cover the complete frame",
              file=sys.stderr)
        return 2
    if args.boundary not in ("", "scene"):
        print(f"capture refused: unknown boundary {args.boundary!r}", file=sys.stderr)
        return 2
    checkpoint_event = getattr(args, "default_checkpoint_event", None)
    checkpoint_events = getattr(args, "default_checkpoint_events", None)
    checkpoint_problem = checkpoint_options_error(args)
    if checkpoint_problem is not None:
        print(f"capture refused: {checkpoint_problem}", file=sys.stderr)
        return 2
    checkpoint_budget = int(getattr(args, "checkpoint_max_bytes",
                                    128 * 1024 * 1024))
    env_extra = {
        "EU4_GFX_FRAME_TRACE_DIR": str(frame_dir),
        "EU4_GFX_FRAME_TRACE_CALLERS": args.callers or "",
        "EU4_GFX_FRAME_TRACE_MIN_DRAWS": str(args.min_draws),
        "EU4_GFX_FRAME_TRACE_MAX_FRAME_DRAWS": str(args.max_frame_draws or 0),
        "EU4_GFX_FRAME_TRACE_BOUNDARY_FB": str(args.boundary_fb),
        "EU4_GFX_FRAME_TRACE_BOUNDARY": args.boundary,
        "EU4_GFX_FRAME_TRACE_PRE_POST_CALLERS": args.pre_post_callers or "",
        "EU4_GFX_FRAME_TRACE_OCCURRENCE": str(args.occurrence),
        "EU4_GFX_FRAME_TRACE_MAX_DRAWS": str(args.max_draws),
    }
    if args.at_frame is not None:
        env_extra["EU4_GFX_FRAME_TRACE_AT_FRAME"] = str(args.at_frame)
    if args.after_ms:
        env_extra["EU4_GFX_FRAME_TRACE_AFTER_MS"] = str(args.after_ms)
    if args.log:
        env_extra["EU4_GFX_FRAME_TRACE_LOG"] = str(
            (output / "frame-boundary.log").resolve()
        )
    if getattr(args, "timeline", False):
        env_extra["EU4_GFX_FRAME_TRACE_TIMELINE"] = "1"
    if getattr(args, "copy_trace", None) is not None:
        env_extra["EU4_GFX_COPY_TRACE_DEST"] = str(args.copy_trace)
        env_extra["EU4_GFX_COPY_TRACE_PATH"] = str(
            (output / "copy-trace.jsonl").resolve()
        )
        env_extra["EU4_GFX_COPY_TRACE_LIMIT"] = "4096"
    if getattr(args, "copy_source", False):
        env_extra["EU4_GFX_FRAME_TRACE_CAPTURE_COPY_SOURCE"] = "1"
    if checkpoint_event is not None:
        env_extra["EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENT"] = str(
            checkpoint_event)
    if checkpoint_events is not None:
        env_extra["EU4_GFX_FRAME_TRACE_DEFAULT_CHECKPOINT_EVENTS"] = ",".join(
            str(event) for event in checkpoint_events)
        env_extra["EU4_GFX_FRAME_TRACE_MAX_CHECKPOINTS"] = str(
            int(getattr(args, "max_checkpoints", 4)))
    if checkpoint_event is not None or checkpoint_events is not None:
        # The aggregate readback budget applies to the single-event form too.
        env_extra["EU4_GFX_FRAME_TRACE_CHECKPOINT_MAX_BYTES"] = str(
            checkpoint_budget)
    prefix_end = getattr(args, "prefix_end", None)
    observe_events = getattr(args, "observe_events", None)
    if prefix_end is not None and observe_events:
        return "--prefix-end and --observe-events are mutually exclusive"
    selection_problem = observation_selection_error(
        observe_events, int(getattr(args, "max_draws", 0) or 0))
    if selection_problem is not None:
        return selection_problem
    if observe_events and (
            int(getattr(args, "prefix_max_bytes", 0) or 0) <= 0 or
            int(getattr(args, "prefix_max_ns", 0) or 0) <= 0):
        return "--prefix-max-bytes and --prefix-max-ns must be positive"
    if prefix_end is not None:
        env_extra["EU4_GFX_FRAME_TRACE_PREFIX_END"] = str(prefix_end)
        env_extra["EU4_GFX_FRAME_TRACE_PREFIX_MAX_BYTES"] = str(
            int(getattr(args, "prefix_max_bytes", 384 * 1024 * 1024)))
        env_extra["EU4_GFX_FRAME_TRACE_PREFIX_MAX_NS"] = str(
            int(getattr(args, "prefix_max_ns", 10_000_000_000)))
    sparse_uv2 = getattr(args, "uv2_probe_pixels", None)
    if sparse_uv2:
        if (capture_guard is None or not getattr(args, "seal", False)
                or not getattr(args, "uv2_probe", False)
                or prefix_end != 1 or args.uv2_probe_event != 1
                or (args.uv2_probe_program, args.uv2_probe_framebuffer,
                    args.uv2_probe_texture) != (310, 6, 433)
                or getattr(args, "shadow", False)):
            print("capture refused: sparse UV2 requires an authorized single sealed "
                  "capture of event 1 with --uv2-probe and --prefix-end 1",
                  file=sys.stderr)
            return 2
        env_extra.update(EU4_GFX_UV2_PROBE_PIXELS=sparse_uv2,
                         EU4_GFX_FRAME_TRACE_OBSERVE_DEPTH_STENCIL="1")
    if getattr(args, "uv2_probe", False):
        event = int(args.uv2_probe_event)
        if event < 0 or event >= int(getattr(args, "max_draws", 0) or 0):
            print("capture refused: --uv2-probe-event is outside --max-draws",
                  file=sys.stderr)
            return 2
        if prefix_end is None or int(prefix_end) < event:
            print(
                "capture refused: the uv2 probe requires --prefix-end >= "
                "--uv2-probe-event so the original-shader control has an "
                "authentic post-event observation",
                file=sys.stderr,
            )
            return 2
        for name in ("uv2_probe_program", "uv2_probe_framebuffer",
                     "uv2_probe_texture"):
            if int(getattr(args, name)) < 0:
                print(f"capture refused: --{name.replace('_', '-')} is negative",
                      file=sys.stderr)
                return 2
        env_extra["EU4_GFX_UV2_PROBE"] = "1"
        env_extra["EU4_GFX_UV2_PROBE_EVENT"] = str(event)
        env_extra["EU4_GFX_UV2_PROBE_PROGRAM"] = str(args.uv2_probe_program)
        env_extra["EU4_GFX_UV2_PROBE_FRAMEBUFFER"] = str(
            args.uv2_probe_framebuffer)
        env_extra["EU4_GFX_UV2_PROBE_TEXTURE"] = str(args.uv2_probe_texture)
        env_extra["EU4_GFX_UV2_PROBE_DIR"] = str(output / "uv2-probe")
    shadow_enabled = bool(getattr(args, "shadow", False))
    continuous = bool(getattr(args, "continuous", False))
    asynchronous = bool(getattr(args, "async", False))
    seal_enabled = bool(getattr(args, "seal", False)) or shadow_enabled
    if asynchronous and not continuous:
        print("capture refused: --async requires --continuous",
              file=sys.stderr)
        return 2
    if continuous:
        if not shadow_enabled:
            print("capture refused: --continuous requires --shadow",
                  file=sys.stderr)
            return 2
        if not 1 <= args.max_frames <= 64:
            print("capture refused: invalid --max-frames", file=sys.stderr)
            return 2
        if not 1 <= args.max_pending <= 64:
            print("capture refused: invalid --max-pending", file=sys.stderr)
            return 2
        if args.max_bytes <= 0:
            print("capture refused: invalid --max-bytes", file=sys.stderr)
            return 2
        frame_dir = output / "frames"
        env_extra["EU4_GFX_FRAME_TRACE_DIR"] = str(frame_dir)
    if getattr(args, "seal_log", False) and not seal_enabled:
        print("capture refused: --seal-log requires --seal", file=sys.stderr)
        return 2
    shadow_dir = (
        Path(args.shadow_dir).expanduser().resolve()
        if getattr(args, "shadow_dir", "")
        else output / "shadow"
    )
    if shadow_enabled:
        if args.duration > 600 or args.timeout > 600:
            print("capture refused: invalid shadow launch bounds", file=sys.stderr)
            return 2
        if not 1 <= args.shadow_timeout_ms <= 3_600_000:
            print("capture refused: invalid shadow GPU timeout", file=sys.stderr)
            return 2
        if not 1 <= args.shadow_wait_ms <= 3_600_000:
            print("capture refused: invalid shadow shader wait", file=sys.stderr)
            return 2
        if shadow_dir.exists():
            print(
                f"capture refused: shadow output already exists: {shadow_dir}",
                file=sys.stderr,
            )
            return 2
        if continuous:
            env_extra["EU4_GFX_SHADOW_MODE"] = "continuous"
            env_extra["EU4_GFX_FRAME_TRACE_CONTINUOUS"] = "1"
            env_extra["EU4_GFX_FRAME_TRACE_MAX_FRAMES"] = str(
                args.max_frames)
            env_extra["EU4_GFX_FRAME_TRACE_MAX_PENDING"] = str(
                args.max_pending)
            env_extra["EU4_GFX_FRAME_TRACE_MAX_BYTES"] = str(
                args.max_bytes)
            env_extra["EU4_GFX_SHADOW_MAX_FRAMES"] = str(args.max_frames)
            env_extra["EU4_GFX_SHADOW_MAX_PENDING"] = str(args.max_pending)
            env_extra["EU4_GFX_SHADOW_MAX_BYTES"] = str(args.max_bytes)
            env_extra["EU4_GFX_FRAME_TRACE_STOP_FILE"] = str(
                shadow_dir / "stop")
            if asynchronous:
                env_extra["EU4_GFX_SHADOW_ASYNC"] = "1"
        else:
            env_extra["EU4_GFX_SHADOW_MODE"] = "single-frame"
            env_extra["EU4_GFX_SHADOW_SHADER_DIR"] = str(
                shadow_dir / "shaders")
        env_extra["EU4_GFX_SHADOW_OUTPUT_DIR"] = str(shadow_dir)
        env_extra["EU4_GFX_SHADOW_TIMEOUT_MS"] = str(args.shadow_timeout_ms)
        env_extra["EU4_GFX_SHADOW_WAIT_MS"] = str(args.shadow_wait_ms)
    if seal_enabled:
        timeout_ms = int(getattr(args, "seal_timeout_ms", 0) or 0)
        if timeout_ms < 0 or timeout_ms > 3_600_000:
            print("capture refused: invalid seal timeout", file=sys.stderr)
            return 2
        env_extra["EU4_GFX_FRAME_SEAL"] = "1"
        if timeout_ms:
            env_extra["EU4_GFX_FRAME_TRACE_TIMEOUT_MS"] = str(timeout_ms)
        if not shadow_enabled:
            env_extra["EU4_GFX_FRAME_SEAL_LOG"] = "1"
    coordinator = None
    if shadow_enabled:
        coordinator = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "automation.m6_frame",
                "shadow-coordinator",
                "--run",
                str(output),
                "--shadow-dir",
                str(shadow_dir),
                "--capture-id",
                capture_id,
                "--timeout",
                str(
                    max(
                        300.0,
                        (args.duration
                         + args.shadow_wait_ms / 1000.0
                         + args.shadow_timeout_ms / 1000.0
                         + 120.0)
                        * (args.max_frames if continuous else 1),
                    )
                ),
                "--seal-timeout",
                str(max(120.0, args.duration + 60.0)),
                "--translation-timeout",
                str(args.shadow_wait_ms / 1000.0 + 30.0),
                "--replay-timeout",
                str(args.shadow_timeout_ms / 1000.0 + 60.0),
                *(["--continuous", "--max-frames",
                   str(args.max_frames)]
                  if continuous else []),
                *(["--continue-on-difference"]
                  if getattr(args, "continue_on_difference", False) else []),
                *(["--case-max-bytes", str(getattr(
                    args, "case_max_bytes", 512 * 1024 * 1024))]
                  if continuous else []),
                *(["--case-dir", str(args.case_dir)]
                  if continuous and getattr(args, "case_dir", "") else []),
            ],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    try:
        report = startup.run_ops(
            targets=TARGETS,
            max_events=max_events,
            duration_s=args.duration,
            timeout_s=args.timeout,
            output=output,
            authorize_live=True,
            evidence=evidence,
            env_extra=env_extra,
            retention="state",
            background_plan=background_plan,
            game_args=list(getattr(args, "game_arg", []) or []) or None,
            campaign_reservation=reservation,
            terminal_result=(
                (lambda: _shadow_terminal(
                    shadow_dir,
                    capture_id=capture_id,
                    run=output,
                    continuous=continuous,
                    max_frames=(args.max_frames if continuous else None)))
                if shadow_enabled else None),
        )
    except (startup.StartupError, OSError, RuntimeError) as exc:
        _reap_coordinator(coordinator)
        cleanup_errors = list(getattr(exc, "cleanup_errors", None) or [])
        if token is not None:
            live_gfx._finish_launch(
                Path(args.campaign_run_root), token, pid=None,
                status=f"failed: {exc}",
            )
        if capture_guard is not None and reservation is not None:
            capture_guard.complete(
                reservation, outcome="failed",
                detail=f"{type(exc).__name__}: {exc}",
                evidence={"output": str(output)})
        if cleanup_errors:
            print("capture cleanup failed: " + "; ".join(cleanup_errors),
                  file=sys.stderr)
        print(f"capture failed: {exc}", file=sys.stderr)
        return 4
    result: dict = {
        "schema": "m6-frame-capture/v1",
        "capture_id": capture_id,
        "session": report.get("session"),
        "pid": report.get("pid"),
        "display": report.get("display"),
        "wait_reason": report.get("wait_reason"),
        "terminal_shadow": report.get("terminal_shadow"),
        "frame_dir": str(frame_dir),
        "targets": TARGETS,
        "selection": {
            "callers": args.callers,
            "min_draws": args.min_draws,
            "max_frame_draws": args.max_frame_draws,
            "occurrence": args.occurrence,
            "at_frame": args.at_frame,
            "after_ms": args.after_ms,
            "boundary_fb": args.boundary_fb,
            "boundary": args.boundary,
            "pre_post_callers": args.pre_post_callers,
            "default_checkpoint_event": getattr(
                args, "default_checkpoint_event", None),
            "default_checkpoint_events": getattr(
                args, "default_checkpoint_events", None),
            "max_checkpoints": getattr(args, "max_checkpoints", None),
            "checkpoint_max_bytes": getattr(
                args, "checkpoint_max_bytes", None),
            "prefix_end": getattr(args, "prefix_end", None),
            "observe_events": getattr(args, "observe_events", None),
            "prefix_max_bytes": getattr(args, "prefix_max_bytes", None),
            "prefix_max_ns": getattr(args, "prefix_max_ns", None),
        },
    }
    status = 0
    trace = None
    if continuous:
        try:
            campaign = _read_json(frame_dir / "sequence.json")
            result["campaign"] = campaign
            result["trace"] = {
                "schema": campaign.get("schema"),
                "mode": "continuous",
                "frames_completed": campaign.get("frames_completed"),
                "status": campaign.get("status"),
                "reason": campaign.get("reason"),
            }
            result["coverage"] = {
                "ok": campaign.get("status") == "complete" and
                      campaign.get("frames_completed") == args.max_frames,
                "frames_completed": campaign.get("frames_completed"),
                "status": campaign.get("status"),
                "reason": campaign.get("reason"),
            }
            if not result["coverage"]["ok"]:
                status = 5
            trace = None
        except (FrameError, OSError) as exc:
            result["error"] = str(exc)
            status = 5
    try:
        if not continuous:
            trace = load_trace(frame_dir)
            coverage = coverage_report(trace)
            result["trace"] = {
                "schema": trace.get("schema"),
                "present_index": trace.get("present_index"),
                "events": trace.get("events"),
                "frame": trace.get("frame"),
                "gl": trace.get("gl"),
                "selection": trace.get("selection"),
                "blobs_verified": trace.get("_blob_count_verified"),
            }
            result["coverage"] = coverage
            if not coverage["ok"]:
                status = 5
    except FrameError as exc:
        result["error"] = str(exc)
        status = 5
    if shadow_enabled:
        try:
            if coordinator is not None:
                try:
                    coordinator.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    _reap_coordinator(coordinator)
                if coordinator.poll() is None:
                    raise FrameError(
                        "in-process shadow coordinator did not exit after "
                        "publication")
            shadow_report = _shadow_terminal_record(
                shadow_dir,
                capture_id=capture_id,
                run=output,
                continuous=continuous,
                max_frames=(args.max_frames if continuous else None))
            if shadow_report is None:
                raise FrameError(
                    "in-process shadow coordinator produced no bound "
                    "terminal report")
            native_failure = apply_shadow_report(result, shadow_report)
            if native_failure is not None:
                raise FrameError(
                    "in-process shadow failed: " + native_failure
                )
        except (FrameError, OSError, subprocess.TimeoutExpired) as exc:
            result["shadow_error"] = str(exc)
            status = 7 if status == 0 else status
    if seal_enabled and not continuous:
        try:
            seal = load_seal(frame_dir)
            notification = None
            if not shadow_enabled:
                notification = load_notification(output)
                if int(notification.get("pid", -1)) != int(seal.get("pid", -2)):
                    raise FrameError("notification pid does not match the seal")
                if notification.get("capture_frame_id") != seal.get(
                    "capture_frame_id"
                ):
                    raise FrameError(
                        "notification frame id does not match the seal"
                    )
                for field in (
                    "manifest_sha256",
                    "reference_sha256",
                    "operation_count",
                    "draw_count",
                    "clear_count",
                    "copy_count",
                ):
                    if notification.get(field) != seal.get(field):
                        raise FrameError(
                            f"notification {field} does not match the seal"
                        )
            if int(seal.get("pid", -1)) != int(report.get("pid", -2)):
                raise FrameError("sealed frame pid does not match the launch")
            result["seal"] = {
                key: seal.get(key)
                for key in (
                    "schema",
                    "capture_frame_id",
                    "session_id",
                    "pid",
                    "boundary",
                    "present_index",
                    "scene_index",
                    "operation_count",
                    "draw_count",
                    "clear_count",
                    "copy_count",
                    "width",
                    "height",
                    "pixel_format",
                    "manifest_sha256",
                    "reference_sha256",
                    "readback_ns",
                    "finalize_ns",
                    "seal_ns",
                )
            }
            if notification is not None:
                result["notification"] = notification
            status = 0 if status == 0 else status
        except FrameError as exc:
            result["seal_error"] = str(exc)
            status = 6
    if continuous:
        campaign_frames = (result.get("campaign") or {}).get("frames") or []
        present_indices = [item.get("present_index")
                           for item in campaign_frames]
        identity_complete = all(isinstance(item, int) and item > 0
                                for item in present_indices)
        identity_gap_free = bool(
            identity_complete
            and all(present_indices[index] == present_indices[index - 1] + 1
                    for index in range(1, len(present_indices))))
        result["seal"] = {
            "mode": "continuous",
            "engine_identity": {
                "status": ("gap_free" if identity_gap_free
                           else "gap" if identity_complete
                           else "unavailable"),
                "gap_free": identity_gap_free,
                "present_indices": present_indices,
                "scene_indices": [item.get("scene_index")
                                  for item in campaign_frames],
            },
            "frames": [
                {
                    "sequence": item.get("sequence"),
                    "capture_frame_id": item.get("capture_frame_id"),
                    "present_index": item.get("present_index"),
                    "scene_index": item.get("scene_index"),
                    "manifest_sha256": item.get("manifest_sha256"),
                    "reference_sha256": item.get("reference_sha256"),
                }
                for item in campaign_frames
            ],
        }
    raw_cleanup_ok = report.get("cleanup_ok", _MISSING_CLEANUP)
    cleanup_record = report.get("cleanup")
    cleanup_errors = list(report.get("cleanup_errors") or [])
    nested_failed = (isinstance(cleanup_record, dict)
                     and (cleanup_record.get("ok") is False
                          or bool(cleanup_record.get("errors"))))
    if raw_cleanup_ok is _MISSING_CLEANUP:
        # Legacy reports predate the explicit field.  A report with no cleanup
        # fields at all is not a failure; any explicit failure signal is.
        cleanup_ok = not nested_failed and not cleanup_errors
    else:
        cleanup_ok = (raw_cleanup_ok is True and not nested_failed
                      and not cleanup_errors)
    display_record = report.get("display")
    result["cleanup"] = {
        "ok": cleanup_ok,
        "errors": cleanup_errors,
        "process_alive": report.get("process_alive"),
        "termination": report.get("termination"),
        "lease": report.get("lease"),
        "display_restore": (
            display_record.get("restore")
            if isinstance(display_record, dict) else None),
    }
    if not cleanup_ok:
        cleanup_detail = "cleanup failed: " + (
            "; ".join(cleanup_errors) or "unspecified cleanup failure")
        result["cleanup_failure"] = cleanup_detail
        # A cleanup failure can never turn a failed capture into a success.
        # When the trace/seal/shadow checks already failed, keep their status
        # and error as the primary reason and report cleanup separately.
        if status == 0:
            status = 8
            result["error"] = cleanup_detail
    result["outcome"] = "ok" if status == 0 else "failed"
    if capture_guard is not None and reservation is not None:
        seal_value = result.get("seal") or {}
        sealed_frames = (seal_value.get("frames")
                         if isinstance(seal_value, dict) else None) or []
        detail = str(result.get("error") or result.get("seal_error")
                     or result.get("shadow_error") or "")
        if not cleanup_ok and detail != result.get("cleanup_failure"):
            detail = "; ".join(filter(
                None, [detail, str(result.get("cleanup_failure") or "")]))
        capture_guard.complete(
            reservation,
            outcome="ok" if status == 0 else "failed",
            detail=detail,
            evidence={"output": str(output), "status": status,
                      "pid": report.get("pid"),
                      "frames": len(sealed_frames),
                      "cleanup_ok": cleanup_ok})
    (output / "capture-report.json").write_text(json.dumps(result, indent=2) + "\n")
    if token is not None:
        live_gfx._finish_launch(
            Path(args.campaign_run_root),
            token,
            pid=report.get("pid"),
            status=("completed" if status == 0 else
                    "failed: cleanup" if status == 8 else
                    "failed: frame-trace"),
        )
    print(json.dumps(result, indent=2))
    return status


# ---------------------------------------------------------------------------
# Shader translation, replay and comparison
# ---------------------------------------------------------------------------

REPLAY_HOST_SOURCE, REPLAY_HOST_CLI_SOURCE = (
    ROOT / source for source in replay_host_dependencies.REPLAY_SOURCES)
REPLAY_HOST = BENCH / ".build" / "gfx_frame_replay"


_REPLAY_BUILD_THREAD_LOCK = threading.Lock()


def _replay_host_stamp() -> Path:
    return Path(str(REPLAY_HOST) + ".deps.json")


def _git_head_revision() -> str | None:
    """Revision of the checkout the replay host was built from.

    Read from the Git metadata files rather than by spawning git: the build
    path is exercised by tests that replace ``subprocess.run`` with the
    compiler fake, and a build must not depend on an external process just to
    label its own output.  Worktrees (``.git`` is a file) are resolved.
    """
    git_dir = ROOT / ".git"
    try:
        if git_dir.is_file():
            text = git_dir.read_text(encoding="utf-8").strip()
            if not text.startswith("gitdir:"):
                return None
            target = Path(text.split(":", 1)[1].strip())
            git_dir = target if target.is_absolute() else (ROOT / target)
        common = git_dir
        common_file = git_dir / "commondir"
        if common_file.is_file():
            target = Path(common_file.read_text(encoding="utf-8").strip())
            common = target if target.is_absolute() else (git_dir / target)
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            reference = head.split(":", 1)[1].strip()
            reference_path = git_dir / reference
            if not reference_path.is_file():
                reference_path = common / reference
            if reference_path.is_file():
                head = reference_path.read_text(encoding="utf-8").strip()
            else:
                packed = common / "packed-refs"
                if not packed.is_file():
                    return None
                resolved = None
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.startswith("#") or " " not in line:
                        continue
                    candidate, _, name = line.partition(" ")
                    if name.strip() == reference:
                        resolved = candidate.strip()
                        break
                if resolved is None:
                    return None
                head = resolved
    except (OSError, UnicodeError):
        return None
    if len(head) not in (40, 64):
        return None
    if any(character not in "0123456789abcdef" for character in head):
        return None
    return head


def _replay_host_lock() -> Path:
    return Path(str(REPLAY_HOST) + ".build.lock")


def _replay_stamp_matches(stamp: Path, identity: dict, host: Path) -> bool:
    if not host.is_file() or not stamp.is_file():
        return False
    try:
        recorded = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if recorded.get("fingerprint") != identity["fingerprint"]:
        return False
    return recorded.get("binary_sha256") == _sha256(host)


def replay_host_producer(host: Path | str | None = None) -> dict:
    """Identity of the binary that produced a replay result."""
    host = Path(host) if host is not None else REPLAY_HOST
    stamp = Path(str(host) + ".deps.json")
    identity = replay_host_dependencies.build_identity(
        root=ROOT, sources=(REPLAY_HOST_SOURCE, REPLAY_HOST_CLI_SOURCE), strict=True)
    if not _replay_stamp_matches(stamp, identity, host):
        raise FrameError(
            "replay host build receipt does not match the current binary and "
            "dependency closure")
    try:
        recorded = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FrameError(f"unreadable replay host build receipt: {exc}") from exc
    return {
        "schema": "gfx-replay-producer/v1",
        "executable": str(host),
        "executable_sha256": _sha256(host),
        "build_fingerprint": identity["fingerprint"],
        "build_stamp_sha256": _sha256(stamp),
        "source_revision": recorded.get("source_revision"),
        "build_dependencies": identity["dependencies"],
    }


def snapshot_replay_host(host: Path | str | None = None,
                        *, attempts: int = 3) -> tuple[Path, dict]:
    """Create an immutable per-run executable snapshot of the replay host.

    The producer identity is read first, the bytes are copied, and the copy is
    hashed.  If a concurrent build replaced the shared host between those
    steps, the snapshot digest will not match the identity and the operation
    retries.  The returned receipt describes the snapshot that will actually be
    executed, not merely the shared path.
    """
    host = Path(host) if host is not None else REPLAY_HOST
    for _attempt in range(max(1, attempts)):
        producer = replay_host_producer(host)
        descriptor, name = tempfile.mkstemp(prefix="gfx-frame-replay-host.")
        os.close(descriptor)
        snapshot = Path(name)
        try:
            shutil.copyfile(host, snapshot)
            os.chmod(snapshot, host.stat().st_mode & 0o777)
            digest = _sha256(snapshot)
            if digest != producer.get("executable_sha256"):
                snapshot.unlink()
                continue
            value = dict(producer)
            value["executable"] = str(snapshot)
            value["executable_sha256"] = digest
            return snapshot, value
        except BaseException:
            try:
                snapshot.unlink()
            except FileNotFoundError:
                pass
            raise
    raise FrameError(
        "replay host changed repeatedly while creating the per-run snapshot")


def build_replay_host(force: bool = False) -> Path:
    """Build and publish the replay host under a narrow exclusive lock.

    Publication is atomic: clang writes a private temporary file, the file is
    validated, and only then is it renamed over the published path.  A stamp
    containing the source/build fingerprint and compiled-binary digest is
    written after publication; a failed, timed-out or interrupted build leaves
    neither a stamp nor a binary that could be mistaken for current evidence.
    """
    with _REPLAY_BUILD_THREAD_LOCK:
        lock_path = _replay_host_lock()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return _build_replay_host_locked(force=force)
            finally:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass


def _build_replay_host_locked(force: bool = False) -> Path:
    sources = (REPLAY_HOST_SOURCE, REPLAY_HOST_CLI_SOURCE)
    identity = replay_host_dependencies.build_identity(
        root=ROOT, sources=sources, strict=True)
    stamp = _replay_host_stamp()
    if not force and _replay_stamp_matches(stamp, identity, REPLAY_HOST):
        return REPLAY_HOST
    REPLAY_HOST.parent.mkdir(parents=True, exist_ok=True)
    # Invalidate the previous receipt before compiling so no previous success
    # can be mistaken for the result of this build.
    try:
        stamp.unlink()
    except FileNotFoundError:
        pass
    temporary = REPLAY_HOST.with_name(
        f".{REPLAY_HOST.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        built = subprocess.run(
            replay_host_dependencies.build_command(temporary, sources),
            env=identity["environment"],
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        if built.returncode:
            raise FrameError(
                built.stderr.strip() or "frame replay host build failed")
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise FrameError("frame replay host build produced no binary")
        binary_sha = _sha256(temporary)
        if not binary_sha:
            raise FrameError("frame replay host build produced an unreadable binary")
        os.replace(temporary, REPLAY_HOST)
        payload = {
            "schema": "gfx-replay-host-build/v1",
            "fingerprint": identity["fingerprint"],
            "binary_sha256": binary_sha,
            "source_revision": _git_head_revision(),
            "dependencies": identity["dependencies"],
            "compiler": identity["compiler"],
            "flags": identity["flags"],
            "environment": identity["environment"],
        }
        stamp_tmp = stamp.with_name(stamp.name + f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
        stamp_tmp.write_text(json.dumps(payload, indent=2) + "\n",
                             encoding="utf-8")
        os.replace(stamp_tmp, stamp)
        return REPLAY_HOST
    except subprocess.TimeoutExpired as exc:
        _discard_build_outputs(temporary, stamp, REPLAY_HOST)
        raise FrameError("frame replay host build timed out") from exc
    except BaseException:
        _discard_build_outputs(temporary, stamp, REPLAY_HOST)
        raise


def _discard_build_outputs(temporary: Path, stamp: Path, host: Path) -> None:
    for path in (temporary, stamp, host):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _tool_identity(name: str) -> dict:
    found = shutil.which(name)
    if not found:
        return {"name": name, "available": False, "sha256": None}
    path = Path(found)
    try:
        return {
            "name": name,
            "available": True,
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    except OSError:
        return {"name": name, "available": True, "sha256": None}


def _translator_identity() -> dict:
    """Content identity of every input that can change a translation.

    A filename/size/mtime fingerprint missed both the external compiler tools
    and an in-place translator edit that preserved size and time.  Hash the
    translator bytes and both tool binaries so a stale cache cannot be reused
    after any of them changes.
    """
    from automation import gfx_shader_translate

    path = Path(gfx_shader_translate.__file__)
    try:
        digest = _sha256(path)
        size = path.stat().st_size
    except OSError:
        digest, size = "", 0
    return {
        "schema": "gfx-translator-identity/v2",
        "path": path.name,
        "bytes": size,
        "sha256": digest,
        "tools": {
            "glslang": _tool_identity("glslangValidator"),
            "spirv_cross": _tool_identity("spirv-cross"),
        },
    }


def _shader_source_identity(trace: dict) -> dict:
    """Content identity of the translated shader sources for one frame.

    Translation is deterministic in the source program/stage digests; a cache
    keyed by these digests can never reuse a translation for different source
    content.  The key deliberately excludes frame/event identity but includes
    the translator identity, so an older translator's cached output is never
    reused after the translator changes.
    """
    programs: dict[str, dict] = {}
    for event in trace.get("_events") or []:
        if event.get("kind") != "draw":
            continue
        program = event.get("program") or {}
        name = str(program.get("name") or "")
        if not name:
            continue
        record = programs.setdefault(name, {"stages": {}, "attributes": {}})
        for shader in program.get("shaders") or []:
            stage = str(shader.get("stage") or "")
            digest = str(shader.get("sha256") or "")
            if stage and digest:
                record["stages"][stage] = digest
        for attribute in event.get("attributes") or []:
            attribute_name = str(attribute.get("name") or "")
            location = attribute.get("location")
            if attribute_name and location is not None:
                record["attributes"][attribute_name] = int(location)
    # Attribute/varying locations are injected into the compiler input, so two
    # programs sharing raw stage bytes but differing in linkage are different
    # compiler inputs.  The attribute map is part of the cache identity.
    projected = {}
    for name, record in sorted(programs.items()):
        projected[name] = {
            "stages": dict(sorted(record["stages"].items())),
            "attribute_locations": dict(sorted(
                record["attributes"].items())),
        }
    identity = {
        "schema": "gfx-shader-source-identity/v2",
        "translator": _translator_identity(),
        "programs": projected,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    identity["sha256"] = _bytes_sha256(canonical.encode("utf-8"))
    return identity


_CACHE_STAGE_ARTIFACTS = (
    ("msl_file", "msl_sha256"),
    ("glsl_file", "glsl_sha256"),
    ("spv_file", "spv_sha256"),
)


def _translation_cache_valid(cache_root: Path, identity: dict) -> bool:
    """A cache entry is valid only when its identity and every stage artifact
    are present and match the digest recorded by the translator.

    ``shaders.json`` existing is not a cache hit: a concurrent publisher or an
    interrupted copy can leave a report whose MSL files are missing or
    truncated.  Adoption requires a complete, content-verified entry.
    """
    try:
        metadata = json.loads(
            (cache_root / "translation-cache.json").read_text())
        report = json.loads((cache_root / "shaders.json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if metadata.get("schema") != "gfx-shader-translation-cache/v1":
        return False
    if not isinstance(metadata.get("identity"), dict):
        return False
    recorded = metadata["identity"].get("sha256")
    if recorded != identity.get("sha256"):
        return False
    if report.get("schema") != "gfx-shader-translation/v1":
        return False
    if report.get("ok") is not True:
        return False
    programs = report.get("programs")
    if not isinstance(programs, list) or not programs:
        return False
    for program in programs:
        if not isinstance(program, dict) or program.get("status") != "ok":
            return False
        stages = program.get("stages")
        if not isinstance(stages, dict) or not stages:
            return False
        for stage in stages.values():
            if not isinstance(stage, dict):
                return False
            for file_field, hash_field in _CACHE_STAGE_ARTIFACTS:
                name = stage.get(file_field)
                expected = stage.get(hash_field)
                if (not isinstance(name, str) or not name
                        or Path(name).name != name):
                    return False
                path = cache_root / name
                if not path.is_file():
                    return False
                if (not isinstance(expected, str) or len(expected) != 64
                        or _sha256(path) != expected):
                    return False
    return True


def _copy_translation_cache(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def _publish_translation_cache(source: Path, cache_root: Path,
                               identity: dict) -> str:
    """Publish a validated translation without adopting a corrupt winner.

    A unique sibling staging directory means two builders never share a
    partial directory.  An existing winner is adopted only after full
    validation; an invalid winner is quarantined and replaced atomically.
    """
    cache_root.parent.mkdir(parents=True, exist_ok=True)
    partial = cache_root.with_name(
        f"{cache_root.name}.partial-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    shutil.copytree(source, partial)
    try:
        for _ in range(8):
            if cache_root.exists():
                if _translation_cache_valid(cache_root, identity):
                    return "existing"
                quarantine = cache_root.with_name(
                    f"{cache_root.name}.invalid-{os.getpid()}-"
                    f"{uuid.uuid4().hex[:8]}")
                try:
                    os.replace(cache_root, quarantine)
                except FileNotFoundError:
                    # Another repairer removed or replaced it; re-evaluate.
                    continue
                except OSError:
                    continue
                shutil.rmtree(quarantine, ignore_errors=True)
            try:
                os.replace(partial, cache_root)
            except FileNotFoundError:
                return "refused"
            except OSError:
                # Another builder published first; adopt only if it verifies.
                continue
            if _translation_cache_valid(cache_root, identity):
                return "published"
            # Our published copy failed verification (for example a
            # filesystem error during the rename): remove it so no later
            # reader can adopt corrupt bytes.
            rejected = cache_root.with_name(
                f"{cache_root.name}.invalid-{os.getpid()}-"
                f"{uuid.uuid4().hex[:8]}")
            try:
                os.replace(cache_root, rejected)
                shutil.rmtree(rejected, ignore_errors=True)
            except OSError:
                pass
            return "refused"
        return "refused"
    finally:
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)


def translate_command(args: argparse.Namespace) -> int:
    from automation import gfx_shader_translate

    frame_dir = Path(args.frame).expanduser().resolve()
    out_dir = (
        Path(args.out).expanduser().resolve()
        if args.out
        else frame_dir / "shaders"
    )
    cache_dir = (Path(args.cache_dir).expanduser().resolve()
                 if getattr(args, "cache_dir", None) else None)
    if out_dir.exists() and args.force:
        shutil.rmtree(out_dir)
    try:
        trace = load_trace(frame_dir)
    except FrameError as exc:
        print(json.dumps({"status": "invalid", "reason": str(exc)}, indent=2))
        return 1
    identity = _shader_source_identity(trace)
    cache_root = cache_dir / identity["sha256"] if cache_dir else None
    cache_hit = False
    publish_status = None
    report = None
    if (cache_root is not None and not args.force
            and _translation_cache_valid(cache_root, identity)):
        copied = False
        try:
            _copy_translation_cache(cache_root, out_dir)
            copied = True
        except OSError:
            cache_hit = False
        else:
            # A winner can be repaired or removed between validation and the
            # copy.  Validate the staged copy itself before reusing it.
            cache_hit = _translation_cache_valid(out_dir, identity)
        if copied and not cache_hit:
            shutil.rmtree(out_dir, ignore_errors=True)
    if not cache_hit:
        report = gfx_shader_translate.translate_trace(frame_dir, out_dir)
        (out_dir / "translation-cache.json").write_text(json.dumps({
            "schema": "gfx-shader-translation-cache/v1",
            "identity": identity,
            "cache_dir": str(cache_dir) if cache_dir else None,
            "hit": False,
        }, indent=2) + "\n")
        if cache_root is not None:
            if (report.get("ok")
                    and _translation_cache_valid(out_dir, identity)):
                publish_status = _publish_translation_cache(
                    out_dir, cache_root, identity)
            else:
                # Never publish a partial or unverified translation; the next
                # invocation rebuilds rather than adopting it.
                publish_status = "refused"
    if report is None:
        try:
            report = json.loads((out_dir / "shaders.json").read_text())
            report = {"ok": True, "programs": report.get("programs") or []}
        except (OSError, json.JSONDecodeError):
            report = {"ok": False, "programs": []}
    (out_dir / "translation-cache.json").write_text(json.dumps({
        "schema": "gfx-shader-translation-cache/v1",
        "identity": identity,
        "cache_dir": str(cache_dir) if cache_dir else None,
        "hit": cache_hit,
    }, indent=2) + "\n")
    summary = {
        "status": "ok" if report["ok"] else "partial",
        "programs": len(report["programs"]),
        "failed": [
            {"program": entry["program"], "error": entry.get("error")}
            for entry in report["programs"]
            if entry["status"] != "ok"
        ],
        "out": str(out_dir),
        "draws": len(trace.get("_draws") or []),
        "translation_cache": {
            "hit": cache_hit,
            "key": identity["sha256"],
            "dir": str(cache_dir) if cache_dir else None,
            "publish": publish_status,
        },
    }
    print(json.dumps(summary, indent=2))
    return 0 if report["ok"] else 2


def _replay_evidence(args: argparse.Namespace) -> dict:
    """Stable identity of the direct replay command for durable evidence."""
    argv = [sys.executable, "-m", "automation.m6_frame", "replay",
            "--frame", str(args.frame), "--out", str(args.out)]
    if getattr(args, "shaders", None):
        argv += ["--shaders", str(args.shaders)]
    if getattr(args, "only_event", None) is not None:
        argv += ["--only-event", str(args.only_event)]
    return {
        "entry_point": "automation.m6_frame.replay_command",
        "tool": "eu4_replay frame",
        "frame": str(args.frame),
        "out": str(args.out),
        "command": argv,
    }


def _replay_mark(evidence: dict | None, **values) -> None:
    if evidence is None:
        return
    for key, value in values.items():
        if value is not None:
            evidence[key] = value


def replay_command(args: argparse.Namespace) -> int:
    """Execute the direct frame-replay entry point.

    Replay executions are not budgeted, but an agent-facing replay must state a
    concise experiment rationale, and an explicit campaign stop still refuses.
    Execution safety remains the replay host's timeout and process cleanup; a
    failed replay is reported by its own result and is never promoted to
    equivalence.
    """
    from automation import campaign_guard, experiment_rationale

    try:
        # Removing quotas must not also bypass explicit campaign cancellation.
        campaign_guard.require_active_from_env()
    except campaign_guard.CampaignRefusal as exc:
        print(f"replay refused: {exc.kind}: {exc.detail}", file=sys.stderr)
        return 3
    try:
        rationale = experiment_rationale.require(
            getattr(args, "rationale", None), run_root=Path.cwd())
    except experiment_rationale.RationaleError as exc:
        print(f"replay refused: rationale: {exc}", file=sys.stderr)
        return 3
    out = Path(args.out).expanduser().resolve()
    code = _replay_command_body(args, evidence=_replay_evidence(args))
    if code == 0:
        try:
            result = json.loads((out / "replay.json").read_text())
        except (OSError, json.JSONDecodeError):
            result = {}
        experiment_rationale.record(out, rationale, result=result)
    return code


def uv2_probe_replay_env(out: Path, args: argparse.Namespace) -> dict | None:
    """Child environment for the opt-in Metal uv2 diagnostic probe.

    The probe is disabled unless ``--uv2-probe`` was requested; the returned
    mapping is the inherited environment plus exactly the probe variables.
    ``None`` means the selection is invalid and the replay must not run.
    """
    env = dict(os.environ)
    if not getattr(args, "uv2_probe", False):
        return env
    event = int(args.uv2_probe_event)
    if event < 0:
        return None
    env["EU4_FRAME_REPLAY_UV2_PROBE"] = "1"
    env["EU4_FRAME_REPLAY_UV2_PROBE_EVENT"] = str(event)
    env["EU4_FRAME_REPLAY_UV2_PROBE_PROGRAM"] = str(args.uv2_probe_program)
    env["EU4_FRAME_REPLAY_UV2_PROBE_FRAMEBUFFER"] = str(
        args.uv2_probe_framebuffer)
    env["EU4_FRAME_REPLAY_UV2_PROBE_TEXTURE"] = str(args.uv2_probe_texture)
    env["EU4_FRAME_REPLAY_UV2_PROBE_DIR"] = str(Path(out) / "uv2-probe")
    return env


def _replay_command_body(args: argparse.Namespace,
                         evidence: dict | None = None) -> int:
    _replay_mark(evidence, status="running")
    frame_dir = Path(args.frame).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    if out == frame_dir or frame_dir in out.parents or out in frame_dir.parents:
        _replay_mark(evidence, status="refused",
                     reason="replay output overlaps the captured frame")
        raise FrameError(
            "replay output must not overlap the captured source frame directory"
        )
    shaders = (
        Path(args.shaders).expanduser().resolve()
        if args.shaders
        else frame_dir / "shaders"
    )
    sparse_uv2 = getattr(args, "uv2_probe_pixels", None)
    sparse_primitives = None
    uv2_checkpoints = None
    if sparse_uv2:
        from automation import gfx_uv2_probe

        if (not getattr(args, "uv2_probe", False) or args.uv2_probe_event != 1
                or (args.uv2_probe_program, args.uv2_probe_framebuffer,
                    args.uv2_probe_texture) != (310, 6, 433)
                or args.only_event is not None or args.stop_after is not None
                or args.seed_marked or args.depth_from_prefix or args.checkpoint_request):
            raise FrameError("sparse UV2 requires the normal sealed replay and "
                             "its automatic event-1 checkpoints")
        try:
            sparse_primitives, uv2_checkpoints = gfx_uv2_probe.sparse_replay_inputs(
                frame_dir, sparse_uv2)
        except (gfx_uv2_probe.ProbeError, OSError, ValueError) as exc:
            raise FrameError(str(exc)) from exc
    if args.only_event is not None:
        try:
            trace = load_trace(frame_dir)
        except FrameError as exc:
            _replay_mark(evidence, status="invalid", reason=str(exc))
            print(json.dumps({"status": "invalid", "reason": str(exc)}, indent=2))
            return 1
        events = trace.get("_events") or []
        if not 0 <= args.only_event < len(events):
            reason = (
                f"event {args.only_event} is outside the "
                f"{len(events)}-event frame")
            _replay_mark(evidence, status="invalid", reason=reason)
            print(json.dumps({"status": "invalid", "reason": reason}, indent=2))
            return 1
        selected = events[args.only_event]
        if selected.get("kind") != "draw":
            reason = (f"event {args.only_event} is a "
                      f"{selected.get('kind')} event, not a draw")
            _replay_mark(evidence, status="invalid", reason=reason)
            print(json.dumps({"status": "invalid", "reason": reason}, indent=2))
            return 1
        if not selected.get("pre_image"):
            reason = (f"event {args.only_event} has no authentic GL "
                      "pre-image")
            _replay_mark(evidence, status="invalid", reason=reason)
            print(json.dumps({"status": "invalid", "reason": reason}, indent=2))
            return 1
    if out.exists() and not args.force:
        _replay_mark(evidence, status="refused",
                     reason=f"replay output already exists: {out}",
                     stderr_tail=f"replay output already exists: {out}")
        print(f"replay output already exists: {out}", file=sys.stderr)
        return 2
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    try:
        host = build_replay_host(force=args.force_build)
    except FrameError as exc:
        _replay_mark(evidence, status="error", reason=str(exc))
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    command = [
        str(host),
        "--frame",
        str(frame_dir),
        "--shaders",
        str(shaders),
        "--out",
        str(out),
    ]
    if args.stop_after is not None:
        command += ["--stop-after", str(args.stop_after)]
    if args.seed_marked:
        command.append("--seed-marked")
    if args.capture_post:
        command.append("--capture-post")
    if args.only_event is not None:
        command += ["--only-event", str(args.only_event)]
        command.append("--capture-pre")
        command.append("--capture-post")
    if args.depth_from_prefix:
        command.append("--depth-from-prefix")
    checkpoint_path = (
        Path(args.checkpoint_request).expanduser().resolve()
        if getattr(args, "checkpoint_request", None) else None)
    if checkpoint_path is not None and not checkpoint_path.is_file():
        print(f"replay refused: checkpoint request is absent: {checkpoint_path}",
              file=sys.stderr)
        return 2
    if getattr(args, "uv2_probe", False):
        if args.only_event is not None:
            print("replay refused: --uv2-probe is incompatible with "
                  "--only-event (checkpoint and isolation flags conflict)",
                  file=sys.stderr)
            return 2
        if checkpoint_path is None:
            checkpoint_path = out / "uv2-checkpoint-request.json"
            checkpoint_path.write_text(json.dumps({
                "schema": "gfx-frame-checkpoint-request/v1",
                "checkpoints": uv2_checkpoints or [{
                    "event": int(args.uv2_probe_event),
                    "target": str(args.uv2_probe_texture),
                    "resource": f"tex:{args.uv2_probe_texture}",
                    "file": "uv2-color.bgra",
                    "timing": "post_event",
                }],
            }, indent=2) + "\n")
    if checkpoint_path is not None:
        command += ["--checkpoints", str(checkpoint_path)]
    child_env = uv2_probe_replay_env(out, args)
    if child_env is None:
        print("replay refused: --uv2-probe-event is negative", file=sys.stderr)
        return 2
    if sparse_uv2:
        from automation import gfx_independent_replay

        # Only the math selection and probe settings are accepted for this
        # diagnostic. Other replay experiments would change the normal pass.
        child_env = {k: v for k, v in child_env.items()
                     if not k.startswith("EU4_FRAME_REPLAY_")
                     or k.startswith("EU4_FRAME_REPLAY_UV2_PROBE")
                     or k == "EU4_FRAME_REPLAY_FAST_MATH"}
        child_env.pop("EU4_FRAME_REPLAY_UV2_PROBE_VARYING", None)
        child_env.update(EU4_FRAME_REPLAY_UV2_PROBE_PIXELS=sparse_uv2,
                         EU4_FRAME_REPLAY_UV2_PROBE_PRIMITIVES=sparse_primitives)
    producer = None
    from automation import launch_lease

    with launch_lease.gpu_execution():
        # A stop or deadline expiry while this replay waited for the GPU lock
        # must not start the replay host.
        try:
            campaign_guard.require_active_from_env()
        except campaign_guard.CampaignRefusal as exc:
            _replay_mark(evidence, status="refused", reason=str(exc))
            print(f"replay refused: {exc.kind}: {exc.detail}",
                  file=sys.stderr)
            return 3
        snapshot = None
        try:
            if sparse_uv2:
                snapshot, producer = snapshot_replay_host(host)
                command[0] = str(snapshot)
            ran = subprocess.run(command, capture_output=True, text=True,
                                 check=False, timeout=1200, env=child_env)
        finally:
            if snapshot is not None:
                snapshot.unlink(missing_ok=True)
    result_path = out / "replay.json"
    if ran.returncode != 0 or not result_path.is_file():
        _replay_mark(evidence, status="error", returncode=ran.returncode,
                     stderr_tail=(ran.stderr or "")[-2000:],
                     replay_command=command,
                     reason=(f"replay host exited {ran.returncode}"
                             if ran.returncode != 0
                             else "replay host produced no replay.json"))
        print(
            json.dumps(
                {
                    "status": "error",
                    "returncode": ran.returncode,
                    "stderr": ran.stderr[-4000:],
                },
                indent=2,
            )
        )
        return 1
    result = json.loads(result_path.read_text())
    if producer is not None:
        try:
            gfx_independent_replay.write_producer_receipt(
                out, frame_dir, producer, result_path, shaders=shaders, env=child_env)
        except gfx_independent_replay.ReplayError as exc:
            raise FrameError(f"sparse UV2 producer receipt: {exc}") from exc
    _replay_mark(evidence, status=result.get("status"),
                 returncode=ran.returncode,
                 stderr_tail=(ran.stderr or "")[-2000:],
                 replay_command=command,
                 reason=(result.get("status")
                         if result.get("status") != "ok" else None))
    summary = {
        "status": result.get("status"),
        "events": result.get("events"),
        "encoded": result.get("encoded"),
        "failed": result.get("failed"),
        "command_status": result.get("command_status"),
        "readback": result.get("readback"),
        "frame": result.get("frame"),
        "isolated": result.get("isolated"),
        "failures": (result.get("failures") or [])[:8],
    }
    print(json.dumps(summary, indent=2))
    return 0 if result.get("status") == "ok" else 1


def compare_command(args: argparse.Namespace) -> int:
    from automation import m6_compare

    reference = Path(args.reference).expanduser().resolve()
    candidate = Path(args.candidate).expanduser().resolve()
    policy_path = (
        Path(args.policy).expanduser().resolve()
        if args.policy
        else ROOT / "analysis" / "m6" / "comparison-policy.json"
    )
    out = (
        Path(args.out).expanduser().resolve()
        if args.out
        else candidate / "differential"
    )
    out.mkdir(parents=True, exist_ok=True)
    try:
        policy = json.loads(policy_path.read_text())
        report = m6_compare.compare(reference, candidate, policy)
        if args.difference:
            report["difference"] = m6_compare.write_difference(
                reference,
                candidate,
                report["reference"]["width"],
                report["reference"]["height"],
                out,
            )
    except (m6_compare.CompareError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    (out / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "mismatched_pixels": report["statistics"]["mismatched_pixels"],
                "pixels": report["statistics"]["pixels"],
                "max_channel_error": report["statistics"]["max_channel_error"],
                "mean_max_channel_error": report["statistics"][
                    "mean_max_channel_error"
                ],
                "mean_abs_error": report["statistics"]["mean_abs_error"],
                "worst_region": report["worst_region"],
            },
            indent=2,
        )
    )
    return 0 if report["status"] == "pass" else 2


def _bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def selected_event_report(
    frame_dir: Path, candidate: Path, event_index: int
) -> dict:
    """One selected draw's authentic GL pre/post versus its isolated Metal run."""
    from automation import m6_compare

    try:
        trace = load_trace(frame_dir)
    except FrameError as exc:
        return {"status": "invalid", "reason": str(exc)}
    events = trace.get("_events") or []
    if not 0 <= event_index < len(events):
        return {
            "status": "invalid",
            "reason": f"event {event_index} is outside the {len(events)}-event frame",
        }
    event = events[event_index]
    if event.get("kind") != "draw":
        return {
            "status": "invalid",
            "reason": f"event {event_index} is a {event.get('kind')} event",
        }
    pre = event.get("pre_image")
    post = event.get("post_image")
    if not isinstance(pre, dict) or not isinstance(post, dict):
        return {
            "status": "missing_pre_post",
            "reason": f"event {event_index} has no GL pre/post pair",
        }
    width = int(post.get("width") or pre.get("width") or 0)
    height = int(post.get("height") or pre.get("height") or 0)
    gl_pre_path = frame_dir / "blobs" / str(pre.get("file") or "")
    gl_post_path = frame_dir / "blobs" / str(post.get("file") or "")
    metal_pre_path = candidate / f"isolated-pre-{event_index}.bgra"
    metal_post_path = candidate / f"isolated-post-{event_index}.bgra"
    missing = [
        str(path)
        for path in (
            gl_pre_path,
            gl_post_path,
            metal_pre_path,
            metal_post_path,
        )
        if not path.is_file()
    ]
    if missing:
        return {
            "status": "missing",
            "reason": "missing image files",
            "missing": missing,
            "event": {
                "index": event_index,
                "caller": (event.get("draw") or {}).get("caller_rva"),
            },
        }
    gl_pre = gl_pre_path.read_bytes()
    gl_post = gl_post_path.read_bytes()
    metal_pre = metal_pre_path.read_bytes()
    metal_post = metal_post_path.read_bytes()
    if width <= 0 or height <= 0:
        return {"status": "invalid", "reason": "non-positive image dimensions"}
    size = width * height * 4
    for name, data in (
        ("gl_pre", gl_pre),
        ("gl_post", gl_post),
        ("metal_pre", metal_pre),
        ("metal_post", metal_post),
    ):
        if len(data) != size:
            return {
                "status": "invalid",
                "reason": f"{name} size {len(data)} does not match {size}",
            }
    replay_path = candidate / "replay.json"
    replay = {}
    if replay_path.is_file():
        try:
            replay = json.loads(replay_path.read_text())
        except (OSError, json.JSONDecodeError):
            replay = {}
    isolated = replay.get("isolated") or {}
    replay_valid = bool(
        replay.get("status") == "ok"
        and replay.get("failed") == 0
        and replay.get("command_status") == 4
        and isolated.get("event") == event_index
        and isolated.get("pre_image_supplied") is True
        and isolated.get("pre_capture_ok") is True
        and isolated.get("color_initialization") == "supplied_pre_image"
        and isolated.get("depth_contents") in ("not_required", "replayed_prefix")
    )
    gl_change = m6_compare.compare_images(gl_pre, gl_post, width, height)
    metal_change = m6_compare.compare_images(metal_pre, metal_post, width, height)
    pre_match = gl_pre == metal_pre
    post_match = gl_post == metal_post
    post_difference = m6_compare.compare_images(
        gl_post, metal_post, width, height
    )
    target = event.get("target") or {}
    attachment = target.get("color_attachment") or {}
    report = {
        "schema": "gfx-isolated-event-differential/v1",
        "frame": str(frame_dir),
        "candidate": str(candidate),
        "status": "compared",
        "event": {
            "index": event_index,
            "caller": (event.get("draw") or {}).get("caller_rva"),
            "program": (event.get("program") or {}).get("name"),
            "draw": event.get("draw") or {},
            "target": {
                "framebuffer": target.get("framebuffer"),
                "name": attachment.get("name"),
                "internal_format": attachment.get("internal_format"),
                "width": width,
                "height": height,
            },
            "pre_image": {
                "gl_sha256": _bytes_sha256(gl_pre),
                "metal_sha256": _bytes_sha256(metal_pre),
                "sha256": pre.get("sha256"),
                "match": pre_match,
                "bytes": len(gl_pre),
            },
            "post_image": {
                "gl_sha256": _bytes_sha256(gl_post),
                "metal_sha256": _bytes_sha256(metal_post),
                "sha256": post.get("sha256"),
            },
            "changed_pixels": {
                "gl": gl_change["mismatched_pixels"],
                "metal": metal_change["mismatched_pixels"],
            },
            "gl_pre_to_post": gl_change,
            "metal_pre_to_post": metal_change,
            "post_difference": post_difference,
            "pre_match": pre_match,
            "post_match": post_match,
            "exact_isolated_replay": bool(isolated.get("exact")),
            "replay_valid": replay_valid,
            "depth": {
                "matters": isolated.get("depth_matters"),
                "contents": isolated.get("depth_contents"),
            },
            "color_initialization": isolated.get("color_initialization"),
        },
        "ok": replay_valid and pre_match and post_match,
    }
    (candidate / f"isolated-event-{event_index}.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    return report


def isolate_command(args: argparse.Namespace) -> int:
    """Compares isolated replay output with authentic GL pre/post images."""
    from automation import m6_compare

    frame_dir = Path(args.frame).expanduser().resolve()
    candidate = Path(args.candidate).expanduser().resolve()
    if args.event is not None:
        report = selected_event_report(frame_dir, candidate, args.event)
        print(json.dumps(report, indent=2))
        return 0 if report.get("ok") else 2
    try:
        trace = load_trace(frame_dir)
    except FrameError as exc:
        print(json.dumps({"status": "invalid", "reason": str(exc)}, indent=2))
        return 1
    results = []
    for index, event in enumerate(trace.get("_events") or []):
        if event.get("kind") != "draw":
            continue
        post = event.get("post_image")
        pre = event.get("pre_image")
        if not isinstance(post, dict) or not isinstance(pre, dict):
            continue
        replay_path = candidate / f"isolated-post-{index}.bgra"
        gl_path = frame_dir / "blobs" / str(post.get("file") or "")
        if not replay_path.is_file() or not gl_path.is_file():
            results.append(
                {
                    "event": index,
                    "caller": (event.get("draw") or {}).get("caller_rva"),
                    "status": "missing",
                }
            )
            continue
        width = int(post.get("width") or 0)
        height = int(post.get("height") or 0)
        reference = gl_path.read_bytes()
        candidate_bytes = replay_path.read_bytes()
        if width <= 0 or height <= 0 or len(reference) != width * height * 4:
            results.append({"event": index, "status": "invalid_reference"})
            continue
        if len(candidate_bytes) != len(reference):
            results.append({"event": index, "status": "size_mismatch"})
            continue
        statistics = m6_compare.compare_images(
            reference, candidate_bytes, width, height
        )
        results.append(
            {
                "event": index,
                "caller": (event.get("draw") or {}).get("caller_rva"),
                "program": (event.get("program") or {}).get("name"),
                "textures": [
                    item.get("name") for item in event.get("textures") or []
                ],
                "status": "compared",
                "statistics": statistics,
            }
        )
    report = {
        "schema": "gfx-isolated-draw-differential/v1",
        "frame": str(frame_dir),
        "candidate": str(candidate),
        "draws": results,
        "ok": bool(results)
        and all(
            item.get("status") == "compared"
            and item["statistics"]["mismatched_pixels"] == 0
            for item in results
        ),
    }
    (candidate / "isolated-differential.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps({
        "ok": report["ok"],
        "draws": [
            {
                "event": item["event"],
                "caller": item.get("caller"),
                "program": item.get("program"),
                "status": item.get("status"),
                "mismatched": (item.get("statistics") or {}).get("mismatched_pixels"),
                "pixels": (item.get("statistics") or {}).get("pixels"),
                "max": (item.get("statistics") or {}).get("max_channel_error"),
            }
            for item in results
        ],
    }, indent=2))
    return 0 if report["ok"] else 2


# ---------------------------------------------------------------------------
# In-process shadow coordinator
# ---------------------------------------------------------------------------


def shadow_failure_detail(report: dict) -> str:
    """Return the native shadow failure without degrading it to a generic text.

    The bridge keeps the native stage and error in both the top-level report and
    ``metal``; replay.json additionally carries a structured ``failure`` object.
    Prefer those over any coordinator fallback so a specific manifest, shader,
    event or GPU failure survives into coordinator.json and capture-report.json.
    """
    if not isinstance(report, dict):
        return "Metal replay failed"
    metal = report.get("metal")
    if not isinstance(metal, dict):
        metal = {}
    failure = report.get("failure")
    if not isinstance(failure, dict):
        failure = {}
    error = report.get("error")
    stage = failure.get("stage") or metal.get("stage") or report.get("stage")
    if not error or (stage and not str(error).startswith(str(stage))):
        error = metal.get("error") or failure.get("error") or error
    if not error:
        error = "Metal replay failed"
    error = str(error)
    if stage and not error.startswith(str(stage)):
        return f"{stage}: {error}"
    return error


_CONTINUOUS_RENDERING_DIFFERENCES = {
    "qualified_difference", "difference_with_partial_state_coverage",
}
_CONTINUOUS_TERMINAL_CLASSIFICATIONS = {
    "gpu_execution_failure", "invalid_provenance", "incomplete_capture",
    "incomplete_diagnostics",
}


def _continuous_should_continue(classification: str | None,
                                    history_status,
                                    continue_on_difference: bool) -> bool:
    """Single continuation predicate for the native decision and supervisor."""
    if history_status not in (1, None):
        return False
    if classification in _CONTINUOUS_TERMINAL_CLASSIFICATIONS:
        return False
    if classification == "qualified_match":
        return True
    if classification in _CONTINUOUS_RENDERING_DIFFERENCES:
        return bool(continue_on_difference)
    return False


def _continuous_terminal_failure(report: dict) -> dict | None:
    """Return a terminal live-campaign failure, if one exists."""
    rows = [row for row in report.get("frames") or []
            if isinstance(row, dict)]
    for row in rows:
        if row.get("history") not in (1, None):
            return {
                "kind": "resource_history_failure",
                "frame": row.get("sequence"),
                "detail": f"history={row.get('history')}",
            }
        classification = row.get("classification")
        if classification in _CONTINUOUS_TERMINAL_CLASSIFICATIONS:
            return {
                "kind": str(classification),
                "frame": row.get("sequence"),
                "detail": str(classification),
            }
    if report.get("status") not in ("ok", "running", None):
        return {
            "kind": str(report.get("stop_reason") or report.get("status")),
            "frame": report.get("stopped_at"),
            "detail": str(report.get("error") or report.get("stop_reason")
                          or report.get("status")),
        }
    stop_reason = report.get("stop_reason")
    if stop_reason in ("tool_failure", "gpu_execution_failure",
                       "history_invalid", "host_failure", "frame_timeout"):
        return {
            "kind": str(stop_reason),
            "frame": report.get("stopped_at"),
            "detail": str(report.get("error") or stop_reason),
        }
    return None


def apply_shadow_report(result: dict, shadow_report: dict) -> str | None:
    """Copy the coordinator report into the final capture summary.

    Returns the native failure text when the shadow run failed, otherwise None.
    The capture summary therefore carries the same stage, error and structured
    failure object as shadow-report.json and coordinator.json.
    """
    result["shadow"] = shadow_report
    result["shadow_stage"] = shadow_report.get("stage")
    result["shadow_timed_out"] = bool(shadow_report.get("timed_out", False))
    if shadow_report.get("mode") == "continuous":
        frames = list(shadow_report.get("frames") or [])
        last = frames[-1] if frames else {}
        terminal = _continuous_terminal_failure(shadow_report)
        campaign_ok = shadow_report.get("status") == "ok" and terminal is None
        result["continuous"] = shadow_report
        result["pixel_verdict"] = last.get("classification")
        result["campaign_execution"] = (
            "ok" if campaign_ok else
            "failed" if shadow_report.get("status") == "failed" else
            "incomplete")
        result["terminal_failure"] = terminal
        result["classification"] = (
            terminal.get("kind") if terminal else last.get("classification"))
        result["first_divergence"] = last.get("first_divergence")
        result["qualified"] = bool(
            campaign_ok and frames
            and all(item.get("classification") == "qualified_match"
                    and item.get("history") in (1, None) for item in frames))
        result["diagnostic_complete"] = bool(
            campaign_ok and frames
            and all(item.get("diagnostic_report") for item in frames))
        result["case"] = shadow_report.get("case")
        result["reproduction"] = shadow_report.get("reproduction")
        result["reproduction_complete"] = bool(
            isinstance(result["reproduction"], dict)
            and result["reproduction"].get("independently_reproducible"))
        result["diagnostic"] = {
            "mode": "continuous",
            "frames": len(frames),
            "stop_reason": shadow_report.get("stop_reason"),
            "last_classification": last.get("classification"),
            "diagnostic_reports": [item.get("diagnostic_report")
                                   for item in frames],
        }
        if shadow_report.get("status") == "ok" and terminal is None:
            return None
        return shadow_failure_detail(shadow_report)
    failure = shadow_report.get("failure")
    if isinstance(failure, dict) and failure:
        result["shadow_failure"] = failure
    result["pixel_verdict"] = shadow_report.get("pixel_verdict")
    result["qualified"] = bool(shadow_report.get("qualified"))
    result["classification"] = shadow_report.get("classification")
    result["first_divergence"] = shadow_report.get("first_divergence")
    result["checkpoint_validation"] = shadow_report.get(
        "checkpoint_validation")
    result["state_coverage"] = shadow_report.get("state_coverage")
    result["diagnostic"] = {
        "metal": "complete" if shadow_report.get("status") == "ok" else "failed",
        "pixel": shadow_report.get("pixel_verdict"),
        "classification": shadow_report.get("classification"),
        "axes": (shadow_report.get("diagnosis") or {}).get("axes"),
        "diagnostic_qualified": shadow_report.get("diagnostic_qualified"),
        "first_divergence": shadow_report.get("first_divergence"),
        "checkpoint_validation": shadow_report.get("checkpoint_validation"),
        "diagnostic_report": (shadow_report.get("diagnosis") or {}).get(
            "report"),
    }
    if shadow_report.get("status") == "ok":
        return None
    return shadow_failure_detail(shadow_report)


def shadow_coordinator(args: argparse.Namespace) -> int:
    """Coordinates the one external translator step for a sealed live frame.

    This process never launches EU4.  It waits for the PR 1 seal, runs the
    existing shader translator into the shadow directory, publishes the ready
    marker the in-process bridge waits for, then waits for the Metal report and
    runs the existing differential comparison.
    """
    if getattr(args, "continuous", False):
        return shadow_coordinator_continuous(args)
    run = Path(args.run).expanduser().resolve()
    shadow = (
        Path(args.shadow_dir).expanduser().resolve()
        if args.shadow_dir
        else run / "shadow"
    )
    if args.timeout <= 0 or args.seal_timeout <= 0 or             args.translation_timeout <= 0 or args.replay_timeout <= 0 or             args.compare_timeout <= 0:
        shadow.mkdir(parents=True, exist_ok=True)
        (shadow / "coordinator.json").write_text(
            json.dumps(
                {"schema": "m7-shadow-coordinator/v1",
                 "run": str(run), "shadow_dir": str(shadow),
                 "capture_id": getattr(args, "capture_id", "") or "",
                 "status": "failed", "error": "invalid phase budget"},
                indent=2,
            )
            + "\n"
        )
        return 1
    frame_dir = run / "frame"
    shader_dir = shadow / "shaders"
    report_path = shadow / "shadow-report.json"
    result: dict = {
        "schema": "m7-shadow-coordinator/v1",
        "run": str(run),
        "shadow_dir": str(shadow),
        "capture_id": getattr(args, "capture_id", "") or "",
        "status": "incomplete",
    }
    overall_deadline = time.monotonic() + args.timeout
    seal_deadline = min(overall_deadline, time.monotonic() + args.seal_timeout)
    seal_path = frame_dir / "frame-seal.json"
    while not seal_path.is_file() and time.monotonic() < seal_deadline:
        time.sleep(0.1)
    if not seal_path.is_file():
        result["status"] = "failed"
        result["error"] = "frame seal did not appear"
        (shadow / "coordinator.json").parent.mkdir(parents=True, exist_ok=True)
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    try:
        seal = load_seal(frame_dir)
    except FrameError as exc:
        result["status"] = "failed"
        result["error"] = f"frame seal is not usable: {exc}"
        (shadow / "coordinator.json").parent.mkdir(parents=True, exist_ok=True)
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    result["seal"] = {
        "capture_frame_id": seal.get("capture_frame_id"),
        "pid": seal.get("pid"),
        "operation_count": seal.get("operation_count"),
        "reference_sha256": seal.get("reference_sha256"),
    }
    shadow.mkdir(parents=True, exist_ok=True)
    shader_dir.mkdir(parents=True, exist_ok=True)
    for stale in (
        shader_dir / "ready",
        shadow / "shadow-report.json",
        shadow / "replay.json",
        shadow / "comparison.json",
    ):
        if stale.exists():
            result["status"] = "failed"
            result["error"] = f"stale shadow artifact: {stale}"
            (shadow / "coordinator.json").write_text(
                json.dumps(result, indent=2) + "\n"
            )
            return 1
    try:
        translated = subprocess.run(
            [
                sys.executable,
                "-m",
                "automation.m6_frame",
                "translate",
                "--frame",
                str(frame_dir),
                "--out",
                str(shader_dir),
                "--force",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=min(
                args.translation_timeout,
                max(0.0, overall_deadline - time.monotonic()),
            ),
            check=False,
        )
    except subprocess.TimeoutExpired:
        result["status"] = "failed"
        result["error"] = "shader translation timed out"
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    if translated.returncode != 0:
        result["status"] = "failed"
        result["error"] = "shader translation failed"
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    # Build the dependency graph and publish the checkpoint request before the
    # ready marker.  The bridge binds this request's digest into the receipt,
    # so a stale or mismatched plan cannot be executed silently.
    try:
        from automation import gfx_frame_graph

        graph = gfx_frame_graph.build(frame_dir)
        if graph.get("status") == "invalid":
            raise FrameError(
                "frame graph is invalid: "
                + "; ".join(graph.get("reasons") or ["unknown"]))
        request_path = shadow / "checkpoint-request.json"
        request = gfx_frame_graph.write_checkpoint_request(graph, request_path)
        request_sha256 = _sha256(request_path)
    except (FrameError, OSError, ValueError) as exc:
        result["status"] = "failed"
        result["error"] = f"checkpoint request preparation failed: {exc}"
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    result["checkpoint_request"] = {
        "file": request_path.name,
        "sha256": request_sha256,
        "count": len(request.get("checkpoints") or []),
        "source": str(request_path),
        "graph_status": graph.get("status"),
    }
    try:
        identity = shader_receipt_identity(shader_dir, required=True)
    except FrameError as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        (shadow / "coordinator.json").write_text(
            json.dumps(result, indent=2) + "\n")
        return 1
    receipt = build_shader_receipt(seal, request_path)
    receipt.update(identity)
    receipt_path = shadow / "shader-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    (shader_dir / "ready").write_text(
        json.dumps(
            {
                "status": "ready",
                "capture_frame_id": seal.get("capture_frame_id"),
                "shader_receipt_sha256": _sha256(receipt_path),
            }
        )
        + "\n"
    )
    # Translation time does not consume the in-process replay budget.
    report_deadline = min(
        overall_deadline, time.monotonic() + args.replay_timeout
    )
    while not report_path.is_file() and time.monotonic() < report_deadline:
        time.sleep(0.1)
    if not report_path.is_file():
        result["status"] = "failed"
        result["error"] = "in-process Metal replay report did not appear"
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    try:
        report = _read_json(report_path)
    except FrameError as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    result["metal"] = report
    result["stage"] = report.get("stage")
    result["timed_out"] = bool(report.get("timed_out", False))
    if isinstance(report.get("failure"), dict) and report.get("failure"):
        result["failure"] = report.get("failure")
    provenance_error = None
    for report_field, seal_field in (
        ("capture_frame_id", "capture_frame_id"),
        ("pid", "pid"),
        ("gl_reference_sha256", "reference_sha256"),
        ("trace_manifest_sha256", "manifest_sha256"),
        ("operation_count", "operation_count"),
    ):
        if report.get(report_field) != seal.get(seal_field):
            provenance_error = (
                f"shadow report {report_field} does not match the sealed frame"
            )
            break
    execution_failed = report.get("status") != "ok"
    result["provenance_ok"] = provenance_error is None
    result["execution_failed"] = execution_failed
    if provenance_error is not None:
        result["status"] = "failed"
        result["provenance_error"] = provenance_error
        if execution_failed:
            # Keep the native execution failure as the actionable error while
            # recording the independent identity mismatch.
            result["error"] = shadow_failure_detail(report)
        else:
            result["error"] = provenance_error
        (shadow / "coordinator.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        return 1
    if execution_failed:
        result["status"] = "failed"
        result["error"] = shadow_failure_detail(report)
        (shadow / "coordinator.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        return 1
    if report.get("checkpoint_request_sha256") != request_sha256:
        result["status"] = "failed"
        result["provenance_error"] = (
            "shadow report checkpoint request digest does not match the "
            "published request")
        result["provenance_ok"] = False
        result["error"] = result["provenance_error"]
        (shadow / "coordinator.json").write_text(
            json.dumps(result, indent=2) + "\n")
        return 1
    if report.get("checkpoint_request_file") != request_path.name:
        result["status"] = "failed"
        result["provenance_error"] = (
            "shadow report checkpoint request file does not match the "
            "published request")
        result["provenance_ok"] = False
        result["error"] = result["provenance_error"]
        (shadow / "coordinator.json").write_text(
            json.dumps(result, indent=2) + "\n")
        return 1
    policy_path = (
        Path(args.policy).expanduser().resolve()
        if args.policy
        else ROOT / "analysis" / "m6" / "comparison-policy.json"
    )
    try:
        from automation import gfx_independent_replay

        class _DiagnosisTimeout(Exception):
            pass

        def _diagnosis_alarm(signum, frame):
            raise _DiagnosisTimeout(
                f"diagnosis exceeded {args.compare_timeout}s")

        previous = signal.signal(signal.SIGALRM, _diagnosis_alarm)
        diagnosis_budget = min(
            args.compare_timeout,
            max(0.0, overall_deadline - time.monotonic()),
        )
        if diagnosis_budget <= 0:
            raise _DiagnosisTimeout("overall coordinator deadline expired")
        signal.setitimer(signal.ITIMER_REAL, diagnosis_budget)
        try:
            diagnosis = gfx_independent_replay.diagnose_frame(
                frame_dir, shadow, shadow, policy_path)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    except (OSError, ValueError, Exception) as exc:  # noqa: BLE001
        result["status"] = "failed"
        result["error"] = f"diagnosis failed: {exc}"
        (shadow / "coordinator.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        return 1
    qualification = diagnosis.get("qualification") or {}
    final = (diagnosis.get("pixels") or {}).get("final") or {}
    comparison = final.get("detail")
    result["diagnosis"] = {
        "schema": diagnosis.get("schema"),
        "classification": diagnosis.get("classification"),
        "axes": qualification.get("axes"),
        "qualified": qualification.get("qualified"),
        "diagnostic_qualified": qualification.get("diagnostic_qualified"),
        "first_divergence": diagnosis.get("first_divergence"),
        "checkpoint_validation": diagnosis.get("checkpoint_validation"),
        "pixels": final.get("statistics"),
        "report": str(shadow / "diagnostic-report.json"),
    }
    result["classification"] = diagnosis.get("classification")
    result["qualified"] = bool(qualification.get("qualified"))
    result["diagnostic_qualified"] = bool(
        qualification.get("diagnostic_qualified"))
    result["first_divergence"] = diagnosis.get("first_divergence")
    result["checkpoint_validation"] = diagnosis.get("checkpoint_validation")
    result["state_coverage"] = diagnosis.get("state_coverage")
    (shadow / "state-coverage.json").write_text(
        json.dumps(diagnosis.get("state_coverage"), indent=2) + "\n")
    result["execution"] = {
        "capture": "complete",
        "metal": (diagnosis.get("execution") or {}).get("status", "failed"),
        "operation_coverage": (
            diagnosis.get("operation_coverage") or {}).get("status", "invalid"),
    }
    if isinstance(comparison, dict):
        result["comparison"] = comparison
        result["pixel_verdict"] = comparison.get("status")
        (shadow / "comparison.json").write_text(
            json.dumps(comparison, indent=2) + "\n")
    else:
        result["pixel_verdict"] = "unavailable"
        result["comparison_error"] = final.get("reason") or (
            "the shared comparison engine produced no report")
    result["status"] = "ok"
    (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


def diagnose_command(args: argparse.Namespace) -> int:
    """Diagnose an already-produced in-process Metal result offline."""
    from automation import gfx_independent_replay

    frame_dir = Path(args.frame).expanduser().resolve()
    metal_dir = Path(args.metal).expanduser().resolve()
    out = (Path(args.out).expanduser().resolve()
           if args.out else metal_dir)
    try:
        report = gfx_independent_replay.diagnose_frame(
            frame_dir, metal_dir, out, args.policy,
            package=bool(args.package),
            shaders=(Path(args.shaders).expanduser().resolve()
                     if args.shaders else None))
    except (gfx_independent_replay.ReplayError, FrameError, OSError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    print(json.dumps({
        "schema": report.get("schema"),
        "status": report.get("status"),
        "classification": report.get("classification"),
        "qualification": report.get("qualification", {}).get("axes"),
        "first_divergence": {
            "kind": (report.get("first_divergence") or {}).get("kind"),
            "event": (report.get("first_divergence") or {}).get("event"),
            "confidence": (report.get("first_divergence") or {}).get("confidence"),
        },
        "checkpoint_validation": report.get("checkpoint_validation"),
        "report": str(out / "diagnostic-report.json"),
    }, indent=2))
    return gfx_independent_replay.diagnosis_exit_code(report)



def _wait_for_path(path: Path, deadline: float) -> bool:
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        time.sleep(0.1)
    return path.is_file()


def artifact_digest(directory: Path) -> tuple[str, int]:
    """Canonical digest of a translated shader directory.

    Every regular payload is ordered by relative path and hashed as
    ``relative-path \\0 sha256 \\n``.  The publish markers ``ready`` and
    ``shader-receipt.json`` are bookkeeping, not shader artifacts, so they are
    excluded.  The same contract is used by the live receipt writer, the
    sequence checkpoint setup and portable-case verification.
    """
    digest = hashlib.sha256()
    count = 0
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name in ("ready", "shader-receipt.json"):
            continue
        digest.update(str(path.relative_to(directory)).encode())
        digest.update(b"\0")
        digest.update(_sha256(path).encode())
        digest.update(b"\n")
        count += 1
    return digest.hexdigest(), count


def shader_receipt_identity(directory: Path | str | None, *,
                            required: bool = False) -> dict:
    """Canonical shader-report and shader-artifact identity for a receipt.

    This is the single owner of the three fields written into live receipts.
    ``required`` is used at live publication: a missing directory or
    ``shaders.json`` fails instead of yielding an empty identity that could be
    mistaken for a successful receipt.  Optional and legacy readers get the
    empty identity and keep their own missing-versus-tampered distinction.
    """
    empty = {"shaders_json_sha256": "", "artifacts_sha256": "",
             "artifact_count": 0}
    if directory is None:
        if required:
            raise FrameError("a translated shader directory is required")
        return empty
    root = Path(directory)
    report = root / "shaders.json"
    if not root.is_dir() or not report.is_file():
        if required:
            raise FrameError(
                f"translated shader report is missing: {report}")
        return empty
    artifacts_sha256, artifact_count = artifact_digest(root)
    return {
        "shaders_json_sha256": _sha256(report),
        "artifacts_sha256": artifacts_sha256,
        "artifact_count": artifact_count,
    }


def build_shader_receipt(seal: dict, request_path: Path | str,
                         shader_dir: Path | str | None = None,
                         *, required_identity: bool = False) -> dict:
    """Build the one shader-receipt representation used by every replay path.

    The receipt binds the checkpoint request to the sealed frame; when a
    translated shader directory is supplied its canonical identity is added.
    It is metadata about an execution, never a substitute for the independently
    verified result.
    """
    request_path = Path(request_path)
    receipt = {
        "schema": "gfx-shader-receipt/v1",
        "capture_frame_id": seal.get("capture_frame_id"),
        "pid": seal.get("pid"),
        "session_id": seal.get("session_id"),
        "trace_manifest_sha256": seal.get("manifest_sha256"),
        "event_stream_sha256": seal.get("event_stream_sha256"),
        "event_stream_bytes": seal.get("event_stream_bytes"),
    }
    if shader_dir is not None:
        receipt.update(shader_receipt_identity(
            shader_dir, required=required_identity))
    receipt.update({
        "checkpoint_request_file": request_path.name,
        "checkpoint_request_sha256": _sha256(request_path),
        "checkpoint_count": len(_read_json(request_path).get("checkpoints")
                                or []),
    })
    return receipt


def _continuous_frame_coordinator(
    run: Path, shadow: Path, frame_index: int, args,
    overall_deadline: float,
) -> tuple[dict, dict | None]:
    """Prepare, verify and diagnose one continuous frame.

    Returns ``(summary, diagnosis)``.  A failed preparation raises FrameError;
    the caller records it without conflating it with a rendering difference.
    """
    from automation import gfx_frame_graph, gfx_independent_replay

    frame_dir = run / "frames" / f"frame-{frame_index:06d}"
    output = shadow / f"frame-{frame_index:06d}"
    shader_dir = output / "shaders"
    output.mkdir(parents=True, exist_ok=True)
    shader_dir.mkdir(parents=True, exist_ok=True)
    seal_path = frame_dir / "frame-seal.json"
    seal_deadline = min(overall_deadline,
                        time.monotonic() + args.seal_timeout)
    if not _wait_for_path(seal_path, seal_deadline):
        raise FrameError(f"frame {frame_index} seal did not appear")
    seal = load_seal(frame_dir)
    cache_dir = shadow / "translation-cache"
    translated = subprocess.run(
        [sys.executable, "-m", "automation.m6_frame", "translate",
         "--frame", str(frame_dir), "--out", str(shader_dir),
         "--cache-dir", str(cache_dir)],
        cwd=str(ROOT), capture_output=True, text=True,
        timeout=max(0.0, min(args.translation_timeout,
                             overall_deadline - time.monotonic())),
        check=False)
    if translated.returncode != 0:
        raise FrameError(f"frame {frame_index} shader translation failed")
    graph = gfx_frame_graph.build(frame_dir)
    if graph.get("status") == "invalid":
        raise FrameError(
            f"frame {frame_index} graph is invalid: "
            + "; ".join(graph.get("reasons") or ["unknown"]))
    request_path = output / "checkpoint-request.json"
    request = gfx_frame_graph.write_checkpoint_request(graph, request_path)
    request_sha256 = _sha256(request_path)
    identity = shader_receipt_identity(shader_dir, required=True)
    receipt = build_shader_receipt(seal, request_path)
    receipt.update(identity)
    receipt_path = output / "shader-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    (shader_dir / "ready").write_text(json.dumps({
        "status": "ready",
        "capture_frame_id": seal.get("capture_frame_id"),
        "shader_receipt_sha256": _sha256(receipt_path),
        "sequence_index": frame_index,
    }) + "\n")
    report_path = output / "shadow-report.json"
    replay_deadline = min(overall_deadline,
                          time.monotonic() + args.replay_timeout)
    if not _wait_for_path(report_path, replay_deadline):
        raise FrameError(
            f"frame {frame_index} in-process Metal report did not appear")
    report = _read_json(report_path)
    if report.get("checkpoint_request_sha256") != request_sha256:
        raise FrameError(
            f"frame {frame_index} checkpoint request digest mismatch")
    for report_field, seal_field in (
        ("capture_frame_id", "capture_frame_id"),
        ("pid", "pid"),
        ("gl_reference_sha256", "reference_sha256"),
        ("trace_manifest_sha256", "manifest_sha256"),
        ("operation_count", "operation_count"),
    ):
        if report.get(report_field) != seal.get(seal_field):
            raise FrameError(
                f"frame {frame_index} shadow report {report_field} does not "
                "match the seal")
    if report.get("status") != "ok":
        raise FrameError(
            f"frame {frame_index} Metal replay failed: "
            + shadow_failure_detail(report))
    diagnosis = gfx_independent_replay.diagnose_frame(
        frame_dir, output, output, args.policy,
        shaders=shader_dir)
    classification = diagnosis.get("classification")
    history_status = report.get("history_status")
    should_continue = _continuous_should_continue(
        classification, history_status, args.continue_on_difference)
    temp = output / "decision.json.tmp"
    temp.write_text(json.dumps({
        "continue": should_continue,
        "classification": classification,
        "sequence": frame_index,
    }) + "\n")
    os.replace(temp, output / "decision.json")
    qualification = diagnosis.get("qualification") or {}
    summary = {
        "sequence": frame_index,
        "capture_frame_id": seal.get("capture_frame_id"),
        "present_index": seal.get("present_index"),
        "scene_index": seal.get("scene_index"),
        "frame_dir": str(frame_dir),
        "output_dir": str(output),
        "operation_count": seal.get("operation_count"),
        "classification": diagnosis.get("classification"),
        "axes": qualification.get("axes"),
        "first_divergence": diagnosis.get("first_divergence"),
        "pixels": (diagnosis.get("pixels") or {}).get("statistics"),
        "state_coverage": (diagnosis.get("state_coverage") or {}).get("status"),
        "checkpoint_validation": diagnosis.get("checkpoint_validation"),
        "history": report.get("history_status"),
        "carried_bindings": report.get("carried_bindings"),
        "missing_history": report.get("missing_history"),
        "stale_history": report.get("stale_history"),
        "async": bool(report.get("async", False)),
        "queue_high_water": report.get("queue_high_water"),
        "queue_depth": report.get("queue_depth"),
        "diagnostic_report": str(output / "diagnostic-report.json"),
    }
    return summary, diagnosis


def _package_continuous_case(run: Path, shadow: Path, count: int,
                             args: argparse.Namespace, *,
                             scope: str = "target_frame",
                             terminal_failure: dict | None = None) -> dict:
    """Package the diagnosed ordered prefix from the live coordinator.

    The live layout keeps each frame's capture under ``run/frames`` and that
    frame's exact translation under ``shadow/frame-NNNNNN/shaders``.  Both the
    per-frame shader mapping and the published checkpoint request/receipt are
    passed to the existing v2 packager, so predecessor history, lifecycle
    records, capture identity and the target diagnosis remain authoritative.
    """
    if count < 1:
        return {"status": "unavailable", "reason": "no diagnosed frame"}
    from automation import gfx_replay_case

    frames = [run / "frames" / f"frame-{index:06d}"
              for index in range(1, count + 1)]
    last_output = shadow / f"frame-{count:06d}"
    diagnosis = _read_json(last_output / "diagnostic-report.json") if (
        last_output / "diagnostic-report.json").is_file() else {}
    if not diagnosis:
        return {"status": "incomplete", "reason": "target diagnosis missing"}
    frame_shaders = {
        str(frame): shadow / f"frame-{index:06d}" / "shaders"
        for index, frame in enumerate(frames, start=1)
    }
    checkpoint_sources = [
        shadow / f"frame-{index:06d}" for index in range(1, count + 1)]
    case_dir = (Path(getattr(args, "case_dir", "") or "").expanduser().resolve()
                if getattr(args, "case_dir", "") else shadow / "case")
    max_bytes = int(getattr(args, "case_max_bytes", 0) or 0)
    if max_bytes <= 0:
        max_bytes = 512 * 1024 * 1024
    try:
        manifest = gfx_replay_case.package_sequence_diagnosis(
            frames, last_output, case_dir, diagnosis,
            shaders=last_output / "shaders",
            frame_shaders=frame_shaders,
            checkpoint_sources=checkpoint_sources,
            case_id=f"m7-live-frame-{count:06d}",
            max_bytes=max_bytes,
        )
    except (gfx_replay_case.CaseError, OSError, ValueError,
            TypeError, KeyError) as exc:
        return {
            "status": "failed",
            "reason": str(exc),
            "case_dir": str(case_dir),
        }
    reproduction = manifest.get("reproduction") or {}
    if (not reproduction
            and manifest.get("schema") == gfx_replay_case.SCHEMA):
        # A complete single-frame package is the v1 schema, which has no
        # ``reproduction`` member.  Normalise it instead of mislabelling a
        # verified package as incomplete.
        reproduction = {
            "status": "complete",
            "independently_reproducible": True,
            "kind": "single_frame_v1",
        }
    # Label the published case itself, not only the coordinator summary, so a
    # completed-prefix reproduction cannot be mistaken for the terminal frame.
    case_manifest_path = case_dir / "case.json"
    try:
        case_manifest = _read_json(case_manifest_path)
        case_manifest["reproduction_scope"] = scope
        case_manifest["terminal_failure"] = terminal_failure
        case_manifest_path.write_text(
            json.dumps(case_manifest, indent=2) + "\n")
    except (FrameError, OSError, ValueError, json.JSONDecodeError):
        pass
    return {
        "status": ("complete" if reproduction.get(
            "independently_reproducible") else "incomplete"),
        "scope": scope,
        "terminal_failure": terminal_failure,
        "case_id": manifest.get("case_id"),
        "case_dir": str(case_dir),
        "files": len(manifest.get("files") or []),
        "bytes": sum(item.get("bytes", 0)
                     for item in manifest.get("files") or []),
        "reproduction": reproduction,
        "replay_command": manifest.get("replay_command"),
        "verify_command": manifest.get("verify_command"),
    }


def _continuous_summary_terminal(summary: dict) -> dict | None:
    """Terminal failure from one live frame summary, if any."""
    if summary.get("history") not in (1, None):
        return {
            "kind": "resource_history_failure",
            "frame": summary.get("sequence"),
            "detail": f"history={summary.get('history')}",
        }
    classification = summary.get("classification")
    if classification in _CONTINUOUS_TERMINAL_CLASSIFICATIONS:
        return {
            "kind": str(classification),
            "frame": summary.get("sequence"),
            "detail": str(classification),
        }
    return None


def shadow_coordinator_continuous(args: argparse.Namespace) -> int:
    """Coordinate a bounded ordered sequence through one persistent session."""
    run = Path(args.run).expanduser().resolve()
    shadow = (Path(args.shadow_dir).expanduser().resolve()
              if args.shadow_dir else run / "shadow")
    shadow.mkdir(parents=True, exist_ok=True)
    result: dict = {
        "schema": "m7-shadow-coordinator/v1",
        "mode": "continuous",
        "run": str(run),
        "shadow_dir": str(shadow),
        "capture_id": getattr(args, "capture_id", "") or "",
        "max_frames": args.max_frames,
        "status": "running",
        "frames": [],
    }
    overall_deadline = time.monotonic() + args.timeout
    stop_reason = "frame_limit"
    for frame_index in range(1, args.max_frames + 1):
        try:
            summary, diagnosis = _continuous_frame_coordinator(
                run, shadow, frame_index, args, overall_deadline)
        except (FrameError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            # The bridge may be waiting for this frame's decision; release it
            # with an explicit stop before recording the tool failure.
            output = shadow / f"frame-{frame_index:06d}"
            try:
                output.mkdir(parents=True, exist_ok=True)
                (output / "decision.json").write_text(json.dumps({
                    "continue": False,
                    "reason": "tool_failure",
                    "sequence": frame_index,
                }) + "\n")
            except OSError:
                pass
            result["status"] = "failed"
            result["error"] = str(exc)
            result["stop_reason"] = "tool_failure"
            result["qualified"] = False
            result["diagnostic_complete"] = False
            result["terminal_failure"] = {
                "kind": "tool_failure", "frame": frame_index,
                "detail": str(exc),
            }
            if result["frames"]:
                result["case"] = _package_continuous_case(
                    run, shadow, len(result["frames"]), args,
                    scope="completed_prefix",
                    terminal_failure=result["terminal_failure"])
                result["reproduction"] = result["case"].get("reproduction")
            (shadow / "coordinator.json").write_text(
                json.dumps(result, indent=2) + "\n")
            return 1
        result["frames"].append(summary)
        classification = summary.get("classification")
        terminal = _continuous_summary_terminal(summary)
        if terminal is not None:
            result["status"] = "failed"
            result["qualified"] = False
            result["terminal_failure"] = terminal
            result["stop_reason"] = terminal["kind"]
            result["stopped_at"] = frame_index
            result["diagnostic_complete"] = False
            result["case"] = _package_continuous_case(
                run, shadow, max(0, frame_index - 1), args,
                scope="completed_prefix", terminal_failure=terminal)
            result["reproduction"] = result["case"].get("reproduction")
            (shadow / "coordinator.json").write_text(
                json.dumps(result, indent=2) + "\n")
            return 1
        continue_campaign = _continuous_should_continue(
            classification, summary.get("history"),
            args.continue_on_difference)
        if not continue_campaign:
            result["status"] = "ok"
            result["stop_reason"] = (
                "first_qualified_difference"
                if classification == "qualified_difference"
                else f"stop_{classification}")
            result["stopped_at"] = frame_index
            result["qualified"] = False
            result["diagnostic_complete"] = bool(
                result["frames"] and all(
                    item.get("diagnostic_report")
                    for item in result["frames"]))
            result["case"] = _package_continuous_case(
                run, shadow, frame_index, args, scope="target_frame")
            result["reproduction"] = result["case"].get("reproduction")
            (shadow / "coordinator.json").write_text(
                json.dumps(result, indent=2) + "\n")
            return 0
    result["status"] = "ok"
    result["stop_reason"] = "frame_limit"
    result["qualified"] = bool(
        result["frames"] and all(
            item.get("classification") == "qualified_match"
            and item.get("history") in (1, None)
            for item in result["frames"]))
    result["diagnostic_complete"] = bool(
        result["frames"] and all(
            item.get("diagnostic_report") for item in result["frames"]))
    result["case"] = _package_continuous_case(
        run, shadow, len(result["frames"]), args,
        scope="completed_sequence")
    result["reproduction"] = result["case"].get("reproduction")
    (shadow / "coordinator.json").write_text(
        json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def inspect(args: argparse.Namespace) -> int:
    frame_dir = Path(args.frame).expanduser().resolve()
    try:
        trace = load_trace(frame_dir)
        coverage = coverage_report(trace)
    except FrameError as exc:
        print(json.dumps({"status": "invalid", "reason": str(exc)}, indent=2))
        return 1
    summary = {
        "schema": "m6-frame-inspection/v1",
        "frame_dir": str(frame_dir),
        "manifest": {
            "present_index": trace.get("present_index"),
            "events": trace.get("events"),
            "frame": trace.get("frame"),
            "gl": trace.get("gl"),
        },
        "coverage": coverage,
        "draws": [
            {
                "index": (event.get("draw") or {}).get("entry"),
                "caller": (event.get("draw") or {}).get("caller_rva"),
                "mode": (event.get("draw") or {}).get("mode"),
                "count": (event.get("draw") or {}).get("count"),
                "program": (event.get("program") or {}).get("name"),
                "target": (event.get("target") or {}).get("framebuffer"),
            }
            for event in trace.get("_draws") or []
        ],
    }
    seal_path = frame_dir / "frame-seal.json"
    if seal_path.is_file():
        try:
            summary["seal"] = load_seal(frame_dir, trace=trace)
        except FrameError as exc:
            summary["seal"] = {"invalid": str(exc)}
    print(json.dumps(summary, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _uv2_probe_pixels(value: str) -> str:
    from automation import gfx_uv2_probe

    try:
        pixels = gfx_uv2_probe.sparse_pixels(value)
    except gfx_uv2_probe.ProbeError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return ";".join(f"{x},{y}" for x, y in pixels)


def _checkpoint_event_list(value: str) -> list[int]:
    """Parse ``14,20,71,142`` into an ordered event list.

    Ordering, range and count budgets are validated later against the capture
    options so the error names the conflicting option.
    """
    if not value:
        raise argparse.ArgumentTypeError("empty checkpoint event list")
    events: list[int] = []
    for token in value.split(","):
        if not token:
            raise argparse.ArgumentTypeError(
                "checkpoint events must be a comma-separated list of integers")
        try:
            events.append(int(token))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"invalid checkpoint event {token!r}") from exc
    return events


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="capture one complete original-GL frame")
    cap.add_argument("--output", required=True)
    cap.add_argument("--campaign-run-root")
    cap.add_argument("--callers", default="0x10d52fc,0x1486ad9")
    cap.add_argument("--min-draws", type=int, default=8)
    cap.add_argument("--max-frame-draws", type=int, default=0)
    cap.add_argument("--occurrence", type=int, default=1)
    cap.add_argument("--at-frame", type=int, default=None)
    cap.add_argument("--after-ms", type=int, default=0)
    cap.add_argument("--boundary-fb", type=int, default=-1)
    cap.add_argument("--boundary", default="")
    cap.add_argument("--pre-post-callers", default="")
    cap.add_argument("--log", action="store_true")
    cap.add_argument("--timeline", action="store_true")
    cap.add_argument("--copy-trace", type=int, default=None,
                     help="bounded full-copy trace for one destination texture")
    cap.add_argument("--copy-source", action="store_true",
                     help="capture the default-framebuffer pixels of in-frame copies")
    cap.add_argument(
        "--default-checkpoint-event", type=int, default=None,
        metavar="EVENT",
        help="capture one full default-framebuffer checkpoint immediately "
             "before this captured event index executes (opt-in; draw, clear "
             "and the recorded copy have pre-execution hooks, any other event "
             "fails closed)")
    cap.add_argument(
        "--default-checkpoint-events", type=_checkpoint_event_list,
        default=None, metavar="EVENTS",
        help="capture a bounded ordered set of default-framebuffer "
             "checkpoints, e.g. 14,20,71,142, immediately before each event "
             "executes; mutually exclusive with --default-checkpoint-event")
    cap.add_argument(
        "--max-checkpoints", type=int, default=4,
        help="maximum accepted default checkpoint count (native hard bound 8)")
    cap.add_argument(
        "--checkpoint-max-bytes", type=int, default=128 * 1024 * 1024,
        help="aggregate readback-byte budget for the whole checkpoint set")
    cap.add_argument(
        "--prefix-end", type=int, default=None, metavar="EVENT",
        help="opt-in exact-prefix mode: observe the post-event state of every "
             "operation from event 0 through EVENT (bounded by the byte/time "
             "budgets and the native hard bound)")
    cap.add_argument(
        "--observe-events", default=None, metavar="LIST",
        help="bounded post-event selection, e.g. 118,120,129; no initial-state "
             "anchor and never an exact-prefix claim")
    cap.add_argument(
        "--prefix-max-bytes", type=int, default=384 * 1024 * 1024,
        help="aggregate readback-byte budget for exact-prefix observations")
    cap.add_argument(
        "--prefix-max-ns", type=int, default=10_000_000_000,
        help="aggregate readback-time budget for exact-prefix observations")
    cap.add_argument("--seal", action="store_true",
                     help="publish the authoritative frame-seal/v1 handoff")
    cap.add_argument(
        "--uv2-probe", action="store_true",
        help="opt-in diagnostic: execute the selected event-1 draw twice in "
             "isolated offscreen targets (original-shader control + uv2 float "
             "probe) after the engine's own draw; requires --prefix-end")
    cap.add_argument("--uv2-probe-pixels", type=_uv2_probe_pixels, metavar="X,Y;X,Y",
                     help="use sparse fragment observation at lower-left coordinates")
    cap.add_argument("--uv2-probe-event", type=int, default=1)
    cap.add_argument("--uv2-probe-program", type=int, default=310)
    cap.add_argument("--uv2-probe-framebuffer", type=int, default=6)
    cap.add_argument("--uv2-probe-texture", type=int, default=433)
    cap.add_argument("--shadow", action="store_true",
                     help="execute the sealed frame through the in-process Metal bridge")
    cap.add_argument("--continuous", action="store_true",
                     help="capture an ordered bounded sequence from one process")
    cap.add_argument("--max-frames", type=int, default=3)
    cap.add_argument("--max-pending", type=int, default=4)
    cap.add_argument("--max-bytes", type=int, default=512 * 1024 * 1024)
    cap.add_argument("--case-max-bytes", type=int, default=512 * 1024 * 1024,
                     help="bounded reproduction-case budget for a continuous run")
    cap.add_argument("--case-dir", default="",
                     help="explicit reproduction case directory (continuous)")
    cap.add_argument("--continue-on-difference", action="store_true")
    cap.add_argument("--async", action="store_true",
                     help="use the bounded asynchronous Metal worker queue")
    cap.add_argument("--shadow-dir", default="",
                     help="shadow output directory (default: <output>/shadow)")
    cap.add_argument("--shadow-timeout-ms", type=int, default=120000)
    cap.add_argument("--shadow-wait-ms", type=int, default=180000)
    cap.add_argument("--seal-log", action="store_true",
                     help="compatibility flag; --seal enables logging for non-shadow captures")
    cap.add_argument("--seal-timeout-ms", type=int, default=600000,
                     help="bounded time from arming to a sealed/incomplete result")
    cap.add_argument("--background", action="store_true")
    cap.add_argument("--game-arg", action="append", default=[])
    cap.add_argument(
        "--max-draws", type=int, default=512,
        help="bounded captured-event budget; the default covers the authentic "
             "title scene (325 events) and prevents the former 256-event "
             "draw_cap failure")
    cap.add_argument("--duration", type=float, default=90.0)
    cap.add_argument("--timeout", type=float, default=180.0)
    cap.set_defaults(handler=capture)

    coord = sub.add_parser(
        "shadow-coordinator",
        help="translate shaders and compare one sealed frame's in-process Metal replay",
    )
    coord.add_argument("--run", required=True)
    coord.add_argument("--shadow-dir", default="")
    coord.add_argument("--capture-id", default="",
                       help="per-capture identity written into every "
                            "coordinator record")
    coord.add_argument("--timeout", type=float, default=900.0)
    coord.add_argument("--seal-timeout", type=float, default=300.0)
    coord.add_argument("--translation-timeout", type=float, default=180.0)
    coord.add_argument("--replay-timeout", type=float, default=180.0)
    coord.add_argument("--compare-timeout", type=float, default=60.0)
    coord.add_argument("--policy")
    coord.add_argument("--continuous", action="store_true")
    coord.add_argument("--max-frames", type=int, default=3)
    coord.add_argument("--continue-on-difference", action="store_true")
    coord.add_argument("--case-max-bytes", type=int,
                       default=512 * 1024 * 1024)
    coord.add_argument("--case-dir", default="")
    # The PR3 diagnosis always includes the shared comparison.  The old
    # --no-compare switch could not skip it without discarding qualification
    # evidence, so it is removed rather than silently ignored.
    coord.set_defaults(handler=shadow_coordinator)

    diag = sub.add_parser(
        "diagnose",
        help="diagnose an already-produced Metal result without re-executing it",
    )
    diag.add_argument("--frame", required=True)
    diag.add_argument("--metal", required=True)
    diag.add_argument("--out", default="")
    diag.add_argument("--policy")
    diag.add_argument("--shaders", default="")
    diag.add_argument("--package", action="store_true")
    diag.set_defaults(handler=diagnose_command)

    ins = sub.add_parser("inspect", help="validate and summarize one frame trace")
    ins.add_argument("--frame", required=True)
    ins.set_defaults(handler=inspect)

    tr = sub.add_parser("translate", help="translate a frame's shaders to MSL")
    tr.add_argument("--frame", required=True)
    tr.add_argument("--out")
    tr.add_argument("--force", action="store_true")
    tr.add_argument("--cache-dir", default="")
    tr.set_defaults(handler=translate_command)

    rep = sub.add_parser("replay", help="execute a traced frame through Metal")
    rep.add_argument("--frame", required=True)
    rep.add_argument("--shaders")
    rep.add_argument("--out", required=True)
    rep.add_argument("--stop-after", type=int, default=None)
    rep.add_argument("--force", action="store_true")
    rep.add_argument("--force-build", action="store_true")
    rep.add_argument(
        "--rationale",
        default=None,
        help="JSON file or inline JSON justifying this replay experiment",
    )
    rep.add_argument("--seed-marked", action="store_true")
    rep.add_argument("--capture-post", action="store_true")
    rep.add_argument(
        "--checkpoint-request",
        default=None,
        help="existing gfx-frame-checkpoint-request JSON for pre/post-event "
             "target readbacks",
    )
    rep.add_argument(
        "--only-event",
        type=int,
        default=None,
        help="replay one selected draw against its authentic GL pre-image",
    )
    rep.add_argument(
        "--depth-from-prefix",
        action="store_true",
        help="replay preceding events so the selected draw's depth input is defined",
    )
    rep.add_argument(
        "--uv2-probe",
        action="store_true",
        help="opt-in diagnostic: encode one isolated uv2 pass at --uv2-probe-event "
             "and write <out>/uv2-probe; the normal replay pass is unchanged",
    )
    rep.add_argument("--uv2-probe-pixels", type=_uv2_probe_pixels, metavar="X,Y;X,Y",
                     help="use sparse fragment observation at lower-left coordinates")
    rep.add_argument("--uv2-probe-event", type=int, default=1)
    rep.add_argument("--uv2-probe-program", type=int, default=310)
    rep.add_argument("--uv2-probe-framebuffer", type=int, default=6)
    rep.add_argument("--uv2-probe-texture", type=int, default=433)
    rep.set_defaults(handler=replay_command)

    iso = sub.add_parser("isolate", help="isolated marked-draw differential")
    iso.add_argument("--frame", required=True)
    iso.add_argument("--candidate", required=True)
    iso.add_argument(
        "--event",
        type=int,
        default=None,
        help="compare one selected event (keeps the all-events mode when omitted)",
    )
    iso.set_defaults(handler=isolate_command)

    cmp = sub.add_parser("compare", help="full-frame differential")
    cmp.add_argument("--reference", required=True)
    cmp.add_argument("--candidate", required=True)
    cmp.add_argument("--policy")
    cmp.add_argument("--out")
    cmp.add_argument("--difference", action="store_true")
    cmp.set_defaults(handler=compare_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
