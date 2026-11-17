"""Same-backend GL/GL control for captured transfer operations.

The authentic frame already contains both sides of an in-frame OpenGL copy:

* the captured default-framebuffer source image at the copy event, and
* the captured GL texture contents at the destination's first later sample.

Re-executing the copy through OpenGL is unnecessary for this control: the
engine already executed it, and the two captured GL images are independent
same-backend observations.  A declared channel normalization (BGRA default
framebuffer -> RGBA texture) must reproduce the destination with zero
differing pixels.  A nonzero result is a capture, decoder or comparison
failure, never a renderer difference.

The observation is only accepted when the first sample of the destination is
still the version produced by the selected copy.  A later copy, clear, render
target write, lifecycle mutation or delete between the copy and the sample
makes the correspondence unavailable; it is never reported as a difference.

This is deliberately scoped to the transfer/readback path.  It does not claim
a full-frame GL replay.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from automation import m6_compare

SCHEMA = "gfx-gl-control/v1"


class ControlError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _event_index(event: dict, fallback: int) -> int:
    value = event.get("index")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return fallback


def _read_events(frame: Path) -> list[dict]:
    path = frame / "frame-events.jsonl"
    if not path.is_file():
        raise ControlError("missing_event_stream", str(path))
    events = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def _read_lifecycle(frame: Path) -> tuple[list[dict] | None, str | None]:
    path = frame / "frame-sequence.json"
    if not path.is_file():
        return None, "missing_lifecycle_provenance"
    try:
        document = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "invalid_lifecycle_provenance"
    lifecycle = document.get("lifecycle")
    if not isinstance(lifecycle, list):
        return None, "invalid_lifecycle_provenance"
    return lifecycle, None


def _channel_swap(source_format: str | None,
                  destination_format: str | None) -> str | None:
    source_format = str(source_format or "").upper()
    destination_format = str(destination_format or "").upper()
    if source_format == destination_format:
        return None
    if source_format == "BGRA8" and destination_format == "RGBA8":
        return "bgra_to_rgba"
    if source_format == "RGBA8" and destination_format == "BGRA8":
        return "rgba_to_bgra"
    raise ControlError(
        "unsupported_channels",
        f"no exact normalization from {source_format or 'unknown'} to "
        f"{destination_format or 'unknown'}")


def _swap_red_blue(body: bytes) -> bytes:
    if len(body) % 4:
        raise ControlError("invalid_payload", "pixel payload is not BGRA8")
    swapped = bytearray(body)
    for offset in range(0, len(swapped), 4):
        swapped[offset], swapped[offset + 2] = (
            swapped[offset + 2], swapped[offset])
    return bytes(swapped)


def _lifecycle_write(lifecycle: list[dict], start_event: int,
                     end_event: int, name: str) -> dict | None:
    for item in lifecycle:
        event = item.get("event")
        if not isinstance(event, int) or event < start_event \
                or event >= end_event:
            continue
        names = item.get("names")
        if not isinstance(names, list):
            names = [item.get("name")]
        if name in {str(value) for value in names if value is not None}:
            return {
                "kind": "lifecycle_" + str(item.get("kind") or "write"),
                "event": event,
                "lifecycle_sequence": item.get("lifecycle_sequence"),
            }
    return None


def _destination_observation(events: list[dict], lifecycle: list[dict],
                             start_position: int, start_event: int,
                             name: str) -> dict:
    """Find the first sample of ``name`` and prove it is still the copy.

    Returns ``{"status": "observed", ...}`` only when no intervening write is
    recorded between the selected copy and that first sample.
    """
    for position in range(start_position + 1, len(events)):
        event = events[position]
        index = _event_index(event, position)
        kind = event.get("kind")
        target = event.get("target") or {}
        color = target.get("color_attachment") or {}
        target_name = str(color.get("name")) if color else ""
        if kind == "draw":
            if target_name == name:
                return {
                    "status": "unavailable",
                    "reason_code": "intervening_write",
                    "reason": (
                        f"draw {index} renders into destination {name} before "
                        "its first sample"),
                    "event": index,
                }
            for texture in event.get("textures") or []:
                if str(texture.get("name")) != name:
                    continue
                levels = texture.get("levels") or []
                if not levels:
                    return {
                        "status": "unavailable",
                        "reason_code": "missing_destination_version",
                        "reason": (
                            f"draw {index} samples destination {name} without "
                            "a captured level"),
                        "event": index,
                    }
                level = levels[0]
                if not level.get("file"):
                    return {
                        "status": "unavailable",
                        "reason_code": "missing_destination_version",
                        "reason": (
                            f"draw {index} samples destination {name} without "
                            "a captured payload"),
                        "event": index,
                    }
                lifecycle_write = _lifecycle_write(
                    lifecycle, start_event, index, name)
                if lifecycle_write is not None:
                    return {
                        "status": "unavailable",
                        "reason_code": lifecycle_write["kind"],
                        "reason": (
                            f"{lifecycle_write['kind']} at event "
                            f"{lifecycle_write['event']} changes destination "
                            f"{name} before its first sample"),
                        "event": lifecycle_write["event"],
                    }
                return {
                    "status": "observed",
                    "event": index,
                    "file": level["file"],
                    "sha256": level.get("sha256"),
                    "width": level.get("width") or texture.get("width"),
                    "height": level.get("height") or texture.get("height"),
                    "pixel_format": level.get("pixel_format"),
                    "internal_format": level.get("internal_format"),
                }
        elif kind == "clear" and target_name == name:
            return {
                "status": "unavailable",
                "reason_code": "intervening_clear",
                "reason": (
                    f"clear {index} targets destination {name} before its "
                    "first sample"),
                "event": index,
            }
        elif kind == "resolve" and str(event.get("destination")) == name:
            return {
                "status": "unavailable",
                "reason_code": "intervening_copy",
                "reason": (
                    f"copy {index} writes destination {name} before its first "
                    "sample"),
                "event": index,
            }
    return {
        "status": "unavailable",
        "reason_code": "no_consumer",
        "reason": f"destination {name} is never sampled in this frame",
        "event": None,
    }


def _unavailable_check(index: int, event: dict, destination: str,
                       observation: dict) -> dict:
    return {
        "event": index,
        "operation": event.get("operation"),
        "destination": destination,
        "status": "unavailable",
        "reason_code": observation.get("reason_code"),
        "reason": observation.get("reason"),
        "destination_event": observation.get("event"),
        "provenance": {
            "selected_copy_event": index,
            "first_consumer_event": observation.get("event"),
        },
    }


def control_frame(frame_dir: Path | str) -> dict:
    """Run every captured GL copy control in one frame.

    A missing or superseded observation is ``unavailable``; corrupted digests
    are ``invalid``; only an established, correctly versioned observation can
    be ``match`` or ``different``.
    """
    frame = Path(frame_dir).expanduser().resolve()
    events = _read_events(frame)
    lifecycle, lifecycle_error = _read_lifecycle(frame)
    checks: list[dict] = []
    for position, event in enumerate(events):
        if event.get("kind") != "resolve":
            continue
        index = _event_index(event, position)
        source = event.get("source_image")
        if not isinstance(source, dict) or not source.get("file"):
            continue
        destination = str(event.get("destination") or "")
        if not destination:
            continue
        if lifecycle is None:
            checks.append(_unavailable_check(index, event, destination, {
                "reason_code": lifecycle_error,
                "reason": (
                    "the frame has no readable frame-sequence lifecycle "
                    "record, so a later write cannot be excluded"),
                "event": None,
            }))
            continue
        observation = _destination_observation(
            events, lifecycle, position, index, destination)
        if observation.get("status") != "observed":
            checks.append(_unavailable_check(
                index, event, destination, observation))
            continue
        source_path = frame / "blobs" / str(source["file"])
        destination_path = frame / "blobs" / str(observation["file"])
        if not source_path.is_file():
            checks.append(_unavailable_check(index, event, destination, {
                "reason_code": "missing_source",
                "reason": f"captured copy source is absent: {source_path}",
                "event": observation["event"],
            }))
            continue
        if not destination_path.is_file():
            checks.append(_unavailable_check(index, event, destination, {
                "reason_code": "missing_destination",
                "reason": (
                    f"captured destination payload is absent: "
                    f"{destination_path}"),
                "event": observation["event"],
            }))
            continue
        source_sha = _sha(source_path)
        destination_sha = _sha(destination_path)
        if source.get("sha256") and source["sha256"] != source_sha:
            checks.append({
                "event": index,
                "operation": event.get("operation"),
                "destination": destination,
                "status": "invalid",
                "reason_code": "source_digest",
                "reason": f"captured copy source digest mismatch: {source_path}",
                "destination_event": observation["event"],
            })
            continue
        if observation.get("sha256") \
                and observation["sha256"] != destination_sha:
            checks.append({
                "event": index,
                "operation": event.get("operation"),
                "destination": destination,
                "status": "invalid",
                "reason_code": "destination_digest",
                "reason": (
                    f"captured destination digest mismatch: "
                    f"{destination_path}"),
                "destination_event": observation["event"],
            })
            continue
        width = int(source.get("width") or 0)
        height = int(source.get("height") or 0)
        if width <= 0 or height <= 0:
            checks.append(_unavailable_check(index, event, destination, {
                "reason_code": "source_extent",
                "reason": "captured copy source has no usable extent",
                "event": observation["event"],
            }))
            continue
        if (int(observation.get("width") or width) != width
                or int(observation.get("height") or height) != height):
            checks.append(_unavailable_check(index, event, destination, {
                "reason_code": "extent_mismatch",
                "reason": (
                    f"copy source {width}x{height} does not match destination "
                    f"{observation.get('width')}x"
                    f"{observation.get('height')}"),
                "event": observation["event"],
            }))
            continue
        source_format = "BGRA8"  # a default-framebuffer capture is BGRA8
        destination_format = str(
            observation.get("pixel_format")
            or observation.get("internal_format") or "")
        try:
            normalization = _channel_swap(source_format, destination_format)
        except ControlError as exc:
            checks.append(_unavailable_check(index, event, destination, {
                "reason_code": exc.kind,
                "reason": str(exc),
                "event": observation["event"],
            }))
            continue
        source_bytes = source_path.read_bytes()
        destination_bytes = destination_path.read_bytes()
        if len(source_bytes) != width * height * 4:
            checks.append({
                "event": index,
                "operation": event.get("operation"),
                "destination": destination,
                "status": "invalid",
                "reason_code": "source_size",
                "reason": f"copy source payload has the wrong size: {source_path}",
                "destination_event": observation["event"],
            })
            continue
        if len(destination_bytes) != width * height * 4:
            checks.append({
                "event": index,
                "operation": event.get("operation"),
                "destination": destination,
                "status": "invalid",
                "reason_code": "destination_size",
                "reason": (
                    f"destination payload has the wrong size: "
                    f"{destination_path}"),
                "destination_event": observation["event"],
            })
            continue
        candidate = (
            _swap_red_blue(destination_bytes)
            if normalization in ("bgra_to_rgba", "rgba_to_bgra")
            else destination_bytes)
        statistics = m6_compare.compare_images(
            source_bytes, candidate, width, height)
        status = "match" if statistics["mismatched_pixels"] == 0 else "different"
        checks.append({
            "event": index,
            "operation": event.get("operation"),
            "destination": destination,
            "destination_event": observation["event"],
            "width": width,
            "height": height,
            "source_file": str(source_path.relative_to(frame)),
            "source_sha256": source_sha,
            "destination_file": str(destination_path.relative_to(frame)),
            "destination_sha256": destination_sha,
            "normalization": {
                "channel_swap": normalization,
                "row_origin": "declared_lower_left",
            },
            "provenance": {
                "selected_copy_event": index,
                "first_consumer_event": observation["event"],
                "lifecycle": "checked",
            },
            "statistics": statistics,
            "status": status,
        })
    statuses = {item["status"] for item in checks}
    if "invalid" in statuses:
        status = "invalid"
    elif "different" in statuses:
        status = "different"
    elif checks and all(item["status"] == "match" for item in checks):
        status = "match"
    elif "unavailable" in statuses:
        status = "incomplete"
    else:
        status = "unavailable"
    return {
        "schema": SCHEMA,
        "frame_dir": str(frame),
        "scope": "captured_transfer",
        "checks": checks,
        "status": status,
        "qualified": status == "match",
        "reason_codes": sorted({
            item.get("reason_code") for item in checks
            if item.get("reason_code")}),
        "note": (
            "same-backend GL control of the captured copy/readback path; "
            "this does not claim a full-frame GL replay"),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        report = control_frame(args.frame)
    except ControlError as exc:
        print(json.dumps({"schema": SCHEMA, "status": "invalid",
                          "kind": exc.kind, "reason": str(exc)}, indent=2))
        return 3
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if report["status"] == "match":
        return 0
    if report["status"] == "different":
        return 1
    if report["status"] == "invalid":
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
