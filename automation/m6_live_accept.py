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
    """Loads and audits the authentic reference: the manifest must be complete,
    its declared extent must match the policy, the raw pixels must match their
    declared digest, and the row origin must be the supported lower-left one."""
    manifest_path = reference / "frame-trace.json"
    path = reference / "frame-reference.bgra"
    width, height = logical
    if not manifest_path.is_file():
        return None, f"reference manifest is missing: {manifest_path}"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        return None, f"reference manifest is unreadable: {exc}"
    if manifest.get("complete") is not True:
        return None, "reference manifest is incomplete"
    frame = manifest.get("frame") or {}
    if (frame.get("width"), frame.get("height")) != (width, height):
        return None, (
            f"reference manifest extent {frame.get('width')}x{frame.get('height')} "
            f"does not match the policy {width}x{height}"
        )
    if frame.get("origin") != "lower_left":
        return None, f"reference row origin {frame.get('origin')!r} is unsupported"
    if not path.is_file():
        return None, f"reference frame is missing: {path}"
    data = np.fromfile(path, dtype=np.uint8)
    if data.size != width * height * 4:
        return None, (
            f"reference frame has {data.size} bytes, expected "
            f"{width * height * 4} for {width}x{height}"
        )
    if hashlib.sha256(data.tobytes()).hexdigest() != frame.get("reference_sha256"):
        return None, "reference pixels do not match their declared digest"
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
    width, height = logical
    pixels = data.size // 4
    scale = None
    if pixels > 0 and width > 0 and height > 0 and pixels % (width * height) == 0:
        scale = int(round((pixels / float(width * height)) ** 0.5))
        if scale * scale * width * height != pixels:
            scale = None
    if scale is None:
        return None, (
            f"presented frame dump {data.size} bytes does not match the "
            f"logical scene {width}x{height} at an integer scale"
        ), details
    physical = (width * scale, height * scale)
    details["physical"] = list(physical)
    details["scale"] = scale
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


def map_color_similarity(window: np.ndarray, reference: np.ndarray) -> float:
    """Coarse 4x4x4 RGB histogram overlap with the reference map region.

    The authentic menu map is static across captures (verified over 45 s), so a
    materially different palette (for example a missing terrain layer that
    leaves the political colours exposed) is a real rendering defect rather than
    animation.
    """
    def histogram(patch: np.ndarray) -> np.ndarray:
        q = patch[:, :, :3].astype(np.int32) * 4 // 256
        index = (q[:, :, 0] << 4) | (q[:, :, 1] << 2) | q[:, :, 2]
        counts = np.bincount(index.reshape(-1), minlength=64).astype(np.float64)
        total = counts.sum()
        return counts / total if total else counts

    a = histogram(window)
    b = histogram(reference)
    return float(np.minimum(a, b).sum())


def map_structure_ok(window: np.ndarray, policy: dict) -> bool:
    """Credible map content: real structure, not a flat colour or noise.

    A map has spatial coherence (neighbouring pixels are similar) and a
    non-trivial luminance spread. Random noise has the spread but no coherence;
    a blank or single-colour fill has neither.
    """
    from PIL import Image

    rgb = window[:, :, :3].astype(np.float32)
    luminance = rgb.mean(axis=2)
    if luminance.size == 0:
        return False
    coarse = np.asarray(
        Image.fromarray(luminance.astype(np.uint8)).resize((64, 64), Image.LANCZOS),
        dtype=np.float32,
    )
    spread = float(coarse.std())
    if spread < float(policy.get("map_min_std", 20.0)):
        return False
    left = coarse[:, :-1]
    right = coarse[:, 1:]
    left = left - left.mean()
    right = right - right.mean()
    denominator = float(np.sqrt((left * left).sum() * (right * right).sum()))
    coherence = float((left * right).sum() / denominator) if denominator else 0.0
    return coherence >= float(policy.get("map_min_coherence", 0.5))


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


def run_identity(run: Path, probe: dict) -> str:
    """Stable per-launch identity recorded by the probe launcher."""
    provenance = probe.get("provenance") or {}
    identity = provenance.get("session") or provenance.get("pid")
    if identity:
        return str(identity)
    metadata_path = run / "metadata.json"
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text())
            return str(metadata.get("pid") or "")
        except (OSError, ValueError):
            return ""
    return ""


def summarize_runs(runs: list[dict]) -> dict:
    """Aggregates per-run results, requiring distinct paths and launches.

    One successful capture can never satisfy the three-clean-start requirement
    by being passed three times, even through copied directories or symlinks.
    """
    canonical = [item.get("canonical") or "" for item in runs]
    identities = [item.get("identity") or "" for item in runs]
    distinct_runs = len(set(canonical)) == len(runs) and all(canonical)
    distinct_identities = (
        len(set(identities)) == len(runs) and all(identities)
    )
    clean = sum(1 for item in runs if item.get("ok"))
    ok = bool(
        len(runs) == 3
        and all(item.get("ok") for item in runs)
        and distinct_runs
        and distinct_identities
    )
    return {
        "clean_starts": clean,
        "distinct_runs": distinct_runs,
        "distinct_identities": distinct_identities,
        "ok": ok,
    }


def resolve_failures(run: Path) -> list[dict]:
    """Returns failed/malformed resolve records from an opt-in sequence trace.

    A resolve that failed must not be treated as an entirely successful scene
    just because no draw was refused. Absence of the trace is not a failure;
    the trace is opt-in.
    """
    path = run / "resolve-trace.jsonl"
    if not path.is_file():
        return []
    failures: list[dict] = []
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeError):
        return [{"status": "unreadable_trace"}]
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            failures.append({"line": index, "status": "malformed_trace"})
            continue
        if not isinstance(record, dict):
            failures.append({"line": index, "status": "malformed_trace"})
            continue
        if record.get("status") not in ("copy_ok", "scaled_ok"):
            failures.append(record)
    return failures[:8]


def resolve_trace_present(run: Path) -> bool:
    path = run / "resolve-trace.jsonl"
    if not path.is_file():
        return False
    try:
        return any(line.strip() for line in path.read_text().splitlines())
    except (OSError, UnicodeError):
        return False


def check_run(run: Path, reference: np.ndarray | None, reference_error: str,
              policy: dict, require_resolve_trace: bool = False) -> dict:
    logical = tuple(policy["reference"]["logical"])
    result: dict = {"run": str(run), "status": {}, "checks": {}, "stats": {},
                    "canonical": str(run.resolve()), "identity": "", "ok": False}
    status = result["status"]
    checks = result["checks"]

    probe_path = run / "probe-result.json"
    if not probe_path.is_file():
        result["reason"] = "no probe-result.json"
        return result
    probe = json.loads(probe_path.read_text())
    result["identity"] = run_identity(run, probe)
    probe_status = probe.get("status") or {}
    refused = sorted(run.glob("draw-refused-*.json"))

    checks["probe_captured"] = probe.get("overall") == "captured"
    checks["draw_reached"] = bool(probe_status.get("draw_reached"))
    checks["draw_encoded"] = bool(probe_status.get("draw_encoded"))
    checks["pixels_captured"] = bool(probe_status.get("pixels_captured"))
    checks["no_refused_draws"] = not refused
    failed_resolves = resolve_failures(run)
    checks["no_failed_resolves"] = not failed_resolves
    checks["resolve_trace_present"] = resolve_trace_present(run)
    executed_checks = ["probe_captured", "draw_reached", "draw_encoded",
                       "pixels_captured", "no_refused_draws",
                       "no_failed_resolves"]
    if require_resolve_trace:
        executed_checks.append("resolve_trace_present")
    status["executed"] = all(checks[key] for key in executed_checks)
    if failed_resolves:
        result["resolve_failures"] = failed_resolves
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
        structure = map_structure_ok(window, policy)
        reference_window = reference[y0:y1, x0:x1]
        color_similarity = map_color_similarity(window, reference_window)
        min_color = float(policy.get("map_min_color_similarity", 0.85))
        details["map_unique_colors"] = map_unique
        details["map_mean"] = round(map_mean, 3)
        details["map_structure"] = structure
        details["map_color_similarity"] = round(color_similarity, 3)
        map_ok = (
            map_unique >= int(policy["map_min_unique_colors"])
            and map_mean >= float(policy["map_min_mean"])
            and map_unique < int(0.5 * (y1 - y0) * (x1 - x0))
            and structure
            and color_similarity >= min_color
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
    parser.add_argument("--require-resolve-trace", action="store_true")
    args = parser.parse_args(argv)
    policy = json.loads(Path(args.policy).read_text())
    logical = tuple(policy["reference"]["logical"])
    reference, reference_error = load_reference(Path(args.reference), logical)
    runs = [check_run(Path(item), reference, reference_error, policy,
                      require_resolve_trace=args.require_resolve_trace)
            for item in args.run]
    summary = summarize_runs(runs)
    report = {
        "schema": "m6-live-acceptance/v1",
        "reference": str(args.reference),
        "reference_ok": reference is not None,
        "reference_error": reference_error,
        "runs": runs,
        "clean_starts": summary["clean_starts"],
        "distinct_runs": summary["distinct_runs"],
        "distinct_identities": summary["distinct_identities"],
        "ok": summary["ok"],
    }
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    print(text)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
