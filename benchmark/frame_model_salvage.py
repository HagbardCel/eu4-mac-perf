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

RETENTION_POOR_THRESHOLD = 0.25
TOP_N = 16
FULL_RETENTION_THRESHOLD = 1.0


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
        if generation:
            windows[generation] = list(window.get("sampled") or [])
    return windows


def _window_frame_keys(
    tail_frames: list[dict],
    generation: int,
    observed: list[dict],
) -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    for obs in observed:
        try:
            keys.add((int(obs["measurement_epoch"]), int(obs["update_id"])))
        except (KeyError, TypeError, ValueError):
            continue
    if keys:
        return keys
    return _frame_keys_for_generation(tail_frames, generation)


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


def _aggregate_ranks(rows: list[list[str]], *, kinds: frozenset[str] | None = None) -> list[tuple[tuple, int]]:
    counts: Counter[tuple] = Counter()
    for row in rows:
        if kinds is not None and row[0] not in kinds:
            continue
        key = _salvage_rank_key(row)
        if key:
            counts[key] += 1
    return counts.most_common(TOP_N)


def _count_complete_frame_keys(retentions: list[dict]) -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    for item in retentions:
        if item.get("retention_s") == FULL_RETENTION_THRESHOLD and item.get("retention_u") == FULL_RETENTION_THRESHOLD:
            keys.add((item.get("measurement_epoch", 0), item["update_id"]))
    return keys


def _shared_rank_maps(
    left: list[tuple[tuple, int]], right: list[tuple[tuple, int]]
) -> tuple[list[tuple], dict[tuple, int], dict[tuple, int]] | None:
    right_keys = {item[0] for item in right}
    shared = [key for key, _ in left if key in right_keys]
    if len(shared) < 3:
        return None
    left_order = {key: index for index, (key, _) in enumerate(left)}
    right_order = {key: index for index, (key, _) in enumerate(right)}
    shared_sorted = sorted(shared, key=lambda key: (left_order[key], right_order[key]))
    left_rank = {key: index for index, key in enumerate(sorted(shared, key=lambda k: left_order[k]))}
    right_rank = {key: index for index, key in enumerate(sorted(shared, key=lambda k: right_order[k]))}
    return shared_sorted, left_rank, right_rank


def _spearman(left: list[tuple[tuple, int]], right: list[tuple[tuple, int]]) -> float | None:
    mapped = _shared_rank_maps(left, right)
    if mapped is None:
        return None
    shared, left_rank, right_rank = mapped
    n = len(shared)
    diff_sq = sum((left_rank[key] - right_rank[key]) ** 2 for key in shared)
    return 1 - (6 * diff_sq) / (n * (n * n - 1))


def _kendall(left: list[tuple[tuple, int]], right: list[tuple[tuple, int]]) -> float | None:
    mapped = _shared_rank_maps(left, right)
    if mapped is None:
        return None
    shared, left_rank, right_rank = mapped
    concordant = discordant = 0
    for i, a in enumerate(shared):
        for b in shared[i + 1 :]:
            left_order = left_rank[a] - left_rank[b]
            right_order = right_rank[a] - right_rank[b]
            product = left_order * right_order
            if product > 0:
                concordant += 1
            elif product < 0:
                discordant += 1
    denom = concordant + discordant
    return (concordant - discordant) / denom if denom else None


def _rank_overlap(left: list[tuple[tuple, int]], right: list[tuple[tuple, int]]) -> float:
    left_keys = [item[0] for item in left]
    right_keys = [item[0] for item in right]
    if not left_keys or not right_keys:
        return 0.0
    overlap = len(set(left_keys) & set(right_keys))
    return overlap / min(len(left_keys), len(right_keys), TOP_N)


def _count_rates(rows: list[list[str]], frame_count: int) -> Counter[tuple]:
    counts: Counter[tuple] = Counter()
    for row in rows:
        key = _salvage_rank_key(row)
        if key:
            counts[key] += 1
    if frame_count <= 0:
        return counts
    return Counter({key: value / frame_count for key, value in counts.items()})


def _callsite_drift_table(
    left_rows: list[list[str]],
    right_rows: list[list[str]],
    left_frames: int,
    right_frames: int,
) -> list[dict[str, Any]]:
    left_rates = _count_rates(left_rows, left_frames)
    right_rates = _count_rates(right_rows, right_frames)
    keys = sorted(set(left_rates) | set(right_rates), key=lambda item: (-max(left_rates.get(item, 0), right_rates.get(item, 0)), item))
    table: list[dict[str, Any]] = []
    for rank, key in enumerate(keys[:TOP_N], start=1):
        left_rate = left_rates.get(key, 0.0)
        right_rate = right_rates.get(key, 0.0)
        denom = max(left_rate, right_rate, 1e-9)
        entry: dict[str, Any] = {
            "rank": rank,
            "key": list(key),
            "window_a_count_per_frame": round(left_rate, 4),
            "window_b_count_per_frame": round(right_rate, 4),
            "relative_drift": round(abs(left_rate - right_rate) / denom, 4),
        }
        if key[0] == "S" and len(key) >= 3:
            entry["symbol"] = symbolize.eu4_symbolize_offset(int(key[2]))
        elif len(key) >= 2 and isinstance(key[1], int):
            entry["symbol"] = symbolize.eu4_symbolize_offset(int(key[1]))
        table.append(entry)
    return table


def _retention_poor(window_reports: dict[str, Any]) -> bool:
    fallback_s: list[float] = []
    fallback_u: list[float] = []
    for window in window_reports.values():
        retentions = window.get("per_frame_retention") or []
        cc_keys = _count_complete_frame_keys(retentions)
        if cc_keys:
            cc_s = [
                item["retention_s"]
                for item in retentions
                if (item.get("measurement_epoch", 0), item["update_id"]) in cc_keys
                and item.get("retention_s") is not None
            ]
            cc_u = [
                item["retention_u"]
                for item in retentions
                if (item.get("measurement_epoch", 0), item["update_id"]) in cc_keys
                and item.get("retention_u") is not None
            ]
            if cc_s and min(cc_s) < FULL_RETENTION_THRESHOLD:
                return True
            if cc_u and min(cc_u) < FULL_RETENTION_THRESHOLD:
                return True
            continue
        if window.get("median_retention_s") is not None:
            fallback_s.append(window["median_retention_s"])
        if window.get("median_retention_u") is not None:
            fallback_u.append(window["median_retention_u"])
    if fallback_s and min(fallback_s) < RETENTION_POOR_THRESHOLD:
        return True
    if fallback_u and min(fallback_u) < RETENTION_POOR_THRESHOLD:
        return True
    return False


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
        "measurement_epoch": frame.get("measurement_epoch"),
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
        keys = _window_frame_keys(tail_frames, gen, windows.get(gen) or [])
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
            "top_ranks_all_frames": [_rank_entry(k, c) for k, c in _aggregate_ranks(window_rows)],
            "top_ranks_count_complete": [
                _rank_entry(k, c)
                for k, c in _aggregate_ranks(
                    [r for r in window_rows if _detail_key(r) in _count_complete_frame_keys(retentions)],
                    kinds=frozenset({"S"}),
                )
            ],
            "top_uniform_ranks_count_complete": [
                _rank_entry(k, c)
                for k, c in _aggregate_ranks(
                    [r for r in window_rows if _detail_key(r) in _count_complete_frame_keys(retentions)],
                    kinds=SALVAGE_UNIFORM_KINDS,
                )
            ],
        }

    rank_stability = None
    if len(window_gens) >= 2:
        g0, g1 = window_gens[0], window_gens[1]
        keys0 = {tuple(pair) for pair in window_reports[str(g0)]["frame_keys"]}
        keys1 = {tuple(pair) for pair in window_reports[str(g1)]["frame_keys"]}
        left = _aggregate_ranks([r for r in salvage_trace if _detail_key(r) in keys0])
        right = _aggregate_ranks([r for r in salvage_trace if _detail_key(r) in keys1])
        left_complete = _aggregate_ranks(
            [r for r in salvage_trace if _detail_key(r) in _count_complete_frame_keys(
                window_reports[str(g0)]["per_frame_retention"]
            )],
            kinds=frozenset({"S"}),
        )
        right_complete = _aggregate_ranks(
            [r for r in salvage_trace if _detail_key(r) in _count_complete_frame_keys(
                window_reports[str(g1)]["per_frame_retention"]
            )],
            kinds=frozenset({"S"}),
        )
        left_uniform = _aggregate_ranks(
            [r for r in salvage_trace if _detail_key(r) in _count_complete_frame_keys(
                window_reports[str(g0)]["per_frame_retention"]
            )],
            kinds=SALVAGE_UNIFORM_KINDS,
        )
        right_uniform = _aggregate_ranks(
            [r for r in salvage_trace if _detail_key(r) in _count_complete_frame_keys(
                window_reports[str(g1)]["per_frame_retention"]
            )],
            kinds=SALVAGE_UNIFORM_KINDS,
        )
        left_cc_rows = [
            r
            for r in salvage_trace
            if _detail_key(r) in _count_complete_frame_keys(window_reports[str(g0)]["per_frame_retention"])
            and r[0] == "S"
        ]
        right_cc_rows = [
            r
            for r in salvage_trace
            if _detail_key(r) in _count_complete_frame_keys(window_reports[str(g1)]["per_frame_retention"])
            and r[0] == "S"
        ]
        rank_stability = {
            "windows": [g0, g1],
            "top_n_overlap_fraction": _rank_overlap(left, right),
            "count_complete_s_overlap_fraction": _rank_overlap(left_complete, right_complete),
            "spearman_rho_all_frames": _spearman(left, right),
            "kendall_tau_all_frames": _kendall(left, right),
            "spearman_rho_count_complete_s": _spearman(left_complete, right_complete),
            "kendall_tau_count_complete_s": _kendall(left_complete, right_complete),
            "count_complete_u_overlap_fraction": _rank_overlap(left_uniform, right_uniform),
            "spearman_rho_count_complete_u": _spearman(left_uniform, right_uniform),
            "kendall_tau_count_complete_u": _kendall(left_uniform, right_uniform),
            "window_a_top": left,
            "window_b_top": right,
            "window_a_top_count_complete_s": left_complete,
            "window_b_top_count_complete_s": right_complete,
            "per_callsite_drift_count_complete_s": _callsite_drift_table(
                left_cc_rows,
                right_cc_rows,
                len(_count_complete_frame_keys(window_reports[str(g0)]["per_frame_retention"])),
                len(_count_complete_frame_keys(window_reports[str(g1)]["per_frame_retention"])),
            ),
        }

    retention_poor = _retention_poor(window_reports)
    if not salvage_trace:
        status = "insufficient_for_hypothesis_selection"
        reason = "No salvage detail rows after TAIL filter"
    elif retention_poor:
        status = "insufficient_for_hypothesis_selection"
        reason = "Poor detail retention in one or more TAIL sample windows"
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
            "## Rank stability (reporting only)",
            "",
            f"- Top-{TOP_N} overlap between windows {rs.get('windows')}: **{rs.get('top_n_overlap_fraction')}**",
            f"- Count-complete S overlap: **{rs.get('count_complete_s_overlap_fraction')}**",
            f"- Spearman ρ (all frames): **{rs.get('spearman_rho_all_frames')}**",
            f"- Kendall τ (all frames): **{rs.get('kendall_tau_all_frames')}**",
            f"- Spearman ρ (count-complete S): **{rs.get('spearman_rho_count_complete_s')}**",
            f"- Kendall τ (count-complete S): **{rs.get('kendall_tau_count_complete_s')}**",
            f"- Count-complete U overlap: **{rs.get('count_complete_u_overlap_fraction')}**",
            f"- Spearman ρ (count-complete U/u): **{rs.get('spearman_rho_count_complete_u')}**",
            f"- Kendall τ (count-complete U/u): **{rs.get('kendall_tau_count_complete_u')}**",
        ])
        drift_rows = rs.get("per_callsite_drift_count_complete_s") or []
        if drift_rows:
            lines.extend(["", "## Count-complete S callsite drift (TAIL windows)", ""])
            for row in drift_rows[:TOP_N]:
                symbol = row.get("symbol") or row.get("key")
                lines.append(
                    f"- #{row['rank']} {symbol}: W{rs['windows'][0]}={row['window_a_count_per_frame']}, "
                    f"W{rs['windows'][1]}={row['window_b_count_per_frame']}, drift={row['relative_drift']}"
                )
    lines.append("")
    lines.append(report.get("interpretation", ""))
    (run_dir / "salvage-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
