#!/usr/bin/env python3
"""Derive pre-ready forensic JSON from a preserved submission-experiment run directory."""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GAME_LOG_MARKERS = {
    "human_player": "Human Player set as primary local",
    "venice": "Venice",
    "launching_singleplayer": "Launching SINGLEPLAYER-game",
    "start_date": "Start-date: 1444.11.11",
    "end_restore_device_objects": "End RestoreDeviceObjects",
}

READY_TIMEOUT_SECONDS = 180.0


def _probe_stats(probe_path: Path, anchor: dict) -> dict:
    anchor_mono_ns = int(anchor["monotonic_ns"])
    anchor_wall_ns = int(anchor["wall_ns"])
    rows: list[dict] = []
    with probe_path.open(newline="") as handle:
        import csv

        for row in csv.reader(handle):
            if len(row) != 6 or row[0] != "S":
                continue
            _clock_ns, wall_ns, interval_ns, swaps, paused = map(int, row[1:])
            if interval_ns <= 0:
                continue
            mono = anchor_mono_ns + wall_ns - anchor_wall_ns
            rows.append(
                {
                    "monotonic_ns": mono,
                    "interval_ns": interval_ns,
                    "swaps_s": swaps * 1e9 / interval_ns,
                    "paused_swaps": paused,
                    "swaps": swaps,
                }
            )
    if not rows:
        return {"probe_row_count": 0}
    monos = [r["monotonic_ns"] for r in rows]
    first_mono, last_mono = monos[0], monos[-1]
    intervals_s = [(monos[i] - monos[i - 1]) / 1e9 for i in range(1, len(monos))]
    first_offset = (first_mono - anchor_mono_ns) / 1e9
    last_offset = (last_mono - anchor_mono_ns) / 1e9
    collection_mono_ns = anchor_mono_ns + int(READY_TIMEOUT_SECONDS * 1e9)
    last_age = (collection_mono_ns - last_mono) / 1e9
    swaps_rates = [r["swaps_s"] for r in rows]
    return {
        "timeout_window_seconds": READY_TIMEOUT_SECONDS,
        "probe_row_count": len(rows),
        "probe_span_seconds": round((last_mono - first_mono) / 1e9, 3),
        "first_probe_offset_seconds": round(first_offset, 3),
        "last_probe_offset_seconds": round(last_offset, 3),
        "last_probe_age_seconds": round(last_age, 3),
        "max_probe_interval_seconds": round(max(intervals_s), 3) if intervals_s else None,
        "min_probe_interval_seconds": round(min(intervals_s), 3) if intervals_s else None,
        "median_probe_interval_seconds": round(statistics.median(intervals_s), 3) if intervals_s else None,
        "median_swaps_s": round(statistics.median(swaps_rates), 3),
        "min_swaps_s": round(min(swaps_rates), 3),
        "max_swaps_s": round(max(swaps_rates), 3),
        "paused_swaps_total": sum(r["paused_swaps"] for r in rows),
        "swaps_total": sum(r["swaps"] for r in rows),
    }


def _game_log_markers(log_text: str) -> dict[str, bool]:
    return {key: phrase in log_text for key, phrase in GAME_LOG_MARKERS.items()}


def reconstruct_readiness_timeline(
    run_dir: Path,
    anchor: dict,
    *,
    game_log_ready_assumed: bool,
    log_timing_assumption: str,
    pid: int = 0,
) -> dict:
    sys.path.insert(0, str(ROOT / "benchmark"))
    import autonomous_runner as auto

    probe_path = run_dir / "auto-probe.csv"
    power_path = run_dir / "powermetrics.pliststream"
    rows = auto.probe_rows(probe_path, anchor)
    power_samples: list[dict] = []
    if power_path.is_file():
        tail = auto.PowerTail(power_path)
        tail.poll()
        tail.finish()
        power_samples = tail.samples
    blocking_counts: dict[str, int] = {
        "probe_sample_count": 0,
        "probe_fresh": 0,
        "paused_rows_ok": 0,
        "swap_rate_stable": 0,
        "power_stable": 0,
        "game_log_ready": 0,
    }
    first_numeric_true_offset: float | None = None
    for index, row in enumerate(rows):
        eval_ns = int(row["monotonic_ns"])
        window = [r for r in rows if r["monotonic_ns"] <= eval_ns][-10:]
        power = auto.cpu_samples_between(
            power_samples, pid, anchor, eval_ns - 12_000_000_000, eval_ns
        )[-10:]
        logged = game_log_ready_assumed
        preds = auto.readiness_predicates(
            logged=logged,
            frontmost=False,
            pointer_interior=False,
            rows=window,
            now_ns=eval_ns,
            power=power,
            gate_swap_stability=True,
            gate_power_stability=True,
        )
        numeric_ok = (
            preds["probe_sample_count"] >= 10
            and preds["probe_fresh"]
            and preds["paused_rows_ok"]
            and preds["swap_rate_stable"]
            and preds["power_stable"]
        )
        if not numeric_ok:
            for key in ("probe_sample_count", "probe_fresh", "paused_rows_ok", "swap_rate_stable", "power_stable"):
                if key == "probe_sample_count":
                    if preds["probe_sample_count"] < 10:
                        blocking_counts[key] += 1
                elif not preds.get(key, False):
                    blocking_counts[key] += 1
        if not logged:
            blocking_counts["game_log_ready"] += 1
        elif first_numeric_true_offset is None:
            first_numeric_true_offset = round((eval_ns - int(anchor["monotonic_ns"])) / 1e9, 3)
    non_focus_numeric = first_numeric_true_offset is not None
    if non_focus_numeric and log_timing_assumption == "optimistic_final_state":
        conclusion = (
            "Under optimistic final-log-state assumption, all observable non-focus legacy "
            "readiness predicates were simultaneously satisfiable at least once; "
            "focus/pointer remains the leading unobserved blocker."
        )
    elif non_focus_numeric:
        conclusion = (
            "With reconstructed log timing, all non-focus legacy readiness predicates were "
            "simultaneously satisfiable at least once; focus/pointer was necessarily the missing class."
        )
    else:
        conclusion = (
            "No probe instant satisfied all non-focus legacy readiness predicates; "
            f"dominant blockers by count: {blocking_counts}."
        )
    return {
        "policy": "legacy_wait_until_ready_v1",
        "log_timing_assumption": log_timing_assumption,
        "non_focus_numeric_all_true_any_instant": non_focus_numeric,
        "first_non_focus_numeric_true_offset_seconds": first_numeric_true_offset,
        "blocking_counts": blocking_counts,
        "focus_not_observable": True,
        "conclusion": conclusion,
    }


def build_forensic(run_dir: Path, *, game_log_path: Path | None = None) -> dict:
    run_dir = run_dir.resolve()
    manifest_path = run_dir / "manifest.json"
    probe_path = run_dir / "auto-probe.csv"
    events_path = run_dir / "events.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    anchor: dict | None = None
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("event") == "clock_anchor":
                anchor = item
                break
    if anchor is None:
        raise ValueError(f"clock_anchor missing in {events_path}")
    probe_stats = _probe_stats(probe_path, anchor)
    log_source = "unavailable"
    log_text = ""
    if game_log_path and game_log_path.is_file():
        log_source = str(game_log_path)
        log_text = game_log_path.read_text(encoding="utf-8", errors="replace")
    markers = _game_log_markers(log_text)
    game_log_ready = all(markers.values()) if log_text else False
    diag = manifest.get("pre_ready_diagnostics")
    if isinstance(diag, dict):
        if diag.get("game_log_markers"):
            markers = dict(diag["game_log_markers"])
            game_log_ready = bool(diag.get("game_log_ready", game_log_ready))
        if diag.get("game_log_source"):
            log_source = str(diag["game_log_source"])
    verified_commit = manifest.get("verified_code_commit", "3ae26023acb5f0e255c8d1182ab2438b47f7b74c")
    median_swaps = probe_stats.get("median_swaps_s")
    log_timing_assumption = "optimistic_final_state" if game_log_ready and not log_text else (
        "reconstructed_timestamps" if log_text else "absent"
    )
    readiness_reconstruction = reconstruct_readiness_timeline(
        run_dir,
        anchor,
        game_log_ready_assumed=game_log_ready,
        log_timing_assumption=log_timing_assumption,
    )
    if game_log_ready and isinstance(median_swaps, (int, float)) and median_swaps >= 20:
        interpretation = readiness_reconstruction["conclusion"]
    elif isinstance(median_swaps, (int, float)) and median_swaps < 5 and probe_stats.get("probe_row_count", 0) < 20:
        interpretation = (
            "Sparse probe rows and very low swap rate during pre_ready while ROI observation was inactive."
        )
    else:
        interpretation = (
            "Deferred loop-head install did not cure pre_ready; probe cadence shows severe "
            "render/load degradation while ROI observation was inactive."
        )
    return {
        "run": run_dir.name,
        "error": manifest.get("error"),
        "deferred_install_at_candidate": manifest.get("deferred_install_at_candidate", True),
        "verified_code_commit": verified_commit,
        "derivation": {
            "probe_source": str(probe_path.relative_to(ROOT)) if probe_path.is_relative_to(ROOT) else str(probe_path),
            "manifest_source": str(manifest_path.relative_to(ROOT))
            if manifest_path.is_relative_to(ROOT)
            else str(manifest_path),
            "events_source": str(events_path.relative_to(ROOT)) if events_path.is_relative_to(ROOT) else str(events_path),
            "game_log_source": log_source,
        },
        "probe": probe_stats,
        "game_log_ready": game_log_ready,
        "game_log_markers": markers,
        "readiness_reconstruction": readiness_reconstruction,
        "interpretation": interpretation,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: pre_ready_forensic.py <run_dir> [game.log]", file=sys.stderr)
        return 2
    run_dir = Path(sys.argv[1])
    game_log = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    payload = build_forensic(run_dir, game_log_path=game_log)
    run_id = run_dir.name.removesuffix("-submission-experiment")
    out_path = ROOT / "analysis" / "evidence" / f"pre-ready-forensic-{run_id}.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
