"""Offline prefix classifier for border loop-head multidraw (synthetic contract tests)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag
from typing import Literal

TriState = Literal["match", "mismatch", "unknown"]


class BarrierMask(IntFlag):
    SKIP = 1 << 0
    UNSUPPORTED_MODE = 1 << 1
    COLOR_TRANSITION = 1 << 2
    VBO_TRANSITION = 1 << 3
    IBO_TRANSITION = 1 << 4
    OTHER_SIDE_EFFECT = 1 << 5


@dataclass
class BatchKey:
    mode: int
    color_state: int
    vbo_table_index: int
    ibo_key: int
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
    current_ibo_key: int
    skip_or_visibility_mask: int
    color_known: bool = True
    vbo_known: bool = True
    ibo_known: bool = True


@dataclass
class IndexTableEntry:
    record_index: int
    skip_flag_set: bool
    color_byte: int


@dataclass
class RecordView:
    basevertex: int
    triangle_count: int
    vbo_table_index: int


@dataclass
class PrefixResult:
    run_length: int
    stop_reason_mask: BarrierMask
    batch_key: BatchKey | None
    entry_state_matches_batch_key: EntryStateMatch
    draws_covered: int
    eligible_draw_calls_eliminable: int
    batch_eligible: bool
    v1_fall_through_recommended: bool


def _batch_key_for_record(ctx: LoopContext, record: RecordView, entry: IndexTableEntry) -> BatchKey:
    return BatchKey(
        mode=ctx.mode,
        color_state=entry.color_byte,
        vbo_table_index=record.vbo_table_index,
        ibo_key=ctx.current_ibo_key,
    )


def _entry_matches_key(ctx: LoopContext, key: BatchKey) -> EntryStateMatch:
    def cmp(known: bool, current: int, required: int) -> TriState:
        if not known:
            return "unknown"
        return "match" if current == required else "mismatch"

    return EntryStateMatch(
        color=cmp(ctx.color_known, ctx.cached_color, key.color_state),
        vbo=cmp(ctx.vbo_known, ctx.cached_vbo_index, key.vbo_table_index),
        ibo=cmp(ctx.ibo_known, ctx.current_ibo_key, key.ibo_key),
    )


def classify_batchable_prefix(
    ctx: LoopContext,
    records: list[RecordView],
    side_entries: list[IndexTableEntry],
) -> PrefixResult:
    if not records or not side_entries:
        return PrefixResult(
            run_length=0,
            stop_reason_mask=BarrierMask.OTHER_SIDE_EFFECT,
            batch_key=None,
            entry_state_matches_batch_key=EntryStateMatch(),
            draws_covered=0,
            eligible_draw_calls_eliminable=0,
            batch_eligible=False,
            v1_fall_through_recommended=True,
        )

    entry0 = side_entries[0]
    if entry0.skip_flag_set:
        return PrefixResult(
            run_length=0,
            stop_reason_mask=BarrierMask.SKIP,
            batch_key=None,
            entry_state_matches_batch_key=EntryStateMatch(),
            draws_covered=0,
            eligible_draw_calls_eliminable=0,
            batch_eligible=False,
            v1_fall_through_recommended=True,
        )

    if ctx.mode != 0:
        return PrefixResult(
            run_length=0,
            stop_reason_mask=BarrierMask.UNSUPPORTED_MODE,
            batch_key=None,
            entry_state_matches_batch_key=EntryStateMatch(),
            draws_covered=0,
            eligible_draw_calls_eliminable=0,
            batch_eligible=False,
            v1_fall_through_recommended=True,
        )

    key = _batch_key_for_record(ctx, records[0], entry0)
    entry_match = _entry_matches_key(ctx, key)
    if entry_match.any_unknown() or not entry_match.all_match():
        return PrefixResult(
            run_length=0,
            stop_reason_mask=BarrierMask.OTHER_SIDE_EFFECT,
            batch_key=key,
            entry_state_matches_batch_key=entry_match,
            draws_covered=0,
            eligible_draw_calls_eliminable=0,
            batch_eligible=False,
            v1_fall_through_recommended=True,
        )

    run_len = 0
    stop = BarrierMask(0)
    for rec, ent in zip(records, side_entries):
        if ent.skip_flag_set:
            stop = BarrierMask.SKIP
            break
        if ctx.mode != 0:
            stop = BarrierMask.UNSUPPORTED_MODE
            break
        if ent.color_byte != key.color_state:
            stop |= BarrierMask.COLOR_TRANSITION
            break
        if rec.vbo_table_index != key.vbo_table_index:
            stop |= BarrierMask.VBO_TRANSITION
            break
        if ctx.current_ibo_key != key.ibo_key:
            stop |= BarrierMask.IBO_TRANSITION
            break
        run_len += 1

    elim = max(0, run_len - 1)
    return PrefixResult(
        run_length=run_len,
        stop_reason_mask=stop,
        batch_key=key,
        entry_state_matches_batch_key=entry_match,
        draws_covered=run_len,
        eligible_draw_calls_eliminable=elim,
        batch_eligible=run_len >= 2 and stop == 0,
        v1_fall_through_recommended=run_len < 2,
    )
