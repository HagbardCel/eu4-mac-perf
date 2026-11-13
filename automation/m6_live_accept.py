#!/usr/bin/env python3
"""Independent acceptance check for the M6 live title-scene runs.

Validates, for each given probe output directory:
  * the probe captured at the menu checkpoint (draw reached/encoded, GPU
    completed, pixels captured, presented);
  * no live draw was refused (no draw-refused-*.json);
  * the presented backbuffer dump exists and is non-blank;
  * the title UI region matches the authentic GL reference within a bounded
    tolerance (the animated map region is excluded).

Usage:
    python3 -m automation.m6_live_accept --reference <menu frame dir> \
        --run <probe output> [--run ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

# The title UI lives in the lower third of the 1728x1117 logical frame; the
# logo occupies the top-left. The map between them animates, so it is excluded
# from the presentation comparison.
UI_REGIONS = (
    (900, 1117, 0, 1728),      # bottom bar (buttons, version, icon row)
    (40, 260, 0, 760),         # EU4 logo
    (40, 260, 1580, 1728),     # Paradox logo
)
UI_MEAN_ERROR_LIMIT = 40.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_logical(path: Path) -> np.ndarray | None:
    from PIL import Image

    try:
        image = Image.open(path).convert("RGB")
    except OSError:
        return None
    return np.asarray(image.resize((1728, 1117), Image.LANCZOS), dtype=np.int16)


def _reference(reference: Path) -> np.ndarray | None:
    path = reference / "frame-reference.bgra"
    if not path.is_file():
        return None
    data = np.fromfile(path, dtype=np.uint8)
    if data.size != 1728 * 1117 * 4:
        return None
    picture = data.reshape(1117, 1728, 4)
    return picture[::-1, :, [2, 1, 0]].astype(np.int16)


def check_run(run: Path, reference: np.ndarray | None) -> dict:
    result: dict = {"run": str(run), "checks": {}, "ok": False}
    probe_path = run / "probe-result.json"
    if not probe_path.is_file():
        result["reason"] = "no probe-result.json"
        return result
    probe = json.loads(probe_path.read_text())
    status = probe.get("status") or {}
    required = ("draw_reached", "draw_encoded", "gpu_completed",
                "pixels_captured", "presented")
    checks = result["checks"]
    checks["overall_captured"] = probe.get("overall") == "captured"
    for key in required:
        checks[key] = bool(status.get(key))
    refused = sorted(run.glob("draw-refused-*.json"))
    checks["no_refused_draws"] = not refused
    dump = run / "swap-frame.bgra"
    checks["swap_dump"] = dump.is_file() and dump.stat().st_size == 3456 * 2234 * 4
    stats = {}
    if checks["swap_dump"]:
        from PIL import Image

        data = np.fromfile(dump, dtype=np.uint8).reshape(2234, 3456, 4)
        # The backend writes the dump in the captured GL lower-left origin, the
        # same convention as the reference; no flip here.
        picture = data[:, :, [2, 1, 0]].astype(np.int16)
        logical = np.asarray(
            Image.fromarray(picture.astype(np.uint8)).resize(
                (1728, 1117), Image.LANCZOS
            ),
            dtype=np.int16,
        )
        stats["unique_colors"] = int(len(np.unique(picture.reshape(-1, 3), axis=0)))
        checks["non_blank"] = stats["unique_colors"] > 1000
        checks["dump_sha256"] = _sha256(dump)
        if reference is not None:
            errors = []
            for y0, y1, x0, x1 in UI_REGIONS:
                errors.append(
                    float(np.abs(logical[y0:y1, x0:x1] - reference[y0:y1, x0:x1]).mean())
                )
            stats["ui_region_mean_error"] = errors
            stats["ui_max_region_error"] = max(errors)
            checks["ui_matches_reference"] = (
                max(errors) <= UI_MEAN_ERROR_LIMIT
            )
        result["stats"] = stats
    result["ok"] = all(checks.values())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--run", action="append", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    reference = _reference(Path(args.reference))
    runs = [check_run(Path(item), reference) for item in args.run]
    report = {
        "schema": "m6-live-acceptance/v1",
        "reference": str(args.reference),
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
