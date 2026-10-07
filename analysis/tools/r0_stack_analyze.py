#!/usr/bin/env python3
"""Two-dimensional main-thread stack attribution from sample(1) text output."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

FUNCTIONAL_RULES: list[tuple[str, str]] = [
    (r"CPdxMeshObject::RenderBuckets", "mesh_render_buckets"),
    (r"CPdxMapBorderLayer::DrawBorders", "border_draw"),
    (r"GfxDrawIndexed", "gfx_draw_indexed"),
    (r"GfxDraw", "gfx_draw_other"),
    (r"CInGameIdler::Render", "ingame_render"),
]

EXECUTION_RULES: list[tuple[str, str]] = [
    (r"GLDContextRec::setRenderState", "gl_driver_set_render_state"),
    (r"glDrawElements", "gl_draw_elements"),
    (r"AppleMetalOpenGLRenderer", "apple_metal_gl_renderer"),
    (r"GLEngine", "gl_engine"),
    (r"NSCursor", "nscursor"),
]


def classify_line(line: str, rules: list[tuple[str, str]]) -> str:
    for pattern, label in rules:
        if re.search(pattern, line):
            return label
    return "unattributed"


def analyze_sample_text(text: str) -> dict[str, Any]:
    functional = Counter()
    execution = Counter()
    cross = Counter()
    for raw in text.splitlines():
        if not raw.strip():
            continue
        func = classify_line(raw, FUNCTIONAL_RULES)
        exec_loc = classify_line(raw, EXECUTION_RULES)
        functional[func] += 1
        execution[exec_loc] += 1
        cross[(func, exec_loc)] += 1
    total = sum(functional.values()) or 1
    return {
        "functional_caller": {key: value / total for key, value in functional.items()},
        "execution_location": {key: value / total for key, value in execution.items()},
        "cross_tab_counts": {f"{a}|{b}": count for (a, b), count in cross.items()},
        "sample_lines": total,
        "rules": {
            "functional_precedence": [label for _, label in FUNCTIONAL_RULES],
            "execution_precedence": [label for _, label in EXECUTION_RULES],
            "note": "each dimension is mutually exclusive by first matching rule",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sample_file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze_sample_text(args.sample_file.read_text(errors="replace"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
