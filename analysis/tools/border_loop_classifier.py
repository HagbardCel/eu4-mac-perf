"""Offline prefix classifier for border loop-head multidraw (synthetic contract tests)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntFlag
from typing import Literal

TriState = Literal["match", "mismatch", "unknown"]
IboBindTopology = Literal["ZERO_BIND", "ONE_BIND"]


class BarrierMask(IntFlag):
    SKIP = 1 << 0
    UNSUPPORTED_MODE = 1 << 1
    COLOR_TRANSITION = 1 << 2
    VBO_TRANSITION = 1 << 3
    IBO_TRANSITION = 1 << 4
    OTHER_SIDE_EFFECT = 1 << 5


class TerminationKind(Enum):
    BARRIER = "BARRIER"
    WALK_END = "WALK_END"
    SCAN_CAP = "SCAN_CAP"
    INVALID = "INVALID"


@dataclass
class BatchKey:
    mode: int
    color_state: int
    vbo_table_index: int
    ibo_identity: int
    ibo_argument: int
    index_type: str = "GL_UNSIGNED_SHORT"


@dataclass
class EntryStateMatch:
    color: TriState = "unknown"
    vbo: TriState = "unknown"
    ibo: TriState = "unknown"

    def all_match(self) -> bool:
        return self.color == "match" and self.vbo == "match" and self.ibo == "match"

    def any_unknown(self) -> bool:
        return "unknown" in (self.color, self.vbo, self.ibo)


@dataclass
class LoopContext:
    mode: int
    cached_color: int
    cached_vbo_index: int
    skip_or_visibility_mask: int
    bound_ibo_identity: int = 0
    ibo_known: bool = False
    color_known: bool = True
    vbo_known: bool = True
    deferred_attrib_upload_pending: bool = False
    secondary_upload_pending: bool = False


@dataclass
class IndexTableEntry:
    record_index: int
    visibility_byte: int
    color_byte: int


@dataclass
class RecordView:
    """Per-record SBorderDraw view for classifier homogeneity (not GL gather)."""

    arg3_u16_at_plus_04: int
    triangle_count: int
    vbo_table_index: int


@dataclass
class ResolvedIteration:
    entry: IndexTableEntry
    record: RecordView
    required_ibo_identity: int
    required_ibo_argument: int


@dataclass
class PrefixResult:
    run_length: int
    stop_reason_mask: BarrierMask
    boundary_reason_mask: BarrierMask
    batch_key: BatchKey | None
    entry_state_matches_batch_key: EntryStateMatch
    draws_covered: int
    eligible_draw_calls_eliminable: int
    batch_eligible: bool
    v1_fall_through_recommended: bool
    termination_kind: TerminationKind


def _resolve_drawable(
    entry: IndexTableEntry,
    record_table: list[RecordView],
    ibo_table: list[int],
) -> ResolvedIteration | None:
    idx = entry.record_index
    if idx < 0 or idx >= len(record_table) or idx >= len(ibo_table):
        return None
    arg = ibo_table[idx]
    return ResolvedIteration(
        entry=entry,
        record=record_table[idx],
        required_ibo_identity=arg,
        required_ibo_argument=arg,
    )


def _is_drawable(ctx: LoopContext, entry: IndexTableEntry) -> bool:
    return (ctx.skip_or_visibility_mask & entry.visibility_byte) != 0


def _null_ibo_argument(arg: int) -> bool:
    return arg == 0


def _entry_matches_key(
    ctx: LoopContext,
    key: BatchKey,
    topology: IboBindTopology,
) -> EntryStateMatch:
    def cmp(known: bool, current: int, required: int) -> TriState:
        if not known:
            return "unknown"
        return "match" if current == required else "mismatch"

    if topology == "ONE_BIND":
        ibo = "mismatch" if _null_ibo_argument(key.ibo_argument) else "match"
    else:
        ibo = cmp(ctx.ibo_known, ctx.bound_ibo_identity, key.ibo_identity)

    return EntryStateMatch(
        color=cmp(ctx.color_known, ctx.cached_color, key.color_state),
        vbo=cmp(ctx.vbo_known, ctx.cached_vbo_index, key.vbo_table_index),
        ibo=ibo,
    )


def _homogeneity_mask(resolved: ResolvedIteration, key: BatchKey) -> BarrierMask:
    boundary = BarrierMask(0)
    ent = resolved.entry
    rec = resolved.record
    if ent.color_byte != key.color_state:
        boundary |= BarrierMask.COLOR_TRANSITION
    if rec.vbo_table_index != key.vbo_table_index:
        boundary |= BarrierMask.VBO_TRANSITION
    if resolved.required_ibo_identity != key.ibo_identity:
        boundary |= BarrierMask.IBO_TRANSITION
    if _null_ibo_argument(resolved.required_ibo_argument):
        boundary |= BarrierMask.OTHER_SIDE_EFFECT
    if rec.triangle_count == 0:
        boundary |= BarrierMask.OTHER_SIDE_EFFECT
    return boundary


def classify_batchable_prefix(
    ctx: LoopContext,
    record_table: list[RecordView],
    ibo_table: list[int],
    side_entries: list[IndexTableEntry],
    *,
    ibo_bind_topology: IboBindTopology = "ONE_BIND",
    max_scan_steps: int | None = None,
    walk_exhausted: bool = True,
) -> PrefixResult:
    def result(
        run_len: int,
        stop: BarrierMask,
        boundary: BarrierMask,
        key: BatchKey | None,
        entry_match: EntryStateMatch,
        kind: TerminationKind,
    ) -> PrefixResult:
        elim = max(0, run_len - 1)
        eligible = (
            run_len >= 2
            and entry_match.all_match()
            and kind is not TerminationKind.INVALID
        )
        fall_through = run_len < 2 or kind is TerminationKind.INVALID
        return PrefixResult(
            run_length=run_len,
            stop_reason_mask=stop,
            boundary_reason_mask=boundary,
            batch_key=key,
            entry_state_matches_batch_key=entry_match,
            draws_covered=run_len,
            eligible_draw_calls_eliminable=elim,
            batch_eligible=eligible,
            v1_fall_through_recommended=fall_through,
            termination_kind=kind,
        )

    if ctx.deferred_attrib_upload_pending or ctx.secondary_upload_pending:
        return result(0, BarrierMask.OTHER_SIDE_EFFECT, BarrierMask(0), None, EntryStateMatch(), TerminationKind.BARRIER)

    if not side_entries:
        kind = TerminationKind.WALK_END if walk_exhausted else TerminationKind.SCAN_CAP
        return result(0, BarrierMask(0), BarrierMask(0), None, EntryStateMatch(), kind)

    if ctx.mode != 0:
        return result(0, BarrierMask.UNSUPPORTED_MODE, BarrierMask.UNSUPPORTED_MODE, None, EntryStateMatch(), TerminationKind.BARRIER)

    first = side_entries[0]
    if not _is_drawable(ctx, first):
        return result(0, BarrierMask.SKIP, BarrierMask.SKIP, None, EntryStateMatch(), TerminationKind.BARRIER)

    resolved0 = _resolve_drawable(first, record_table, ibo_table)
    if resolved0 is None:
        return result(0, BarrierMask.OTHER_SIDE_EFFECT, BarrierMask.OTHER_SIDE_EFFECT, None, EntryStateMatch(), TerminationKind.INVALID)

    key = BatchKey(
        mode=ctx.mode,
        color_state=first.color_byte,
        vbo_table_index=resolved0.record.vbo_table_index,
        ibo_identity=resolved0.required_ibo_identity,
        ibo_argument=resolved0.required_ibo_argument,
    )
    entry_match = _entry_matches_key(ctx, key, ibo_bind_topology)
    if entry_match.any_unknown() or not entry_match.all_match():
        return result(0, BarrierMask.OTHER_SIDE_EFFECT, BarrierMask(0), key, entry_match, TerminationKind.BARRIER)

    if _homogeneity_mask(resolved0, key):
        return result(0, BarrierMask.OTHER_SIDE_EFFECT, BarrierMask.OTHER_SIDE_EFFECT, key, entry_match, TerminationKind.BARRIER)

    run_len = 0
    boundary = BarrierMask(0)
    termination = TerminationKind.WALK_END

    for ent in side_entries:
        if max_scan_steps is not None and run_len >= max_scan_steps:
            termination = TerminationKind.SCAN_CAP
            break

        if not _is_drawable(ctx, ent):
            boundary = BarrierMask.SKIP
            termination = TerminationKind.BARRIER
            break

        resolved = _resolve_drawable(ent, record_table, ibo_table)
        if resolved is None:
            boundary = BarrierMask.OTHER_SIDE_EFFECT
            termination = TerminationKind.INVALID
            break

        mask = _homogeneity_mask(resolved, key)
        if mask:
            boundary = mask
            termination = TerminationKind.BARRIER
            break

        run_len += 1
    else:
        termination = TerminationKind.WALK_END if walk_exhausted else TerminationKind.SCAN_CAP

    return result(run_len, boundary, boundary, key, entry_match, termination)
