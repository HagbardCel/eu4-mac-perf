"""Pure thread CPU delta aggregation (portable tests)."""

from __future__ import annotations

from typing import Any


def accumulate_thread_deltas(
    polls: list[dict[int, int]],
) -> tuple[dict[int, int], int, int]:
    """Each poll is thread_id -> total_us snapshot. Returns deltas, appeared, disappeared counts."""
    if not polls:
        return {}, 0, 0
    baselines: dict[int, int] = {}
    last: dict[int, int] = {}
    appeared = 0
    disappeared = 0
    previous_ids: set[int] = set()
    for snapshot in polls:
        current_ids = set(snapshot)
        appeared += len(current_ids - previous_ids)
        disappeared += len(previous_ids - current_ids)
        for thread_id, total_us in snapshot.items():
            if thread_id not in baselines:
                baselines[thread_id] = total_us
            last[thread_id] = total_us
        previous_ids = current_ids
    accumulated = {
        thread_id: last[thread_id] - baselines[thread_id]
        for thread_id in last
        if last[thread_id] >= baselines[thread_id]
    }
    return accumulated, appeared, disappeared


def split_main_thread_cpu(
    accumulated: dict[int, int], main_thread_id: int | None
) -> tuple[float, float, str]:
    if main_thread_id is None or main_thread_id not in accumulated:
        total = sum(accumulated.values())
        return 0.0, float(total), "unresolved"
    main_us = accumulated[main_thread_id]
    non_main = sum(us for tid, us in accumulated.items() if tid != main_thread_id)
    return float(main_us), float(non_main), "verified"
