"""Half-open byte interval unions for machine-code size accounting."""

from __future__ import annotations

from typing import Iterable, Sequence


def parse_rva(value: str | int) -> int:
    if isinstance(value, int):
        return value
    text = value.strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return int(text, 16)


def format_rva(rva: int, width: int = 16) -> str:
    return f"0x{rva:0{width}x}"


def merge_intervals(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge half-open [start, end) intervals."""
    sorted_pairs = sorted((start, end) for start, end in intervals if end > start)
    if not sorted_pairs:
        return []
    merged: list[tuple[int, int]] = [sorted_pairs[0]]
    for start, end in sorted_pairs[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def union_size(intervals: Iterable[tuple[int, int]]) -> int:
    return sum(end - start for start, end in merge_intervals(intervals))


def intervals_from_ranges(ranges: Sequence[dict]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for item in ranges:
        start = parse_rva(item["start_rva"])
        end = parse_rva(item["end_rva"])
        out.append((start, end))
    return out
