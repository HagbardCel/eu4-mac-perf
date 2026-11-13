#!/usr/bin/env python3
"""Independent acceptance check for the M6 live title-scene runs.

A run is accepted only when every mandatory criterion passes:

  executed       the menu checkpoint was reached and encoded, no draw refused
  gpu_completed  the GPU finished the frame
  presented      a non-blank frame was actually presented and captured
  visual_match   the authentic reference is present and both the UI regions
                 and the animated map region match the scene policy

Missing or invalid reference data is a hard failure: a run can never be
accepted without it. The scene policy (regions, tolerances) lives in
``analysis/m6/live-scene-policy.json`` so the checker stays reusable.

Usage:
    python3 -m automation.m6_live_accept \
        --reference <menu frame dir> --policy <policy.json> \
        --run <probe output> [--run ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "analysis" / "m6" / "live-scene-policy.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_reference(reference: Path, logical: tuple[int, int]) -> tuple[np.ndarray | None, str]:
    path = reference / "frame-reference.bgra"
    width, height = logical
    if not path.is_file():
        return None, f"reference frame is missing: {path}"
    data = np.fromfile(path, dtype=np.uint8)
    if data.size != width * height * 4:
        return None, (
            f"reference frame has {data.size} bytes, expected "
            f"{width * height * 4} for {width}x{height}"
        )
    picture = data.reshape(height, width, 4)
    return picture[::-1, :, [2, 1, 0]].astype(np.int16), ""


def load_candidate(path: Path, logical: tuple[int, int]) -> tuple[np.ndarray | None, str, dict]:
    from PIL import Image

    details: dict = {}
    if not path.is_file():
        return None, f"presented frame dump is missing: {path}", details
    data = np.fromfile(path, dtype=np.uint8)
    if data.size % 4 != 0:
        return None, "presented frame dump is not 32-bit", details
    physical = None
    for candidate in ((3456, 2234), (1728, 1117), (2160, 1396), (2560, 1654)):
        if candidate[0] * candidate[1] * 4 == data.size:
            physical = candidate
            break
    if physical is None:
        return None, f"presented frame dump has unexpected size {data.size}", details
    details["physical"] = list(physical)
    width, height = logical
    picture = data.reshape(physical[1], physical[0], 4)
    # The dump keeps the captured GL lower-left origin; flip it to the same
    # top-left convention as the reference before resizing.
    rgb = picture[::-1, :, [2, 1, 0]]
    if physical[0] % width != 0 or physical[1] % height != 0:
        return None, (
            f"physical frame {physical[0]}x{physical[1]} is not an integer "
            f"multiple of the logical scene {width}x{height}"
        ), details
    if physical != (width, height):
        image = Image.fromarray(rgb.astype(np.uint8)).resize((width, height), Image.LANCZOS)
        rgb = np.asarray(image)
    details["resampled"] = physical != (width, height)
    return rgb.astype(np.int16), "", details


def high_pass(image: np.ndarray, region, radius: float = 6.0) -> np.ndarray:
    """Remove the low-frequency scene background so the comparison measures UI
    structure (text, edges, buttons) rather than the animated map behind it."""
    from PIL import Image, ImageFilter

    y0, y1, x0, x1 = region
    window = image[y0:y1, x0:x1].astype(np.uint8)
    blurred = Image.fromarray(window).filter(ImageFilter.GaussianBlur(radius))
    low = np.asarray(blurred, dtype=np.int16)
    return window.astype(np.int16) - low


def region_error(candidate: np.ndarray, reference: np.ndarray, region) -> float:
    """Diagnostic mean absolute difference of the raw regions."""
    y0, y1, x0, x1 = region
    return float(np.abs(candidate[y0:y1, x0:x1] -
                        reference[y0:y1, x0:x1]).mean())


def region_correlation(candidate: np.ndarray, reference: np.ndarray,
                       region) -> float:
    """Structural similarity of the UI: high-pass correlation. The animated map
    background is low-frequency and cancels; the UI text/edges dominate. The
    live frame is rendered at physical resolution and resampled, so exact
    pixel equality is not a justified criterion."""
    a = high_pass(candidate, region).astype(np.float32)
    b = high_pass(reference, region).astype(np.float32)
    a -= a.mean()
    b -= b.mean()
    denominator = float(np.sqrt((a * a).sum()) * np.sqrt((b * b).sum()))
    if denominator == 0.0:
        return 0.0
    return float((a * b).sum() / denominator)


def check_run(run: Path, reference: np.ndarray | None, reference_error: str,
              policy: dict) -> dict:
    logical = tuple(policy["reference"]["logical"])
    result: dict = {"run": str(run), "status": {}, "checks": {}, "stats": {},
                    "ok": False}
    status = result["status"]
    checks = result["checks"]

    probe_path = run / "probe-result.json"
    if not probe_path.is_file():
        result["reason"] = "no probe-result.json"
        return result
    probe = json.loads(probe_path.read_text())
    probe_status = probe.get("status") or {}
    refused = sorted(run.glob("draw-refused-*.json"))

    checks["probe_captured"] = probe.get("overall") == "captured"
    checks["draw_reached"] = bool(probe_status.get("draw_reached"))
    checks["draw_encoded"] = bool(probe_status.get("draw_encoded"))
    checks["pixels_captured"] = bool(probe_status.get("pixels_captured"))
    checks["no_refused_draws"] = not refused
    status["executed"] = all(
        checks[key]
        for key in ("probe_captured", "draw_reached", "draw_encoded",
                    "pixels_captured", "no_refused_draws")
    )
    checks["gpu_completed"] = bool(probe_status.get("gpu_completed"))
    status["gpu_completed"] = checks["gpu_completed"]

    candidate, candidate_error, details = load_candidate(
        run / "swap-frame.bgra", logical
    )
    checks["frame_dump"] = candidate is not None
    checks["presented_flag"] = bool(probe_status.get("presented"))
    if candidate is not None:
        unique = int(len(np.unique(candidate.reshape(-1, 3), axis=0)))
        details["unique_colors"] = unique
        checks["non_blank"] = unique > 1000
        checks["frame_sha256"] = _sha256(run / "swap-frame.bgra")
    else:
        checks["non_blank"] = False
        result["reason"] = candidate_error
    status["presented"] = bool(
        checks["presented_flag"] and checks["frame_dump"] and checks["non_blank"]
    )

    status["visual_match"] = False
    if reference is None:
        result["reason"] = reference_error or "reference is unavailable"
    elif candidate is not None:
        regions = [tuple(region) for region in policy["ui_regions"]]
        errors = [region_error(candidate, reference, region) for region in regions]
        correlations = [region_correlation(candidate, reference, region)
                        for region in regions]
        details["ui_region_error"] = [round(value, 3) for value in errors]
        details["ui_region_correlation"] = [round(value, 3) for value in correlations]
        ui_ok = min(correlations) >= float(policy["ui_min_correlation"])
        y0, y1, x0, x1 = policy["map_region"]
        window = candidate[y0:y1, x0:x1]
        map_unique = int(len(np.unique(window.reshape(-1, 3), axis=0)))
        map_mean = float(window.mean())
        details["map_unique_colors"] = map_unique
        details["map_mean"] = round(map_mean, 3)
        map_ok = (
            map_unique >= int(policy["map_min_unique_colors"])
            and map_mean >= float(policy["map_min_mean"])
            and map_unique < int(0.5 * (y1 - y0) * (x1 - x0))
        )
        details["ui_ok"] = ui_ok
        details["map_ok"] = map_ok
        checks["ui_regions"] = ui_ok
        checks["map_region"] = map_ok
        status["visual_match"] = bool(ui_ok and map_ok)
    result["stats"] = details
    status["accepted"] = bool(
        status["executed"] and status["gpu_completed"] and status["presented"]
        and status["visual_match"]
    )
    result["ok"] = status["accepted"]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--run", action="append", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    policy = json.loads(Path(args.policy).read_text())
    logical = tuple(policy["reference"]["logical"])
    reference, reference_error = load_reference(Path(args.reference), logical)
    runs = [check_run(Path(item), reference, reference_error, policy)
            for item in args.run]
    report = {
        "schema": "m6-live-acceptance/v1",
        "reference": str(args.reference),
        "reference_ok": reference is not None,
        "reference_error": reference_error,
        "runs": runs,
        "clean_starts": sum(1 for item in runs if item["ok"]),
        "ok": len(runs) == 3 and all(item["ok"] for item in runs),
    }
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    print(text)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
