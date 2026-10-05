"""Shared border classifier cases for Python/C parity (table adapter path)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from border_loop_classifier import IndexTableEntry, LoopContext, RecordView, classify_batchable_prefix


@dataclass
class ClassifierCorpusCase:
    name: str
    ctx: LoopContext
    records: list[RecordView]
    ibos: list[int]
    entries: list[IndexTableEntry]
    max_scan_steps: int | None = None
    walk_exhausted: bool = True
    adapter_only: bool = False
    expected_semantic: dict[str, Any] = field(default_factory=dict)
    expected_implementable: dict[str, Any] = field(default_factory=dict)

    def run_python(self) -> tuple[dict[str, Any], dict[str, Any]]:
        sem = classify_batchable_prefix(
            self.ctx,
            self.records,
            self.ibos,
            self.entries,
            max_scan_steps=None,
            walk_exhausted=self.walk_exhausted,
        )
        impl = classify_batchable_prefix(
            self.ctx,
            self.records,
            self.ibos,
            self.entries,
            max_scan_steps=128,
            walk_exhausted=self.walk_exhausted,
        )
        return _prefix_dict(sem), _prefix_dict(impl)


def _prefix_dict(result) -> dict[str, Any]:
    return {
        "run_length": result.run_length,
        "batch_eligible": result.batch_eligible,
        "elim": result.eligible_draw_calls_eliminable,
        "termination": result.termination_kind.name,
        "stop_reason_mask": int(result.stop_reason_mask),
        "boundary_reason_mask": int(result.boundary_reason_mask),
    }


def _ctx(**kwargs) -> LoopContext:
    defaults = {
        "mode": 0,
        "cached_color": 5,
        "cached_vbo_index": 1,
        "skip_or_visibility_mask": 0x1,
        "outer_batch_index": 8,
        "bound_ibo_identity": 100,
        "ibo_known": True,
    }
    defaults.update(kwargs)
    return LoopContext(**defaults)


def build_corpus() -> list[ClassifierCorpusCase]:
    cases: list[ClassifierCorpusCase] = []
    records8 = [RecordView(10 + i, 4, 1) for i in range(8)]
    ibos8 = [100] * 8
    entries8 = [IndexTableEntry(i, 0xFF, 1) for i in range(8)]
    cases.append(
        ClassifierCorpusCase(
            "homogeneous_eight",
            _ctx(cached_color=1, skip_or_visibility_mask=0xFF),
            records8,
            ibos8,
            entries8,
        )
    )
    records4 = [RecordView(1, 4, 1), RecordView(2, 4, 1), RecordView(3, 4, 1), RecordView(4, 4, 2)]
    cases.append(
        ClassifierCorpusCase(
            "vbo_barrier_at_three",
            _ctx(),
            records4,
            [100] * 4,
            [IndexTableEntry(i, 3, 5) for i in range(4)],
        )
    )
    cases.append(
        ClassifierCorpusCase(
            "negative_outer_batch_adapter",
            _ctx(outer_batch_index=-1),
            records8[:4],
            ibos8[:4],
            [IndexTableEntry(i, 3, 5) for i in range(4)],
            adapter_only=True,
        )
    )
    ibos_alias = [0x0000000112345678] * 3 + [0x0000000212345678]
    cases.append(
        ClassifierCorpusCase(
            "ibo_high_bits",
            _ctx(),
            [RecordView(1, 4, 1)] * 4,
            ibos_alias,
            [IndexTableEntry(i, 3, 5) for i in range(4)],
        )
    )
    for case in cases:
        if not case.expected_semantic:
            sem, impl = case.run_python()
            case.expected_semantic = sem
            case.expected_implementable = impl
    return cases
