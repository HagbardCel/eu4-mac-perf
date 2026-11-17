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


def _first_destination_version(events: list[dict], start: int,
                               name: str) -> dict | None:
    for event in events[start:]:
        if event.get("kind") != "draw":
            continue
        for texture in event.get("textures") or []:
            if str(texture.get("name")) != name:
                continue
            levels = texture.get("levels") or []
            if not levels:
                continue
            level = levels[0]
            if level.get("file"):
                return {
                    "event": event.get("index"),
                    "file": level["file"],
                    "sha256": level.get("sha256"),
                    "width": level.get("width") or texture.get("width"),
                    "height": level.get("height") or texture.get("height"),
                    "pixel_format": level.get("pixel_format"),
                    "internal_format": level.get("internal_format"),
                }
    return None


def control_frame(frame_dir: Path | str) -> dict:
    """Run every captured GL copy control in one frame."""
    frame = Path(frame_dir).expanduser().resolve()
    events = _read_events(frame)
    checks = []
    for index, event in enumerate(events):
        if event.get("kind") != "resolve":
            continue
        source = event.get("source_image")
        if not isinstance(source, dict) or not source.get("file"):
            continue
        destination = str(event.get("destination") or "")
        if not destination:
            continue
        destination_version = _first_destination_version(
            events, index + 1, destination)
        if destination_version is None:
            raise ControlError(
                "missing_destination",
                f"no captured GL destination texture for copy {index}")
        source_path = frame / "blobs" / str(source["file"])
        destination_path = frame / "blobs" / str(destination_version["file"])
        if not source_path.is_file():
            raise ControlError("missing_source", str(source_path))
        if not destination_path.is_file():
            raise ControlError("missing_destination", str(destination_path))
        source_sha = _sha(source_path)
        destination_sha = _sha(destination_path)
        if source.get("sha256") and source["sha256"] != source_sha:
            raise ControlError("source_digest", str(source_path))
        if (destination_version.get("sha256")
                and destination_version["sha256"] != destination_sha):
            raise ControlError("destination_digest", str(destination_path))
        width = int(source.get("width") or 0)
        height = int(source.get("height") or 0)
        if width <= 0 or height <= 0:
            raise ControlError("source_extent", str(source_path))
        if (int(destination_version.get("width") or width) != width
                or int(destination_version.get("height") or height) != height):
            raise ControlError(
                "extent_mismatch",
                f"copy source {width}x{height} does not match destination "
                f"{destination_version.get('width')}x"
                f"{destination_version.get('height')}")
        source_format = "BGRA8"  # a default-framebuffer capture is BGRA8
        destination_format = str(
            destination_version.get("pixel_format")
            or destination_version.get("internal_format") or "")
        normalization = _channel_swap(source_format, destination_format)
        source_bytes = source_path.read_bytes()
        destination_bytes = destination_path.read_bytes()
        if len(source_bytes) != width * height * 4:
            raise ControlError("source_size", str(source_path))
        if len(destination_bytes) != width * height * 4:
            raise ControlError("destination_size", str(destination_path))
        if normalization == "bgra_to_rgba":
            candidate = _swap_red_blue(destination_bytes)
        elif normalization == "rgba_to_bgra":
            candidate = _swap_red_blue(destination_bytes)
        else:
            candidate = destination_bytes
        statistics = m6_compare.compare_images(
            source_bytes, candidate, width, height)
        status = "match" if statistics["mismatched_pixels"] == 0 else "different"
        checks.append({
            "event": index,
            "operation": event.get("operation"),
            "destination": destination,
            "destination_event": destination_version["event"],
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
            "statistics": statistics,
            "status": status,
        })
    if not checks:
        raise ControlError(
            "no_control",
            "the frame has no captured copy source/destination pair; run the "
            "capture with --copy-source")
    return {
        "schema": SCHEMA,
        "frame_dir": str(frame),
        "scope": "captured_transfer",
        "checks": checks,
        "status": "match" if all(item["status"] == "match"
                                 for item in checks) else "different",
        "qualified": all(item["status"] == "match" for item in checks),
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
        return 1
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
