#!/usr/bin/env python3
"""Weighted main-thread stack attribution from sample(1) text output."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
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

_FRAME_LINE = re.compile(r"^(\s+)(\d+)\s+(.+)$")


def classify_line(line: str, rules: list[tuple[str, str]]) -> str:
    for pattern, label in rules:
        if re.search(pattern, line):
            return label
    return "unattributed"


def _parse_weighted_stacks(text: str) -> list[list[tuple[str, int]]]:
    if "Call graph" not in text and not _FRAME_LINE.search(text):
        raise ValueError("unsupported sample format: missing Call graph or weighted frame lines")
    stacks: list[list[tuple[str, int]]] = []
    current: list[tuple[int, str, int]] = []
    for raw in text.splitlines():
        match = _FRAME_LINE.match(raw.rstrip())
        if not match:
            continue
        indent, count_s, symbol = match.groups()
        depth = len(indent.expandtabs(4))
        count = int(count_s)
        if current and depth <= current[-1][0]:
            stacks.append([(sym, cnt) for _, sym, cnt in current])
            current = []
        current.append((depth, symbol.strip(), count))
    if current:
        stacks.append([(sym, cnt) for _, sym, cnt in current])
    if not stacks:
        raise ValueError("unsupported sample format: no weighted stacks parsed")
    return stacks


def _exclusive_weights(stack: list[tuple[str, int]]) -> list[tuple[str, int]]:
    weighted: list[tuple[str, int]] = []
    for index, (symbol, count) in enumerate(stack):
        next_count = stack[index + 1][1] if index + 1 < len(stack) else 0
        weight = count - next_count
        if weight > 0:
            weighted.append((symbol, weight))
    return weighted


def analyze_sample_text(text: str) -> dict[str, Any]:
    stacks = _parse_weighted_stacks(text)
    functional: Counter[str] = Counter()
    execution: Counter[str] = Counter()
    cross: Counter[tuple[str, str]] = Counter()
    total_exclusive = 0
    for stack in stacks:
        for symbol, weight in _exclusive_weights(stack):
            total_exclusive += weight
            func = classify_line(symbol, FUNCTIONAL_RULES)
            exec_loc = classify_line(symbol, EXECUTION_RULES)
            functional[func] += weight
            execution[exec_loc] += weight
            cross[(func, exec_loc)] += weight
    total = sum(functional.values()) or 1
    return {
        "functional_caller": {key: value / total for key, value in functional.items()},
        "execution_location": {key: value / total for key, value in execution.items()},
        "cross_tab_counts": {f"{a}|{b}": count for (a, b), count in cross.items()},
        "attributed_sample_weight": total_exclusive,
        "rules": {
            "functional_precedence": [label for _, label in FUNCTIONAL_RULES],
            "execution_precedence": [label for _, label in EXECUTION_RULES],
            "note": "exclusive weights from inclusive stack lines; no double counting",
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
