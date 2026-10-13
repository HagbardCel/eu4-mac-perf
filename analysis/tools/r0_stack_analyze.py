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

# Decoration symbols with flexible spacing before count.
_WEIGHTED_ROW = re.compile(r"^(\s*)((?:[\+\!\:\|]\s*)*)(\d+)\s+(.+)$")
_THREAD_HEADER_COUNT = re.compile(r"^\s+(\d+)\s+Thread_", re.IGNORECASE)


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


def _insert_path_strict(root: StackNode, symbols: list[str], count: int) -> None:
    if not symbols:
        raise ValueError("empty stack path")
    node = root
    for index, symbol in enumerate(symbols):
        child = node.children.get(symbol)
        if child is None:
            child = StackNode()
            node.children[symbol] = child
        if index == len(symbols) - 1:
            if child.inclusive != 0 and child.inclusive != count:
                raise ValueError(
                    f"conflicting inclusive weight for {symbol!r}: "
                    f"{child.inclusive} vs {count}"
                )
            child.inclusive = count
        node = child


def _finalize_inclusive(node: StackNode) -> int:
    """Legacy helper for tests that explicitly need repaired trees."""
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


def _count_column(raw: str, count_group_start: int) -> int:
    return len(raw[:count_group_start].expandtabs(4))


def parse_weighted_frame_line(raw: str) -> tuple[int, int, str]:
    line = raw.rstrip()
    match = _WEIGHTED_ROW.match(line)
    if not match:
        raise ValueError(f"unsupported sample call-graph line: {raw!r}")
    count_s, symbol = match.group(3), match.group(4).strip()
    depth = _count_column(line, match.start(3))
    return depth, int(count_s), symbol


def partition_sample_call_graph(text: str) -> tuple[list[str], list[str]]:
    """Split sample text into preamble (outside call graph) and call-graph body lines."""
    preamble: list[str] = []
    call_graph: list[str] = []
    in_call_graph = False
    ended = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if ended:
            preamble.append(raw)
            continue
        if stripped.startswith("Call graph:"):
            in_call_graph = True
            continue
        if not in_call_graph:
            if _WEIGHTED_ROW.match(raw.rstrip()):
                in_call_graph = True
                call_graph.append(raw)
            else:
                preamble.append(raw)
            continue
        if stripped.startswith("Total number in stack") or stripped.startswith("Binary Images:"):
            ended = True
            preamble.append(raw)
            continue
        if stripped == "":
            continue
        call_graph.append(raw)
    return preamble, call_graph


def call_graph_region_lines(text: str) -> list[str]:
    """Non-empty lines inside the call-graph section (weighted rows and peers)."""
    _, call_graph = partition_sample_call_graph(text)
    return call_graph


def enumerate_weighted_call_graph_lines(text: str) -> list[str]:
    """Return raw lines that must parse as weighted stack rows in order."""
    rows: list[str] = []
    for raw in call_graph_region_lines(text):
        if not _WEIGHTED_ROW.match(raw.rstrip()):
            raise ValueError(f"unsupported sample call-graph line: {raw!r}")
        rows.append(raw)
    return rows


def _parse_frame_lines(text: str) -> list[tuple[int, int, str]]:
    lines: list[tuple[int, int, str]] = []
    for raw in enumerate_weighted_call_graph_lines(text):
        lines.append(parse_weighted_frame_line(raw))
    return lines


def build_raw_tree(lines: list[tuple[int, int, str]]) -> StackNode:
    root = StackNode()
    path: list[tuple[int, str]] = []
    for depth, count, symbol in lines:
        while path and path[-1][0] >= depth:
            path.pop()
        path.append((depth, symbol))
        _insert_path_strict(root, [sym for _, sym in path], count)
    return root


def validate_raw_parent_child_weights(root: StackNode) -> None:
    def walk(node: StackNode) -> None:
        if node.inclusive > 0 and node.children:
            child_sum = sum(child.inclusive for child in node.children.values())
            if child_sum > node.inclusive:
                raise ValueError(
                    "invalid inclusive weights: children sum "
                    f"{child_sum} exceeds parent {node.inclusive}"
                )
        for child in node.children.values():
            walk(child)

    for child in root.children.values():
        walk(child)


def build_tree(lines: list[tuple[int, int, str]]) -> StackNode:
    """Production tree build: strict raw tree + validation, no silent repair."""
    root = build_raw_tree(lines)
    validate_raw_parent_child_weights(root)
    return root


def assert_tree_exclusive_nonnegative(root: StackNode) -> None:
    def walk(node: StackNode) -> None:
        _exclusive(node)
        for child in node.children.values():
            walk(child)

    for child in root.children.values():
        walk(child)


_THREAD_DECIMAL_HEADER = re.compile(r"Thread_(\d+)", re.IGNORECASE)
_THREAD_HEX_HEADER = re.compile(r"Thread\s+0x([0-9a-f]+)", re.IGNORECASE)


def thread_header_id(line: str) -> tuple[str, int] | None:
    match = _THREAD_DECIMAL_HEADER.search(line)
    if match:
        return ("decimal", int(match.group(1)))
    match = _THREAD_HEX_HEADER.search(line)
    if match:
        return ("hex", int(match.group(1), 16))
    return None


_thread_header_id = thread_header_id


def _thread_header_sample_count(text: str) -> int | None:
    for line in text.splitlines():
        match = _THREAD_HEADER_COUNT.match(line.rstrip())
        if match:
            return int(match.group(1))
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
    return any(_WEIGHTED_ROW.match(line.rstrip()) for line in text.splitlines())


def analyze_sample_text(text: str, *, main_thread_id: int | None = None) -> dict[str, Any]:
    text = extract_main_thread_section(text, main_thread_id)
    has_frames = _has_weighted_frame_lines(text)
    if "Call graph" not in text and not has_frames:
        raise ValueError("unsupported sample format: missing Call graph or weighted frame lines")
    if "Call graph" not in text and has_frames:
        text = "Call graph:\n" + text
    weighted_rows = enumerate_weighted_call_graph_lines(text)
    if not weighted_rows:
        raise ValueError("unsupported sample format: no weighted stacks parsed")
    lines = [parse_weighted_frame_line(raw) for raw in weighted_rows]
    root = build_tree(lines)
    assert_tree_exclusive_nonnegative(root)
    functional: Counter[str] = Counter()
    execution: Counter[str] = Counter()
    cross: Counter[tuple[str, str]] = Counter()
    attributed = 0
    for symbol, child in root.children.items():
        attributed += _attribute(child, [symbol], functional, execution, cross)
    root_inclusive = sum(child.inclusive for child in root.children.values())
    header_count = _thread_header_sample_count(text)
    if header_count is not None and root_inclusive != header_count:
        raise ValueError(
            "stack conservation failed: root inclusive weight "
            f"{root_inclusive} != thread header sample count {header_count}"
        )
    total = sum(functional.values()) or 1
    if attributed != root_inclusive and root.children:
        raise ValueError("stack conservation failed: attributed weight mismatch")
    unattributed = functional.get("unattributed", 0)
    return {
        "functional_caller": {key: value / total for key, value in functional.items()},
        "execution_location": {key: value / total for key, value in execution.items()},
        "cross_tab_counts": {f"{a}|{b}": count for (a, b), count in cross.items()},
        "functional_caller_counts": dict(functional),
        "execution_location_counts": dict(execution),
        "unattributed_sample_weight": unattributed,
        "unattributed_sample_fraction": unattributed / total if total else 0.0,
        "attributed_sample_weight": attributed,
        "root_inclusive": root_inclusive,
        "weighted_row_count": len(weighted_rows),
        "rules": {
            "note": "sample-weight proportions only; not CPU-ms/s. "
            "Depth from count column; no silent inclusive repair.",
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
