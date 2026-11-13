#!/usr/bin/env python3
"""Full-frame and selected-prefix differential for captured EU4 frames.

Compares the authentic original-OpenGL frame reference against the Metal replay
of the same captured inputs. The comparator is deliberately strict about
provenance: it validates both sides' declarations, extents, formats, GPU
completion and payload digests before it reads a single pixel, and it never
rescales or masks the images.

    python3 -m automation.m6_compare --reference <frame dir> --candidate <replay dir> \
        --policy analysis/m6/comparison-policy.json --out <dir>

Region diagnostics attribute differences to viewport/scissor bands and to the
draws recorded in the frame trace. A policy may declare the only accepted
normalization (row origin); anything else is a failure, not a tolerance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CompareError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompareError(f"unreadable JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CompareError(f"not an object: {path}")
    return value


def _load_bgra(path: Path, width: int, height: int) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CompareError(f"unreadable image {path}: {exc}") from exc
    if len(data) != width * height * 4:
        raise CompareError(
            f"image size {len(data)} != {width}x{height}x4={width * height * 4}"
        )
    return data


def _flip_rows(data: bytes, width: int, height: int) -> bytes:
    stride = width * 4
    out = bytearray(len(data))
    for row in range(height):
        out[row * stride:(row + 1) * stride] = data[
            (height - 1 - row) * stride:(height - row) * stride
        ]
    return bytes(out)


def compare_images(
    reference: bytes, candidate: bytes, width: int, height: int
) -> dict:
    """Exact statistics without numpy, over BGRA8."""
    total = width * height
    mismatched = 0
    alpha_different = 0
    sum_abs = [0, 0, 0, 0]
    max_error = 0
    # Region grid for localization (default 8x8).
    grid_y, grid_x = 8, 8
    region_sum = [
        [0] * grid_x for _ in range(grid_y)
    ]
    region_counts = [[0] * grid_x for _ in range(grid_y)]
    region_pixels = [
        [0] * grid_x for _ in range(grid_y)
    ]
    for pixel in range(total):
        offset = pixel * 4
        b0, g0, r0, a0 = reference[offset:offset + 4]
        b1, g1, r1, a1 = candidate[offset:offset + 4]
        differences = (
            abs(b0 - b1), abs(g0 - g1), abs(r0 - r1), abs(a0 - a1)
        )
        pixel_max = max(differences)
        if pixel_max != 0:
            mismatched += 1
        if a0 != a1:
            alpha_different += 1
        if pixel_max > max_error:
            max_error = pixel_max
        for channel in range(4):
            sum_abs[channel] += differences[channel]
        x = pixel % width
        y = pixel // width
        region = (y * grid_y // height, x * grid_x // width)
        region_sum[region[0]][region[1]] += pixel_max
        region_counts[region[0]][region[1]] += 1
        region_pixels[region[0]][region[1]] += 1
    regions = []
    for gy in range(grid_y):
        for gx in range(grid_x):
            count = region_counts[gy][gx]
            if count == 0:
                continue
            mean = region_sum[gy][gx] / count
            regions.append(
                {
                    "row": gy,
                    "column": gx,
                    "x": gx * width // grid_x,
                    "y": gy * height // grid_y,
                    "width": width // grid_x,
                    "height": height // grid_y,
                    "mean_max_channel_error": round(mean, 6),
                }
            )
    regions.sort(key=lambda item: item["mean_max_channel_error"], reverse=True)
    return {
        "pixels": total,
        "mismatched_pixels": mismatched,
        "mismatched_fraction": mismatched / total if total else 1.0,
        "alpha_different_pixels": alpha_different,
        "max_channel_error": max_error,
        "mean_abs_error": [round(value / total, 9) for value in sum_abs],
        "mean_max_channel_error": round(
            sum(region_sum[gy][gx] for gy in range(grid_y) for gx in range(grid_x))
            / total,
            9,
        ),
        "regions": regions,
    }


def _first_differing_region(regions: list[dict]) -> dict | None:
    for region in regions:
        if region["mean_max_channel_error"] > 0:
            return region
    return None


def _draw_inventory(trace: dict) -> dict:
    draws = trace.get("_draws")
    if draws is None:
        events = []
        path = Path(trace["_frame_dir"]) / "frame-events.jsonl"
        if path.is_file():
            events = [
                json.loads(line)
                for line in path.read_text().splitlines()
                if line.strip()
            ]
        draws = [event for event in events if event.get("kind") == "draw"]
    inventory = []
    for index, event in enumerate(draws):
        draw = event.get("draw") or {}
        target = event.get("target") or {}
        viewport = (event.get("state") or {}).get("viewport")
        inventory.append(
            {
                "index": index,
                "entry": draw.get("entry"),
                "caller_rva": draw.get("caller_rva"),
                "program": (event.get("program") or {}).get("name"),
                "mode": draw.get("mode"),
                "count": draw.get("count"),
                "target_framebuffer": target.get("framebuffer"),
                "target_width": target.get("width"),
                "target_height": target.get("height"),
                "viewport": viewport,
                "scissor": ((event.get("state") or {}).get("scissor") or {}),
            }
        )
    return {"draws": inventory}


def compare(
    reference_dir: Path, candidate_dir: Path, policy: dict
) -> dict:
    reference_dir = Path(reference_dir).resolve()
    candidate_dir = Path(candidate_dir).resolve()
    manifest = _read_json(reference_dir / "frame-trace.json")
    if manifest.get("complete") is not True:
        raise CompareError("reference frame trace is incomplete")
    frame = manifest.get("frame") or {}
    width, height = frame.get("width"), frame.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise CompareError("reference frame extent is missing")
    reference_path = reference_dir / str(frame.get("reference") or "")
    if not reference_path.is_file():
        raise CompareError("reference pixels are absent")
    if _sha256(reference_path) != frame.get("reference_sha256"):
        raise CompareError("reference pixels do not match their declared digest")
    reference = _load_bgra(reference_path, width, height)

    replay = _read_json(candidate_dir / "replay.json")
    if replay.get("status") != "ok":
        raise CompareError(f"candidate replay status {replay.get('status')!r}")
    if replay.get("command_status") != 4:
        raise CompareError("candidate GPU command did not complete")
    if replay.get("failed") not in (0, None):
        raise CompareError("candidate replay reports failed draws")
    expected_events = manifest.get("event_count")
    if not isinstance(expected_events, int):
        # Older traces do not repeat the count in the manifest; the captured
        # event log is the authoritative record.
        events_path = reference_dir / "frame-events.jsonl"
        if events_path.is_file():
            expected_events = sum(
                1 for line in events_path.read_text().splitlines() if line.strip()
            )
    if not isinstance(expected_events, int) or expected_events <= 0:
        raise CompareError("reference event count is missing")
    if replay.get("events") != expected_events:
        raise CompareError(
            f"candidate replay processed {replay.get('events')!r} events, "
            f"expected {expected_events}"
        )
    if replay.get("encoded") != expected_events:
        raise CompareError(
            f"candidate replay encoded {replay.get('encoded')!r} events, "
            f"expected {expected_events}"
        )
    candidate_frame = replay.get("frame") or {}
    if (candidate_frame.get("width"), candidate_frame.get("height")) != (
        width,
        height,
    ):
        raise CompareError("candidate extent differs from the reference")
    candidate_path = candidate_dir / str(candidate_frame.get("raw_file") or "")
    if not candidate_path.is_file():
        raise CompareError("candidate pixels are absent")
    if candidate_frame.get("raw_bytes") is not None:
        if candidate_path.stat().st_size != candidate_frame["raw_bytes"]:
            raise CompareError("candidate pixel size differs from its declaration")
    candidate = _load_bgra(candidate_path, width, height)

    origin = frame.get("origin")
    candidate_origin = candidate_frame.get("origin")
    normalized = False
    if origin != candidate_origin:
        allowed = policy.get("allowed_normalizations") or []
        if "row_origin" not in allowed:
            raise CompareError(
                f"row origin differs ({origin} vs {candidate_origin}) and the "
                "policy does not declare the row_origin normalization"
            )
        if {origin, candidate_origin} != {"lower_left", "top_left"}:
            raise CompareError("unsupported row origin pair")
        candidate = _flip_rows(candidate, width, height)
        normalized = True
    statistics = compare_images(reference, candidate, width, height)

    thresholds = policy.get("thresholds") or {}
    max_channel = int(thresholds.get("max_channel_error", 0))
    mismatched_fraction = float(thresholds.get("mismatched_fraction", 0.0))
    mean_abs = float(thresholds.get("mean_abs_error", 0.0))
    alpha_policy = policy.get("alpha", {})
    ignored = bool(alpha_policy.get("ignored", False))
    if ignored:
        for channel in (3,):
            statistics["mean_abs_error"][channel] = 0.0
    passed = (
        statistics["max_channel_error"] <= max_channel
        and statistics["mismatched_fraction"] <= mismatched_fraction
        and statistics["mean_max_channel_error"] <= mean_abs
        and (ignored or statistics["alpha_different_pixels"] == 0)
    )
    worst = _first_differing_region(statistics["regions"])
    inventory = _draw_inventory({"frame": manifest, "_frame_dir": str(reference_dir)})
    attribution = []
    if worst is not None:
        x0, y0 = worst["x"], worst["y"]
        x1, y1 = x0 + worst["width"], y0 + worst["height"]
        for item in inventory["draws"]:
            viewport = item.get("viewport")
            scissor = item.get("scissor") or {}
            box = viewport
            if scissor.get("enabled") and scissor.get("box"):
                box = scissor["box"]
            if not isinstance(box, list) or len(box) != 4:
                continue
            bx, by, bw, bh = box
            if bx < x1 and bx + bw > x0 and by < y1 and by + bh > y0:
                attribution.append(item)
    return {
        "schema": "gfx-frame-differential/v1",
        "status": "pass" if passed else "fail",
        "reference": {
            "dir": str(reference_dir),
            "sha256": frame.get("reference_sha256"),
            "origin": origin,
            "width": width,
            "height": height,
        },
        "candidate": {
            "dir": str(candidate_dir),
            "sha256": _sha256(candidate_path),
            "origin": candidate_origin,
            "events": replay.get("events"),
            "encoded": replay.get("encoded"),
            "failed": replay.get("failed"),
            "command_status": replay.get("command_status"),
        },
        "normalization": {
            "row_origin_flip": normalized,
            "alpha": "ignored" if ignored else "compared",
        },
        "statistics": statistics,
        "worst_region": worst,
        "attribution": attribution,
        "policy": policy,
    }


def write_difference(
    reference_dir: Path, candidate_dir: Path, width: int, height: int, out: Path
) -> dict:
    manifest = _read_json(Path(reference_dir) / "frame-trace.json")
    frame = manifest["frame"]
    reference = _load_bgra(
        Path(reference_dir) / frame["reference"], width, height
    )
    replay = _read_json(Path(candidate_dir) / "replay.json")
    candidate = _load_bgra(
        Path(candidate_dir) / replay["frame"]["raw_file"], width, height
    )
    stride = width * 4
    difference = bytearray(stride * height)
    for offset in range(0, len(reference), 4):
        b0, g0, r0, _ = reference[offset:offset + 4]
        b1, g1, r1, _ = candidate[offset:offset + 4]
        difference[offset] = min(255, abs(b0 - b1) * 8)
        difference[offset + 1] = min(255, abs(g0 - g1) * 8)
        difference[offset + 2] = min(255, abs(r0 - r1) * 8)
        difference[offset + 3] = 255
    path = out / "difference.bgra"
    path.write_bytes(bytes(difference))
    return {
        "raw_file": path.name,
        "raw_bytes": len(difference),
        "row_stride": stride,
        "origin": "lower_left",
        "pixel_format": "BGRA8",
        "amplification": 8,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--difference", action="store_true")
    args = parser.parse_args(argv)
    reference = Path(args.reference).expanduser().resolve()
    candidate = Path(args.candidate).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        policy = _read_json(Path(args.policy).expanduser().resolve())
        report = compare(reference, candidate, policy)
        width = report["reference"]["width"]
        height = report["reference"]["height"]
        if args.difference:
            report["difference"] = write_difference(
                reference, candidate, width, height, out
            )
    except CompareError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    (out / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    summary = {
        "status": report["status"],
        "mismatched_pixels": report["statistics"]["mismatched_pixels"],
        "pixels": report["statistics"]["pixels"],
        "max_channel_error": report["statistics"]["max_channel_error"],
        "mean_max_channel_error": report["statistics"]["mean_max_channel_error"],
        "mean_abs_error": report["statistics"]["mean_abs_error"],
        "worst_region": report["worst_region"],
    }
    print(json.dumps(summary, indent=2))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
