#!/usr/bin/env python3
"""Weighted main-thread stack attribution from sample(1) text output."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass, field
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


@dataclass
class StackNode:
    inclusive: int = 0
    children: dict[str, StackNode] = field(default_factory=dict)


def classify_path(symbols: list[str], rules: list[tuple[str, str]]) -> str:
    for symbol in reversed(symbols):
        for pattern, label in rules:
            if re.search(pattern, symbol):
                return label
    return "unattributed"


def _exclusive(node: StackNode) -> int:
    child_sum = sum(child.inclusive for child in node.children.values())
    weight = node.inclusive - child_sum
    if weight < 0:
        raise ValueError("inconsistent inclusive tree: negative exclusive weight")
    return weight


def _insert_path(root: StackNode, symbols: list[str], count: int) -> None:
    node = root
    for index, symbol in enumerate(symbols):
        child = node.children.setdefault(symbol, StackNode())
        if index == len(symbols) - 1:
            child.inclusive = max(child.inclusive, count)
        node = child


def _finalize_inclusive(node: StackNode) -> int:
    if not node.children:
        return node.inclusive
    child_max = max(_finalize_inclusive(child) for child in node.children.values())
    node.inclusive = max(node.inclusive, child_max)
    return node.inclusive


def _attribute(node: StackNode, path: list[str], functional: Counter, execution: Counter, cross: Counter) -> int:
    weight = _exclusive(node)
    if weight:
        func = classify_path(path, FUNCTIONAL_RULES)
        exec_loc = classify_path(path, EXECUTION_RULES)
        functional[func] += weight
        execution[exec_loc] += weight
        cross[(func, exec_loc)] += weight
    total = weight
    for symbol, child in node.children.items():
        total += _attribute(child, path + [symbol], functional, execution, cross)
    return total


def _parse_frame_lines(text: str) -> list[tuple[int, int, str]]:
    lines: list[tuple[int, int, str]] = []
    for raw in text.splitlines():
        match = _FRAME_LINE.match(raw.rstrip())
        if not match:
            continue
        indent, count_s, symbol = match.groups()
        depth = len(indent.expandtabs(4))
        lines.append((depth, int(count_s), symbol.strip()))
    return lines


def build_tree(lines: list[tuple[int, int, str]]) -> StackNode:
    root = StackNode()
    path: list[tuple[int, str]] = []
    for depth, count, symbol in lines:
        while path and path[-1][0] >= depth:
            path.pop()
        path.append((depth, symbol))
        _insert_path(root, [sym for _, sym in path], count)
    _finalize_inclusive(root)
    return root


_THREAD_DECIMAL_HEADER = re.compile(r"Thread_(\d+)", re.IGNORECASE)
_THREAD_HEX_HEADER = re.compile(r"Thread\s+0x([0-9a-f]+)", re.IGNORECASE)


def _thread_header_id(line: str) -> tuple[str, int] | None:
    match = _THREAD_DECIMAL_HEADER.search(line)
    if match:
        return ("decimal", int(match.group(1)))
    match = _THREAD_HEX_HEADER.search(line)
    if match:
        return ("hex", int(match.group(1), 16))
    return None


def extract_main_thread_section(text: str, main_thread_id: int | None) -> str:
    if main_thread_id is None:
        return text
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        parsed = _thread_header_id(line)
        if parsed and parsed[1] == main_thread_id:
            start = index
            break
    if start is None:
        raise ValueError("main thread section not found in sample output")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("Binary Images:"):
            end = index
            break
        if stripped.startswith("Total number in stack"):
            end = index
            break
        parsed = _thread_header_id(lines[index])
        if parsed and parsed[1] != main_thread_id:
            end = index
            break
    return "\n".join(lines[start:end])


def _has_weighted_frame_lines(text: str) -> bool:
    return any(_FRAME_LINE.match(line.rstrip()) for line in text.splitlines())


def analyze_sample_text(text: str, *, main_thread_id: int | None = None) -> dict[str, Any]:
    text = extract_main_thread_section(text, main_thread_id)
    has_frames = _has_weighted_frame_lines(text)
    if "Call graph" not in text and not has_frames:
        raise ValueError("unsupported sample format: missing Call graph or weighted frame lines")
    if "Call graph" not in text and has_frames:
        text = "Call graph:\n" + text
    lines = _parse_frame_lines(text)
    if not lines:
        raise ValueError("unsupported sample format: no weighted stacks parsed")
    root = build_tree(lines)
    functional: Counter[str] = Counter()
    execution: Counter[str] = Counter()
    cross: Counter[tuple[str, str]] = Counter()
    attributed = 0
    for symbol, child in root.children.items():
        attributed += _attribute(child, [symbol], functional, execution, cross)
    root_inclusive = sum(child.inclusive for child in root.children.values())
    total = sum(functional.values()) or 1
    if attributed != root_inclusive and root.children:
        raise ValueError("stack conservation failed: attributed weight mismatch")
    return {
        "functional_caller": {key: value / total for key, value in functional.items()},
        "execution_location": {key: value / total for key, value in execution.items()},
        "cross_tab_counts": {f"{a}|{b}": count for (a, b), count in cross.items()},
        "attributed_sample_weight": attributed,
        "root_inclusive": root_inclusive,
        "rules": {
            "note": "branched inclusive tree; path-based cross-tab per exclusive node",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sample_file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--main-thread-id", type=int)
    args = parser.parse_args()
    report = analyze_sample_text(
        args.sample_file.read_text(errors="replace"), main_thread_id=args.main_thread_id
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
