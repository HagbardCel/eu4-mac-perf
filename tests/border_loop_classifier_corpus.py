"""Shared border classifier cases for Python/C parity (table adapter path)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from border_loop_classifier import IndexTableEntry, LoopContext, RecordView, classify_batchable_prefix

IboTopology = Literal["ONE_BIND", "ZERO_BIND"]


@dataclass
class ClassifierCorpusCase:
    name: str
    ctx: LoopContext
    records: list[RecordView]
    ibos: list[int]
    entries: list[IndexTableEntry]
    walk_exhausted: bool = True
    adapter_only: bool = False
    ibo_bind_topology: IboTopology = "ONE_BIND"
    expected_semantic: dict[str, Any] = field(default_factory=dict)
    expected_implementable: dict[str, Any] = field(default_factory=dict)

    def run_python(self) -> tuple[dict[str, Any], dict[str, Any]]:
        sem = classify_batchable_prefix(
            self.ctx,
            self.records,
            self.ibos,
            self.entries,
            ibo_bind_topology=self.ibo_bind_topology,
            max_scan_steps=None,
            walk_exhausted=self.walk_exhausted,
        )
        impl = classify_batchable_prefix(
            self.ctx,
            self.records,
            self.ibos,
            self.entries,
            ibo_bind_topology=self.ibo_bind_topology,
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


def _tables(n: int, vbo: int = 1, ibo: int = 100) -> tuple[list[RecordView], list[int]]:
    records = [RecordView(10 + i, 4, vbo) for i in range(n)]
    ibos = [ibo] * n
    return records, ibos


def _entries(indices: list[int], color: int = 5, vis: int = 3) -> list[IndexTableEntry]:
    return [IndexTableEntry(i, vis, color) for i in indices]


def build_corpus() -> list[ClassifierCorpusCase]:
    cases: list[ClassifierCorpusCase] = []

    records8, ibos8 = _tables(8)
    cases.append(
        ClassifierCorpusCase(
            "homogeneous_prefix_of_five",
            _ctx(),
            records8,
            ibos8,
            _entries([0, 1, 2, 3, 4]),
        )
    )

    records4 = [RecordView(1, 4, 1), RecordView(2, 4, 1), RecordView(3, 4, 1), RecordView(4, 4, 2)]
    cases.append(
        ClassifierCorpusCase(
            "vbo_barrier_at_three",
            _ctx(),
            records4,
            [100] * 4,
            _entries([0, 1, 2, 3]),
        )
    )

    records5, ibos5 = _tables(5)
    cases.append(
        ClassifierCorpusCase(
            "three_safe_then_skip",
            _ctx(),
            records5,
            ibos5,
            [
                IndexTableEntry(0, 3, 5),
                IndexTableEntry(1, 3, 5),
                IndexTableEntry(2, 3, 5),
                IndexTableEntry(3, 0, 5),
            ],
        )
    )

    cases.append(
        ClassifierCorpusCase(
            "three_safe_then_zero_triangles",
            _ctx(),
            [RecordView(1, 4, 1), RecordView(2, 4, 1), RecordView(3, 4, 1), RecordView(4, 0, 1)],
            [100] * 4,
            _entries([0, 1, 2, 3]),
        )
    )

    ibos_alias = [0x0000000112345678] * 3 + [0x0000000212345678]
    cases.append(
        ClassifierCorpusCase(
            "ibo_high_bits",
            _ctx(),
            [RecordView(1, 4, 1)] * 4,
            ibos_alias,
            _entries([0, 1, 2, 3]),
        )
    )

    records4b, ibos4b = _tables(4, ibo=10)
    ibos4b[3] = 99
    cases.append(
        ClassifierCorpusCase(
            "ibo_boundary",
            _ctx(),
            records4b,
            ibos4b,
            _entries([0, 1, 2, 3]),
        )
    )

    cases.append(
        ClassifierCorpusCase(
            "color_and_vbo_boundary",
            _ctx(),
            [RecordView(1, 4, 1), RecordView(2, 4, 1), RecordView(3, 4, 2)],
            [100] * 3,
            [IndexTableEntry(0, 3, 5), IndexTableEntry(1, 3, 5), IndexTableEntry(2, 3, 9)],
        )
    )

    records3, ibos3 = _tables(3)
    cases.append(
        ClassifierCorpusCase(
            "walk_end_terminal",
            _ctx(),
            records3,
            ibos3,
            _entries([0, 1, 2]),
            walk_exhausted=True,
        )
    )

    records200, ibos200 = _tables(200)
    cases.append(
        ClassifierCorpusCase(
            "scan_cap_not_walk_end",
            _ctx(),
            records200,
            ibos200,
            _entries(list(range(200))),
            walk_exhausted=False,
        )
    )

    records2, ibos2 = _tables(2)
    cases.append(
        ClassifierCorpusCase(
            "oob_record_index",
            _ctx(),
            records2,
            ibos2,
            [IndexTableEntry(99, 3, 5)],
        )
    )

    cases.append(
        ClassifierCorpusCase(
            "null_ibo_homogeneous",
            _ctx(),
            _tables(4)[0],
            [0, 0, 0, 0],
            _entries([0, 1, 2, 3]),
        )
    )

    records3z, ibos3z = _tables(3)
    cases.append(
        ClassifierCorpusCase(
            "zero_bind_ibo_mismatch",
            _ctx(bound_ibo_identity=50, ibo_known=True),
            records3z,
            ibos3z,
            _entries([0, 1]),
            ibo_bind_topology="ZERO_BIND",
        )
    )

    cases.append(
        ClassifierCorpusCase(
            "unsupported_mode",
            _ctx(mode=1),
            _tables(3)[0],
            _tables(3)[1],
            _entries([0, 1]),
        )
    )

    cases.append(
        ClassifierCorpusCase(
            "negative_outer_batch_adapter",
            _ctx(outer_batch_index=-1),
            records8[:4],
            ibos8[:4],
            _entries([0, 1, 2, 3]),
            adapter_only=True,
        )
    )

    for case in cases:
        if not case.expected_semantic:
            sem, impl = case.run_python()
            case.expected_semantic = sem
            case.expected_implementable = impl
    return cases


def parity_cases() -> list[ClassifierCorpusCase]:
    return [c for c in build_corpus() if not c.adapter_only]


def write_c_parity_fixture(path: Path) -> None:
    cases = parity_cases()
    lines: list[str] = [
        "/* Generated by border_loop_classifier_corpus.write_c_parity_fixture — do not edit. */",
        "#ifndef BORDER_LOOP_CLASSIFIER_PARITY_CASES_H",
        "#define BORDER_LOOP_CLASSIFIER_PARITY_CASES_H",
        "#include <stdbool.h>",
        "#include <stdint.h>",
        "#include \"border_loop_classifier.h\"",
        "",
        "typedef struct {",
        "    const char *name;",
        "    eu4_border_loop_context_t ctx;",
        "    const eu4_border_record_view_t *records;",
        "    const uintptr_t *ibos;",
        "    const eu4_border_index_entry_t *entries;",
        "    uint32_t record_count;",
        "    uint32_t side_count;",
        "    eu4_border_ibo_topology_t ibo_topology;",
        "    bool walk_exhausted;",
        "} eu4_border_parity_case_t;",
        "",
    ]

    for i, case in enumerate(cases):
        lines.append(f"static const eu4_border_record_view_t case{i}_records[] = {{")
        for r in case.records:
            lines.append(
                f"    {{ .arg3_u16_at_plus_04 = {r.arg3_u16_at_plus_04}u, "
                f".triangle_count = {r.triangle_count}u, .vbo_table_index = {r.vbo_table_index}u }},"
            )
        lines.append("};")
        ibo_literals = [f"0x{ibo:x}ull" if ibo > 0xFFFFFFFF else str(ibo) for ibo in case.ibos]
        lines.append(f"static const uintptr_t case{i}_ibos[] = {{ {', '.join(ibo_literals)} }};")
        lines.append(f"static const eu4_border_index_entry_t case{i}_entries[] = {{")
        for e in case.entries:
            lines.append(f"    {{ .record_index = {e.record_index}, .visibility_byte = {e.visibility_byte}, "
                         f".color_byte = {e.color_byte} }},"
            )
        lines.append("};")
        ctx = case.ctx
        topo = "EU4_BORDER_IBO_ZERO_BIND" if case.ibo_bind_topology == "ZERO_BIND" else "EU4_BORDER_IBO_ONE_BIND"
        lines.append(f"static const eu4_border_parity_case_t case{i}_meta = {{")
        lines.append(f'    "{case.name}",')
        lines.append("    {")
        lines.append(f"        .mode = {ctx.mode}u,")
        lines.append(f"        .cached_color = {ctx.cached_color}u,")
        lines.append(f"        .cached_vbo_index = {ctx.cached_vbo_index}u,")
        lines.append(f"        .skip_or_visibility_mask = {ctx.skip_or_visibility_mask}u,")
        lines.append(f"        .outer_batch_index = {ctx.outer_batch_index & 0xFFFFFFFF}u,")
        lines.append(f"        .bound_ibo_identity = {ctx.bound_ibo_identity}ull,")
        lines.append(f"        .ibo_known = {'true' if ctx.ibo_known else 'false'},")
        lines.append(f"        .color_known = {'true' if ctx.color_known else 'false'},")
        lines.append(f"        .vbo_known = {'true' if ctx.vbo_known else 'false'},")
        lines.append(f"        .deferred_attrib_upload_pending = {'true' if ctx.deferred_attrib_upload_pending else 'false'},")
        lines.append(f"        .secondary_upload_pending = {'true' if ctx.secondary_upload_pending else 'false'},")
        lines.append("    },")
        lines.append(f"    case{i}_records,")
        lines.append(f"    case{i}_ibos,")
        lines.append(f"    case{i}_entries,")
        lines.append(f"    {len(case.records)}u,")
        lines.append(f"    {len(case.entries)}u,")
        lines.append(f"    {topo},")
        lines.append(f"    {'true' if case.walk_exhausted else 'false'},")
        lines.append("};")

    lines.append("static const eu4_border_parity_case_t *const EU4_BORDER_PARITY_CASES[] = {")
    for i in range(len(cases)):
        lines.append(f"    &case{i}_meta,")
    lines.append("};")
    lines.append(f"#define EU4_BORDER_PARITY_CASE_COUNT {len(cases)}")
    lines.append("")
    lines.append("#endif")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
