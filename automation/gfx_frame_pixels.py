#!/usr/bin/env python3
"""Full-frame and selected-prefix differential for captured EU4 frames.

Compares the authentic original-OpenGL frame reference against the Metal replay
of the same captured inputs. The comparator is deliberately strict about
provenance: it validates both sides' declarations, extents, formats, GPU
completion and payload digests before it reads a single pixel, and it never
rescales or masks the images.

    python3 -m automation.gfx_frame_pixels --reference <frame dir> --candidate <replay dir> \
        --policy analysis/m6/comparison-policy.json --out <dir>

Region diagnostics attribute differences to viewport/scissor bands and to the
draws recorded in the frame trace. A policy may declare the only accepted
normalization (row origin); anything else is a failure, not a tolerance.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import struct
import zlib
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation.gfx_artifacts import sha256_file as _sha256

ROOT = Path(__file__).resolve().parents[1]


class CompareError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompareError(f"unreadable JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CompareError(f"not an object: {path}")
    return value


def exact_comparison_statistics(statistics: dict, width: int,
                                height: int) -> tuple[bool | None, str]:
    """Return raw four-channel exactness for a comparison statistics block.

    A policy pass is not exactness.  Exact equality requires a complete,
    dimensionally consistent BGRA8 measurement with zero mismatched pixels,
    zero alpha differences and zero maximum channel error on all four
    channels.  Missing or inconsistent statistics return ``(None, reason)`` so
    callers fail closed instead of advertising a match.

    The function deliberately consumes the raw statistics rather than the
    policy verdict: a caller-supplied threshold must never be able to promote
    a one-byte difference into an exact match.
    """
    if not isinstance(statistics, dict):
        return None, "comparison statistics are absent"
    try:
        pixels = int(statistics["pixels"])
        mismatched = int(statistics["mismatched_pixels"])
        max_error = int(statistics["max_channel_error"])
        alpha = int(statistics["alpha_different_pixels"])
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"comparison statistics are incomplete: {exc}"
    mean_abs = statistics.get("mean_abs_error")
    if not isinstance(mean_abs, (list, tuple)) or len(mean_abs) != 4:
        return None, "comparison did not measure all four BGRA channels"
    if pixels <= 0 or width <= 0 or height <= 0:
        return None, "comparison extent is missing or empty"
    if pixels != width * height:
        return None, (f"comparison pixel count {pixels} disagrees with "
                      f"{width}x{height}")
    if mismatched < 0 or mismatched > pixels:
        return None, f"comparison mismatch count is out of range: {mismatched}"
    if alpha < 0 or alpha > mismatched:
        return None, f"comparison alpha count is out of range: {alpha}"
    if max_error < 0 or max_error > 255:
        return None, f"comparison maximum error is out of range: {max_error}"
    if (mismatched == 0 and max_error == 0
            and alpha == 0 and all(float(value) == 0.0 for value in mean_abs)):
        return True, ""
    return False, ""


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


def swap_red_blue(data: bytes) -> bytes:
    converted = bytearray(data)
    converted[0::4], converted[2::4] = data[2::4], data[0::4]
    return bytes(converted)


def canonical_rgba(data: bytes, width: int, height: int, pixel_format: str, origin: str) -> bytes:
    if pixel_format == "BGRA8":
        data = swap_red_blue(data)
    return _flip_rows(data, width, height) if origin == "lower_left" else data


def amplified_difference(reference: bytes, candidate: bytes, *, amplification: int,
                         tolerance: int = 0, mark_alpha: bool = False) -> bytes:
    if len(reference) != len(candidate) or len(reference) % 4:
        raise CompareError("difference images must have the same packed extent")
    try:
        import numpy as np
    except ImportError:
        rows = bytearray(len(reference))
        for i in range(0, len(reference), 4):
            rgb = [abs(reference[i + c] - candidate[i + c]) for c in range(3)]
            alpha = abs(reference[i + 3] - candidate[i + 3])
            shown = [min(255, value * amplification) for value in rgb]
            if max(*rgb, alpha) <= tolerance:
                shown = [0, 0, 0]
            elif mark_alpha and not any(rgb) and alpha:
                shown = [255, 0, 255]
            rows[i:i + 4] = bytes((*shown, 255))
        return bytes(rows)
    a, b = (np.frombuffer(body, dtype=np.uint8).reshape(-1, 4).astype(np.int16)
            for body in (reference, candidate))
    difference = np.abs(a - b)
    result = np.minimum(difference.astype(np.int32) * amplification, 255).astype(np.uint8)
    result[difference.max(axis=1) <= tolerance, :3] = 0
    if mark_alpha:
        result[(difference[:, :3].max(axis=1) == 0) & (difference[:, 3] > tolerance), :3] = (255, 0, 255)
    result[:, 3] = 255
    return result.tobytes()


def write_png_bytes(path: Path, rgba: bytes, width: int, height: int, *, alpha: bool = True) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    stride = width * 4
    rows = (rgba[y:y + stride] for y in range(0, height * stride, stride))
    raw = b"".join(b"\x00" + (row if alpha else bytes(
        x for i, x in enumerate(row) if i % 4 != 3)) for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                     + chunk(b"IDAT", zlib.compress(raw, 6 if alpha else 9))
                     + chunk(b"IEND", b""))


def _grid_span(index: int, size: int, count: int) -> tuple[int, int]:
    """Half-open pixel span of a floor-indexed grid bin.

    A pixel at coordinate ``value`` is assigned to bin
    ``value * count // size``.  The inverse span is therefore
    ``[ceil(index*size/count), ceil((index+1)*size/count))``; using floor for
    the end point would report a rectangle that does not contain the pixel for
    sizes that are not multiples of the grid.
    """
    start = (index * size + count - 1) // count
    end = ((index + 1) * size + count - 1) // count
    return start, end - start


def measure_pixel_deltas(reference: bytes, candidate: bytes, width: int,
                         height: int, tolerance: int) -> dict:
    """Measure packed four-channel byte deltas in their input channel order."""
    pixels = width * height
    sums = [0] * 4
    maxima = [0] * 4
    grid_counts = [0] * 64
    grid_sums = [0] * 64
    mismatched = alpha = worst = 0
    first_index = worst_index = None
    left, top, right, bottom = width, height, -1, -1
    for index in range(pixels):
        offset = index * 4
        deltas = [abs(reference[offset + channel] - candidate[offset + channel])
                  for channel in range(4)]
        local_max = max(deltas)
        for channel, delta in enumerate(deltas):
            sums[channel] += delta
            if delta > maxima[channel]:
                maxima[channel] = delta
        if local_max > worst:
            worst, worst_index = local_max, index
        if deltas[3] > tolerance:
            alpha += 1
        if local_max > tolerance:
            mismatched += 1
            x, y = index % width, index // width
            if first_index is None:
                first_index = index
            left, right = min(left, x), max(right, x)
            top, bottom = min(top, y), max(bottom, y)
            cell = (y * 8 // height) * 8 + x * 8 // width
            grid_counts[cell] += 1
            grid_sums[cell] += local_max
    return {
        "pixels": pixels,
        "mismatched_pixels": mismatched,
        "alpha_mismatched_pixels": alpha,
        "max_channel_error": worst,
        "channel_sums": sums,
        "channel_maxima": maxima,
        "first_index": first_index,
        "worst_index": worst_index,
        "bounds": (left, top, right, bottom) if mismatched else None,
        "grid_counts": grid_counts,
        "grid_sums": grid_sums,
    }


def compare_images(reference: bytes, candidate: bytes, width: int, height: int) -> dict:
    """Exact statistics without numpy, over BGRA8."""
    deltas = measure_pixel_deltas(reference, candidate, width, height, 0)
    total = deltas["pixels"]
    mismatched = deltas["mismatched_pixels"]
    first_index = deltas["first_index"]
    bounds = None
    if deltas["bounds"] is not None:
        left, top, right, bottom = deltas["bounds"]
        bounds = {"x": left, "y": top, "width": right - left + 1,
                  "height": bottom - top + 1}
    regions = []
    for cell, error_sum in enumerate(deltas["grid_sums"]):
        row, column = divmod(cell, 8)
        x, span_x = _grid_span(column, width, 8)
        y, span_y = _grid_span(row, height, 8)
        if span_x and span_y:
            regions.append({
                "row": row, "column": column, "x": x, "y": y,
                "width": span_x, "height": span_y,
                "mean_max_channel_error": round(error_sum / (span_x * span_y), 6),
            })
    regions.sort(key=lambda item: item["mean_max_channel_error"], reverse=True)
    return {
        "pixels": total,
        "mismatched_pixels": mismatched,
        "mismatched_fraction": mismatched / total if total else 1.0,
        "alpha_different_pixels": deltas["alpha_mismatched_pixels"],
        "max_channel_error": deltas["max_channel_error"],
        "mean_abs_error": [round(value / total, 9) for value in deltas["channel_sums"]],
        "mean_max_channel_error": round(sum(deltas["grid_sums"]) / total, 9),
        "first_differing_pixel": (
            None if first_index is None else
            {"x": first_index % width, "y": first_index // width, "offset": first_index}),
        "differing_bounds": bounds,
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


def _reject_alias(reference_path: Path, candidate_path: Path) -> None:
    """The comparison must consume two independently produced files.

    Equal contents (and therefore equal digests) are legitimate; the same
    filesystem object, a symlink to it or a hard link to it are not, because a
    readback written over the reference would compare the frame to itself.
    """
    try:
        if reference_path.resolve() == candidate_path.resolve():
            raise CompareError("reference and candidate pixels are the same file")
        if os.path.samefile(reference_path, candidate_path):
            raise CompareError("reference and candidate pixels alias the same file")
    except OSError as exc:
        raise CompareError(f"reference/candidate identity is unreadable: {exc}")


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
    if "failed" not in replay:
        raise CompareError("candidate replay does not report its failed count")
    if replay.get("failed") != 0:
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
    _reject_alias(reference_path, candidate_path)
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
    difference = amplified_difference(reference, candidate, amplification=8)
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
