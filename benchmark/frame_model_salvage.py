#!/usr/bin/env python3
"""Bounded TAIL-only intrusive diagnostic telemetry salvage."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

import eu4_symbolize as symbolize

ROOT = Path(__file__).resolve().parents[1]

SALVAGE_STATE_KINDS = frozenset({"S", "B", "b", "T", "t"})
SALVAGE_UNIFORM_KINDS = frozenset({"U", "u"})
SALVAGE_DRAW_KINDS = frozenset({"D"})
DETAIL_KINDS = SALVAGE_STATE_KINDS | SALVAGE_UNIFORM_KINDS | SALVAGE_DRAW_KINDS

COUNTER_STABILITY_FIELDS = (
    "draws",
    "state_calls",
    "uniform_calls",
    "texture_binds",
    "buffer_binds",
    "program_switches",
)

RETENTION_POOR_THRESHOLD = 0.05
TOP_N = 16
RANK_AGREEMENT_MIN = 0.5


def _detail_key(row: list[str]) -> tuple[int, int] | None:
    try:
        if row[0] in DETAIL_KINDS and len(row) in (16, 20):
            return int(row[13]), int(row[14])
    except (ValueError, IndexError):
        return None
    return None


def eligible_intrusive_salvage_trace(
    trace: list[list[str]],
    frames: list[dict],
    tail_phase: dict,
    *,
    frame_filter=None,
) -> list[list[str]]:
    """Detail rows for salvage: allow 2048 except for D records."""
    from eu4_frame_model import eligible_frame

    if frame_filter is None:
        eligible = {
            (r["measurement_epoch"], r["update_id"])
            for r in frames
            if eligible_frame(r, tail_phase)
        }
    else:
        eligible = frame_filter
    incomplete = {
        (r.get("measurement_epoch"), r["update_id"])
        for r in frames
        if r.get("flags", 0) & 2048
    }
    result: list[list[str]] = []
    for row in trace:
        if not row:
            continue
        kind = row[0]
        if kind not in DETAIL_KINDS:
            continue
        key = _detail_key(row)
        if key is None or key not in eligible:
            continue
        if kind in SALVAGE_DRAW_KINDS and key in incomplete:
            continue
        result.append(row)
    return result


def _tail_sample_windows(manifest: dict) -> dict[int, list[dict]]:
    tail = manifest.get("profiling_tail") or {}
    windows: dict[int, list[dict]] = {}
    for request in tail.get("sample_requests") or []:
        generation = int(request["generation"])
        windows[generation] = list(request.get("preceding_observed") or [])
    sampled = (tail.get("sampled_window_evidence") or {}).get("windows") or []
    for window in sampled:
        generation = int(window.get("sample_window", window.get("generation", 0)))
        if generation and generation not in windows:
            windows[generation] = list(window.get("sampled") or [])
    return windows


def _frame_keys_for_generation(frames: list[dict], generation: int) -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    for frame in frames:
        if frame.get("generation") == generation or frame.get("sample_window") == generation:
            keys.add((frame["measurement_epoch"], frame["update_id"]))
    return keys


def _salvage_rank_key(row: list[str]) -> tuple | None:
    kind = row[0]
    try:
        if kind == "S" and len(row) >= 5:
            return ("S", int(row[3]), int(row[2]))
        if kind in SALVAGE_UNIFORM_KINDS and len(row) >= 4:
            return (kind, int(row[2]))
        if kind in {"B", "b", "T", "t"} and len(row) >= 4:
            return (kind, int(row[2]))
    except ValueError:
        return None
    return None


def _rank_entry(key: tuple, count: int) -> dict:
    entry: dict[str, Any] = {"key": list(key), "count": count}
    if key[0] == "S" and len(key) >= 3:
        entry["symbol"] = symbolize.eu4_symbolize_offset(int(key[2]))
    elif len(key) >= 2 and isinstance(key[1], int):
        entry["symbol"] = symbolize.eu4_symbolize_offset(int(key[1]))
    return entry


def _aggregate_ranks(rows: list[list[str]]) -> list[tuple[tuple, int]]:
    counts: Counter[tuple] = Counter()
    for row in rows:
        key = _salvage_rank_key(row)
        if key:
            counts[key] += 1
    return counts.most_common(TOP_N)


def _rank_overlap(left: list[tuple[tuple, int]], right: list[tuple[tuple, int]]) -> float:
    left_keys = [item[0] for item in left]
    right_keys = [item[0] for item in right]
    if not left_keys or not right_keys:
        return 0.0
    overlap = len(set(left_keys) & set(right_keys))
    return overlap / min(len(left_keys), len(right_keys), TOP_N)


def _c1_c2_counter_stability(manifest: dict, profile_rows: list[dict]) -> dict:
    from eu4_frame_model import INTRUSIVE_DIAGNOSTIC_PROFILE_PHASES, eligible_frame, PHASE_NUMBER

    summaries = manifest.get("phase_summaries") or {}
    out: dict[str, Any] = {}
    for name in ("C1", "C2"):
        phase_num = PHASE_NUMBER.get(name)
        window = next((p for p in manifest.get("phases") or [] if p.get("name") == name), None)
        if not window:
            continue
        frames = [r for r in profile_rows if eligible_frame(r, window)]
        medians = {}
        for field in COUNTER_STABILITY_FIELDS:
            values = [r.get(field, 0) for r in frames if field in r]
            medians[field] = statistics.median(values) if values else None
        out[name] = {"frame_count": len(frames), "medians": medians, "phase_summary": summaries.get(name)}
    if "C1" in out and "C2" in out:
        rel: dict[str, float | None] = {}
        for field in COUNTER_STABILITY_FIELDS:
            a, b = out["C1"]["medians"].get(field), out["C2"]["medians"].get(field)
            if a and b and a > 0:
                rel[field] = abs(a - b) / a
            else:
                rel[field] = None
        out["c1_c2_relative_spread"] = rel
    return out


def _frame_retention(frame: dict, rows: list[list[str]]) -> dict:
    key = (frame["measurement_epoch"], frame["update_id"])
    subset = [r for r in rows if _detail_key(r) == key]
    s_rows = sum(1 for r in subset if r[0] == "S")
    u_rows = sum(1 for r in subset if r[0] in SALVAGE_UNIFORM_KINDS)
    state_calls = frame.get("state_calls") or 0
    uniform_calls = frame.get("uniform_calls") or 0
    return {
        "update_id": frame["update_id"],
        "generation": frame.get("generation"),
        "sample_window": frame.get("sample_window"),
        "flags": frame.get("flags"),
        "state_calls": state_calls,
        "uniform_calls": uniform_calls,
        "surviving_s": s_rows,
        "surviving_u": u_rows,
        "retention_s": (s_rows / state_calls) if state_calls else None,
        "retention_u": (u_rows / uniform_calls) if uniform_calls else None,
    }


def run_intrusive_salvage(run_dir: Path) -> dict:
    from eu4_frame_model import PHASE_NUMBER, eligible_frame, frame_rows, read_rows

    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    from frame_model_evidence import resolve_telemetry_path

    telemetry = resolve_telemetry_path(run_dir)
    if not telemetry:
        raise ValueError(f"No telemetry.csv or telemetry.csv.gz in {run_dir}")

    trace = read_rows(telemetry)
    profile_rows = frame_rows(telemetry)
    tail_phase = manifest.get("profiling_tail") or next(
        (p for p in manifest.get("phases") or [] if p.get("name") == "TAIL"),
        None,
    )
    if not tail_phase:
        raise ValueError("manifest missing profiling_tail / TAIL phase")

    tail_frames = [r for r in profile_rows if eligible_frame(r, tail_phase)]
    salvage_trace = eligible_intrusive_salvage_trace(trace, profile_rows, tail_phase)

    windows = _tail_sample_windows(manifest)
    window_gens = sorted(windows.keys())
    window_reports: dict[str, Any] = {}
    for gen in window_gens:
        keys = _frame_keys_for_generation(tail_frames, gen)
        if not keys:
            for obs in windows[gen]:
                keys.add((obs["measurement_epoch"], obs["update_id"]))
        window_rows = [r for r in salvage_trace if _detail_key(r) in keys]
        window_frames = [f for f in tail_frames if (f["measurement_epoch"], f["update_id"]) in keys]
        retentions = [_frame_retention(f, salvage_trace) for f in window_frames]
        window_reports[str(gen)] = {
            "frame_keys": sorted(keys),
            "detail_rows": len(window_rows),
            "per_frame_retention": retentions,
            "median_retention_s": statistics.median(
                [r["retention_s"] for r in retentions if r["retention_s"] is not None]
            )
            if retentions
            else None,
            "median_retention_u": statistics.median(
                [r["retention_u"] for r in retentions if r["retention_u"] is not None]
            )
            if retentions
            else None,
            "top_ranks": [_rank_entry(k, c) for k, c in _aggregate_ranks(window_rows)],
        }

    rank_stability = None
    if len(window_gens) >= 2:
        g0, g1 = window_gens[0], window_gens[1]
        left = _aggregate_ranks(
            [r for r in salvage_trace if _detail_key(r) in _frame_keys_for_generation(tail_frames, g0) or ()]
        )
        right = _aggregate_ranks(
            [r for r in salvage_trace if _detail_key(r) in _frame_keys_for_generation(tail_frames, g1) or ()]
        )
        # rebuild window row sets properly
        keys0 = window_reports[str(g0)]["frame_keys"]
        keys1 = window_reports[str(g1)]["frame_keys"]
        left = _aggregate_ranks([r for r in salvage_trace if _detail_key(r) in set(map(tuple, keys0))])
        right = _aggregate_ranks([r for r in salvage_trace if _detail_key(r) in set(map(tuple, keys1))])
        rank_stability = {
            "windows": [g0, g1],
            "top_n_overlap_fraction": _rank_overlap(left, right),
            "window_a_top": left,
            "window_b_top": right,
        }

    med_ret_s = [
        window_reports[str(g)]["median_retention_s"]
        for g in window_gens
        if window_reports[str(g)]["median_retention_s"] is not None
    ]
    retention_poor = bool(med_ret_s) and min(med_ret_s) < RETENTION_POOR_THRESHOLD
    overlap = (rank_stability or {}).get("top_n_overlap_fraction") or 0.0
    unstable = overlap < RANK_AGREEMENT_MIN
    if retention_poor and unstable:
        status = "insufficient_for_hypothesis_selection"
        reason = "Poor detail retention and unstable top-N rank overlap between TAIL sample windows"
    elif not salvage_trace:
        status = "insufficient_for_hypothesis_selection"
        reason = "No salvage detail rows after TAIL filter"
    else:
        status = "usable_ordinal_hint"
        reason = "TAIL salvage produced ordinal hints; treat as non-quantitative"

    report = {
        "status": status,
        "reason": reason,
        "tail_phase": tail_phase.get("name"),
        "dropped_records": (manifest.get("probe_integrity") or {}).get("dropped_records"),
        "salvage_row_count": len(salvage_trace),
        "c1_c2_aggregate_stability": _c1_c2_counter_stability(manifest, profile_rows),
        "tail_sample_windows": window_reports,
        "rank_stability": rank_stability,
        "interpretation": (
            "Ordinal callsite ranks from TAIL windows only; C1/C2 provide aggregate F-counter stability. "
            "U/u program/location fields are not trusted under flag 2048."
        ),
    }
    return report


def write_salvage_reports(run_dir: Path, report: dict) -> None:
    run_dir = Path(run_dir)
    (run_dir / "salvage-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Intrusive diagnostic salvage (TAIL only)",
        "",
        f"- Status: **{report['status']}**",
        f"- {report['reason']}",
        f"- Salvage detail rows: **{report.get('salvage_row_count', 0)}**",
        f"- Dropped profiler records (run): **{report.get('dropped_records', 'n/a')}**",
        "",
        "## C1 vs C2 aggregate counters",
        "",
    ]
    stability = report.get("c1_c2_aggregate_stability") or {}
    spread = stability.get("c1_c2_relative_spread") or {}
    if spread:
        for field, value in spread.items():
            label = f"{value:.4f}" if isinstance(value, float) else "n/a"
            lines.append(f"- {field}: relative spread {label}")
    lines.extend(["", "## TAIL window retention", ""])
    for name, window in (report.get("tail_sample_windows") or {}).items():
        lines.append(
            f"- Window **{name}**: median r_S={window.get('median_retention_s')}, "
            f"r_U={window.get('median_retention_u')}, detail_rows={window.get('detail_rows')}"
        )
    rs = report.get("rank_stability") or {}
    if rs:
        lines.extend([
            "",
            "## Rank stability",
            "",
            f"- Top-{TOP_N} overlap between windows {rs.get('windows')}: **{rs.get('top_n_overlap_fraction')}**",
        ])
    lines.append("")
    lines.append(report.get("interpretation", ""))
    (run_dir / "salvage-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
