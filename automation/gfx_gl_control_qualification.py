"""Qualify an original-API GL control before it can blame Metal.

A one-draw GL control is only admissible causal evidence when it reproduces
live GL exactly on all four BGRA channels.  A small residual (including the
historical four-pixel residual) leaves the control unqualified: the missing
input or drawable state may still explain the difference, so a Metal-vs-control
residual cannot be attributed to the renderer.

This module is deliberately independent of the renderer.  It reports the exact
statistics, keeps a locator for both images, and names the smallest missing
observation when the control is not exact.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from automation import m6_compare

SCHEMA = "gfx-gl-control-qualification/v1"


class QualificationError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _missing(drawable: dict | None, prestate: dict | None) -> list[str]:
    missing: list[str] = []
    if not isinstance(drawable, dict) or not drawable.get("observed"):
        missing.append(
            "default-framebuffer drawable properties (GL_SAMPLES, "
            "GL_SAMPLE_BUFFERS, depth/stencil/color bit depths) were not "
            "captured for this seal")
    else:
        for field in ("samples", "sample_buffers", "depth_bits",
                      "stencil_bits", "red_bits", "green_bits", "blue_bits",
                      "alpha_bits"):
            if drawable.get(field) is None:
                missing.append(f"drawable {field} is not recorded")
    if not isinstance(prestate, dict) or not prestate:
        missing.append(
            "authentic pre-draw depth/stencil/color prestate is not declared")
    else:
        for field in ("depth_clear", "stencil_clear", "color"):
            if field not in prestate:
                missing.append(f"pre-draw {field} is not declared")
    return missing


def qualify(live_path: Path | str, control_path: Path | str,
            width: int, height: int, *, drawable: dict | None = None,
            prestate: dict | None = None) -> dict:
    """Return exact four-channel control qualification.

    ``qualified`` is true only for a complete, dimensionally consistent
    comparison with zero mismatched pixels, zero maximum channel error and zero
    alpha differences.  Everything else remains unqualified, with the missing
    observation that should be captured or reconstructed next.
    """
    live_path = Path(live_path)
    control_path = Path(control_path)
    if width <= 0 or height <= 0:
        raise QualificationError("invalid_extent", f"{width}x{height}")
    live = live_path.read_bytes()
    control = control_path.read_bytes()
    expected = width * height * 4
    if len(live) != expected or len(control) != expected:
        raise QualificationError(
            "invalid_payload",
            f"expected {expected} BGRA bytes, live={len(live)} "
            f"control={len(control)}")
    statistics = m6_compare.compare_images(live, control, width, height)
    exact = bool(
        statistics["mismatched_pixels"] == 0
        and statistics["max_channel_error"] == 0
        and statistics["alpha_different_pixels"] == 0)
    missing = [] if exact else _missing(drawable, prestate)
    return {
        "schema": SCHEMA,
        "status": "qualified" if exact else "unqualified",
        "qualified": exact,
        "width": width,
        "height": height,
        "live": {"path": str(live_path), "sha256": _sha(live_path)},
        "control": {"path": str(control_path),
                    "sha256": _sha(control_path)},
        "statistics": statistics,
        "missing_observation": missing,
        "next_discriminator": (
            None if exact else
            (missing[0] if missing else
             "reduce the residual by varying one captured input at a time")),
        "metal_blame_allowed": exact,
    }


def attribution(qualification: dict) -> dict:
    """Project qualification into the only permitted blame decision."""
    if not isinstance(qualification, dict):
        raise QualificationError("invalid_qualification")
    allowed = (qualification.get("status") == "qualified"
               and qualification.get("qualified") is True
               and qualification.get("metal_blame_allowed") is True)
    return {
        "schema": "gfx-metal-blame/v1",
        "allowed": allowed,
        "reason": (
            "the original-API control reproduces live GL exactly"
            if allowed else
            "the control is not exact; do not attribute the residual to Metal"),
        "qualification": qualification,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare an original-API GL control with live GL exactly.")
    parser.add_argument("live")
    parser.add_argument("control")
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--drawable", help="JSON drawable record")
    parser.add_argument("--prestate", help="JSON pre-draw state record")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    drawable = json.loads(Path(args.drawable).read_text()) if args.drawable else None
    prestate = json.loads(Path(args.prestate).read_text()) if args.prestate else None
    value = qualify(args.live, args.control, args.width, args.height,
                    drawable=drawable, prestate=prestate)
    text = json.dumps(value, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    else:
        print(text, end="")
    return 0 if value["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
