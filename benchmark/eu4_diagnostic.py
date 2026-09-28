#!/usr/bin/env python3
"""One-launch passive renderer/timer diagnosis for the installed GOG EU IV."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import mmap
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

import eu4_benchmark as base


SOURCE = Path(__file__).with_suffix(".c")
LIBRARY = SOURCE.parent / ".build/libeu4_diagnostic.dylib"
HARNESS = base.ROOT / "tests/gl_telemetry_harness.c"
HARNESS_EXE = SOURCE.parent / ".build/gl_telemetry_harness"
WAIT_NAMES = {"nanosleep", "usleep", "mach_wait_until", "pthread_cond_timedwait", "poll", "select"}
DRAW_NAMES = {"glDrawElements", "glDrawElementsBaseVertex", "glDrawArrays",
              "glDrawArraysInstanced", "glDrawElementsInstanced"}
STATE_NAMES = {"glBindTexture", "glUseProgram", "glBindBuffer", "glEnable", "glDisable",
               "glActiveTexture", "glBlendFunc", "glBlendFuncSeparate", "glDepthFunc",
               "glDepthMask", "glCullFace", "glColorMask", "glVertexAttribPointer",
               "glEnableVertexAttribArray", "glDisableVertexAttribArray", "glBindVertexArray",
               "glScissor", "glStencilFunc", "glStencilMask", "glStencilOp", "glBindSampler",
               "glActiveTextureARB", "glBindBufferARB", "glUseProgramObjectARB",
               "glVertexAttribPointerARB", "glBindVertexArrayAPPLE",
               "glEnableVertexAttribArrayARB", "glDisableVertexAttribArrayARB"}


def build() -> None:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    for source, output, extra in (
        (SOURCE, LIBRARY, ["-dynamiclib"]),
        (HARNESS, HARNESS_EXE, []),
    ):
        command = ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
                   *extra, "-framework", "OpenGL", "-o", str(output), str(source)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise base.BenchmarkError(f"Could not build {source.name}: {result.stderr.strip()}")
        if base.command("lipo", "-archs", str(output)).strip() != "x86_64":
            raise base.BenchmarkError(f"{output.name} is not x86_64")


def telemetry_rows(path: Path) -> list[list[str]]:
    if not path.is_file():
        return []
    with path.open(newline="") as source:
        return [row for row in csv.reader(source) if row]


def preflight() -> dict:
    build()
    identity = base.gog_identity()
    if base.command("lipo", "-archs", str(base.GOG_EXE)).strip() != "x86_64":
        raise base.BenchmarkError("The installed GOG build is not x86_64")
    name_block = SOURCE.read_text().split("static const char *names[N_FUNCS] = {", 1)[1].split("typedef struct", 1)[0]
    covered = set(re.findall(r'"(gl[A-Z][A-Za-z0-9_]*)"', name_block))
    source_text = SOURCE.read_text()
    resolved = set(re.findall(r'ENTRY\((gl[A-Z][A-Za-z0-9_]*),', source_text))
    interposed = set(re.findall(r'INTERPOSE\((gl[A-Z][A-Za-z0-9_]*)\)', source_text))
    if covered-resolved or covered-interposed:
        raise base.BenchmarkError(f"GL coverage tables disagree: dlsym missing {sorted(covered-resolved)}, direct missing {sorted(covered-interposed)}")
    imported = set(re.findall(r'\b_(gl[A-Z][A-Za-z0-9_]*)\b',
                              base.command("nm", "-u", str(base.GOG_EXE))))
    missing_imports = sorted(imported-covered)
    if missing_imports:
        raise base.BenchmarkError(f"Direct GOG OpenGL imports lack wrappers: {missing_imports}")
    with tempfile.TemporaryDirectory(prefix="eu4-diag-preflight-") as temporary:
        root = Path(temporary)
        log, control = root / "telemetry.csv", root / "control.bin"
        control.write_bytes(b"\1" + bytes(4095))
        env = os.environ.copy()
        env.update(EU4_DIAG_LOG=str(log), EU4_DIAG_CONTROL=str(control),
                   DYLD_INSERT_LIBRARIES=str(LIBRARY))
        harness_started_wall = time.time_ns()
        result = subprocess.run([str(HARNESS_EXE)], env=env, capture_output=True,
                                text=True, check=False, timeout=20)
        harness_ended_wall = time.time_ns()
        if result.returncode:
            raise base.BenchmarkError(f"Offline OpenGL harness failed ({result.returncode}): "
                                      f"{result.stderr.strip() or result.stdout.strip()}")
        rows = telemetry_rows(log)
        snapshot_walls = [int(row[5]) for row in rows if row[0] == "S" and len(row) >= 6]
        if not snapshot_walls or not all(harness_started_wall-2_000_000_000 <= stamp <=
                                         harness_ended_wall+2_000_000_000
                                         for stamp in snapshot_walls):
            raise base.BenchmarkError("C telemetry wall-clock anchors are missing or implausible")
        seen = Counter()
        for row in rows:
            if row[0] == "F":
                seen[row[3]] += int(row[4])
        required = {"CGLFlushDrawable", "glDrawArrays", "glBindFramebuffer", "glEnable",
                    "usleep", "nanosleep", "poll", "select"}
        if (not required.issubset(seen) or
                not {"V", "R", "Z"}.issubset({row[0] for row in rows}) or
                not any(row[0] == "F" and row[3] == "glEnable" and int(row[5]) > 0
                        for row in rows)):
            raise base.BenchmarkError("Offline harness did not prove GL calls, timers, draw characteristics, and redundancy")
        unknown = sorted({row[2] for row in rows if row[0] == "U"})
        stress: dict[str, list[int]] = {"passthrough": [], "full": []}
        for mode_name, mode in (("passthrough", 3), ("full", 1)):
            control.write_bytes(bytes([mode]) + bytes(4095))
            for repeat in range(2):
                env["EU4_DIAG_LOG"] = str(root / f"stress-{mode_name}-{repeat}.csv")
                timed = subprocess.run([str(HARNESS_EXE), "--stress"], env=env,
                                       capture_output=True, text=True, check=False, timeout=20)
                match = re.search(r"stress_ns=(\d+)", timed.stdout)
                if timed.returncode or not match:
                    raise base.BenchmarkError(f"Offline stress harness failed in {mode_name} mode")
                stress[mode_name].append(int(match.group(1)))
        stress_result = {"passthrough_ns_per_million_calls": statistics.median(stress["passthrough"]),
                         "full_ns_per_million_calls": statistics.median(stress["full"])}
        stress_result["added_ns_per_call"] = round(
            (stress_result["full_ns_per_million_calls"]-
             stress_result["passthrough_ns_per_million_calls"])/1_000_000, 2)
    return {"gog": identity, "library_sha256": base.sha256(LIBRARY),
            "direct_and_dlsym_harness": "passed", "harness_counts": dict(seen),
            "direct_gl_imports_covered": len(imported),
            "wrapped_gl_names": len(covered),
            "unwrapped_harness_gl": unknown,
            "stress_noop_gl_enable": stress_result}


def mark(path: Path, event: str, **values: object) -> dict:
    record = {"event": event, "monotonic_ns": time.monotonic_ns(),
              "wall_ns": time.time_ns(), **values}
    with path.open("a") as output:
        output.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def phase_for(timestamp: int, phases: list[dict]) -> str | None:
    for phase in phases:
        if phase["start_ns"] <= timestamp < phase["end_ns"]:
            return phase["name"]
    return None


def telemetry_clock_offset(rows: list[list[str]], anchor: dict | None) -> int:
    """Map x86-64 C timestamps onto the controller's monotonic clock."""
    if anchor is None:
        return 0
    offsets = [int(row[5])-int(row[1]) for row in rows
               if row[0] == "S" and len(row) >= 6]
    if not offsets:
        raise base.BenchmarkError("Telemetry lacks wall-clock anchors for phase alignment")
    return statistics.median_low(offsets) + anchor["monotonic_ns"]-anchor["wall_ns"]


def summarize_telemetry(rows: list[list[str]], phases: list[dict],
                        anchor: dict | None = None) -> dict:
    offset = telemetry_clock_offset(rows, anchor)
    phase_by_name = {phase["name"]: phase for phase in phases}
    result: dict[str, dict] = {}
    for phase in phases:
        result[phase["name"]] = {"seconds": (phase["end_ns"]-phase["start_ns"])/1e9,
                                 "frames": [], "calls": Counter(), "redundant": Counter(),
                                 "timed_ns": Counter(), "timed_samples": Counter(),
                                 "callers": Counter(), "thread_calls": Counter(),
                                 "wait_buckets": Counter(),
                                 "draws": 0, "adjacent": Counter(), "pass_draws": 0,
                                 "submitted_vertices": 0, "draw_modes": Counter(),
                                 "draw_sizes": Counter(),
                                 "pass_repeated_draws": 0, "passes": defaultdict(Counter),
                                 "overflow": Counter(), "unknown_gl": Counter(),
                                 "deep_stacks": []}
    for row in rows:
        try:
            timestamp = int(row[1]) + offset
            name = phase_for(timestamp, phases)
            if name is None:
                continue
            phase = phase_by_name[name]
            # Snapshot counters describe the preceding second. Drop the first
            # two seconds after a mode switch so no row mixes two modes.
            if (phase["end_ns"]-phase["start_ns"] >= 5_000_000_000 and
                    timestamp < phase["start_ns"]+2_000_000_000):
                continue
            item = result[name]
            if row[0] == "S":
                item["frames"].append(int(row[3]))
            elif row[0] == "F":
                function = row[3]
                item["calls"][function] += int(row[4])
                item["thread_calls"][row[2]] += int(row[4])
                item["redundant"][function] += int(row[5])
                item["timed_ns"][function] += int(row[6])
                item["timed_samples"][function] += int(row[7])
            elif row[0] == "C":
                item["callers"][(row[2], row[3], row[4], row[5])] += int(row[6])
            elif row[0] == "D":
                item["draws"] += int(row[3])
                for key, value in zip(("program", "textures", "buffers", "render", "uniforms", "complete"), row[4:10]):
                    item["adjacent"][key] += int(value)
                item["pass_draws"] += int(row[10])
                item["pass_repeated_draws"] += int(row[11])
            elif row[0] == "V":
                item["submitted_vertices"] += int(row[3])
            elif row[0] == "R":
                item["draw_modes"][row[3]] += int(row[4])
            elif row[0] == "Z":
                item["draw_sizes"][row[3]] += int(row[4])
            elif row[0] == "W":
                item["wait_buckets"][(row[2], row[3], int(row[4]))] += int(row[5])
            elif row[0] == "U":
                item["unknown_gl"][row[2]] += int(row[3])
            elif row[0] == "P":
                fbo = row[3]
                item["passes"][fbo]["count"] += int(row[4])
                item["passes"][fbo]["draws"] += int(row[5])
                item["passes"][fbo]["repeated_draws"] += int(row[6])
            elif row[0] == "Q":
                for key, value in zip(("callers", "shadow_state", "unknown_lookups"), row[2:5]):
                    item["overflow"][key] += int(value)
            elif row[0] == "O":
                item["overflow"]["passes"] += int(row[3])
            elif row[0] == "B":
                item["deep_stacks"].append({"function": row[2], "frames": row[3].split("|")})
        except (IndexError, ValueError):
            continue
    return result


def summarize_power(raw: list[dict], phases: list[dict], anchor: dict,
                    game_pid: int | None = None) -> dict:
    values: dict[str, dict[str, list[float]]] = {
        phase["name"]: defaultdict(list) for phase in phases}
    for sample in raw:
        timestamp = sample.get("timestamp")
        if not isinstance(timestamp, dt.datetime):
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
        sample_ns = int(timestamp.timestamp()*1e9)
        monotonic_ns = anchor["monotonic_ns"] + sample_ns-anchor["wall_ns"]
        phase = phase_for(monotonic_ns, phases)
        if phase is None:
            continue
        try:
            item = base.normalized_sample(sample, 0)
        except base.BenchmarkError:
            continue
        for field in ("cpu_w", "gpu_w", "combined_w", "gpu_active_pct"):
            if item[field] != "":
                values[phase][field].append(float(item[field]))
        for cluster in sample.get("processor", {}).get("clusters", []):
            frequency = cluster.get("freq_hz")
            if isinstance(frequency, (int, float)):
                values[phase][f"cpu_{cluster.get('name', 'cluster')}_mhz"].append(float(frequency)/1e6)
        frequency = sample.get("gpu", {}).get("freq_hz")
        if isinstance(frequency, (int, float)):
            values[phase]["gpu_mhz"].append(float(frequency)/1e6 if frequency > 1e7 else float(frequency))
        if game_pid is not None:
            task = next((task for task in sample.get("tasks", []) if task.get("pid") == game_pid), None)
            if task:
                values[phase]["eu4_timer_wakeups_per_s"].append(sum(
                    float(bucket.get("wakeups_per_s", 0)) for bucket in task.get("timer_wakeups", [])
                    if bucket.get("interval_ns", 0) <= 5_000_000))
                for field in ("idle_wakeups_per_s", "cputime_ms_per_s"):
                    if isinstance(task.get(field), (int, float)):
                        values[phase][f"eu4_{field}"].append(float(task[field]))
    return {name: {field: round(statistics.median(numbers), 3) for field, numbers in fields.items()
                   if numbers} | {"samples": max((len(v) for v in fields.values()), default=0)}
            for name, fields in values.items()}


def candidate_ranking(telemetry: dict, power: dict | None = None) -> list[dict]:
    paused = telemetry.get("paused_idle", {})
    calls = paused.get("calls", Counter())
    redundant = paused.get("redundant", Counter())
    draws = paused.get("draws", 0)
    state_calls = sum(calls[n] for n in STATE_NAMES)
    state_redundant = sum(redundant[n] for n in STATE_NAMES)
    uniform_names = [name for name in calls if name.startswith("glUniform")]
    uniform_calls = sum(calls[name] for name in uniform_names)
    uniform_redundant = sum(redundant[name] for name in uniform_names)
    wait_calls = sum(calls[n] for n in WAIT_NAMES)
    pass_draws = paused.get("pass_draws", 0)
    repeated = paused.get("pass_repeated_draws", 0)
    ranking = []
    if state_calls:
        ratio = state_redundant/state_calls
        estimated_ms = 0.0
        state_samples = 0
        for function in STATE_NAMES:
            samples = paused.get("timed_samples", Counter())[function]
            if samples:
                state_samples += samples
                estimated_ms += (paused["timed_ns"][function]/samples *
                                 redundant[function] / 1e6)
        frames = sum(paused.get("frames", []))
        estimated_ms_per_frame = estimated_ms/frames if frames else 0
        coverage_gap = bool(paused.get("unknown_gl") or paused.get("overflow"))
        timing_note = (f"their sampled inclusive time is approximately {estimated_ms_per_frame:.2f} ms/frame, not a predicted saving"
                       if state_samples else "sampled call time was disabled for this phase")
        ranking.append({"family": "GL state suppression", "signal": round(ratio, 3),
                        "reason": f"{state_redundant:,} of {state_calls:,} observed state calls repeated tracked values; {timing_note}",
                        "status": ("coverage limited" if coverage_gap else
                                   "investigate" if ratio >= .4 and estimated_ms_per_frame >= .5 else
                                   "time unmeasured" if ratio >= .4 and not state_samples else "low")})
    if uniform_calls:
        ratio = uniform_redundant/uniform_calls
        ranking.append({"family": "uniform update suppression", "signal": round(ratio, 3),
                        "reason": f"{uniform_redundant:,} of {uniform_calls:,} observed uniform updates repeated a tracked value (arrays larger than 256 bytes are excluded)",
                        "status": "investigate" if ratio >= .4 else "low"})
    if wait_calls:
        observed_rate = wait_calls/paused.get("seconds", 1)
        wakeups_rate = (power or {}).get("paused_idle", {}).get("eu4_timer_wakeups_per_s")
        coverage_limited = wakeups_rate is not None and observed_rate < .5*wakeups_rate
        ranking.append({"family": "timer/wait source", "signal": wait_calls,
                        "reason": f"{observed_rate:.0f}/s observed wait calls versus {wakeups_rate if wakeups_rate is not None else 'unknown'} EU IV short timer wakeups/s; inspect caller and duration histograms before any change",
                        "status": "coverage limited" if coverage_limited else
                                  "investigate" if observed_rate >= 500 else "low"})
    if pass_draws:
        ratio = repeated/pass_draws
        ranking.append({"family": "render-pass caching", "signal": round(ratio, 3),
                        "reason": f"{repeated:,} of {pass_draws:,} draw calls occurred in structurally repeated passes; pixel identity unproven",
                        "status": "research only" if ratio >= .3 else "low"})
    if draws:
        ratio = paused["adjacent"]["complete"]/draws
        ranking.append({"family": "draw batching", "signal": round(ratio, 3),
                        "reason": f"{paused['adjacent']['complete']:,} of {draws:,} adjacent draws shared the tracked state signature; ordering constraints untested",
                        "status": "research only"})
    return ranking


def image_differences(run_dir: Path) -> dict:
    before, after = run_dir / "paused-before.png", run_dir / "paused-after.png"
    if not before.is_file() or not after.is_file():
        return {"status": "unavailable"}
    output = {"status": "captured", "before_sha256": base.sha256(before),
              "after_sha256": base.sha256(after)}
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError:
        output["pixel_comparison"] = "Pillow unavailable"
        return output
    with Image.open(before) as first, Image.open(after) as second:
        if first.size != second.size:
            output["pixel_comparison"] = "different image dimensions"
            return output
        if max(ImageStat.Stat(first.resize((64, 64)).convert("RGB")).stddev) < 2 or \
           max(ImageStat.Stat(second.resize((64, 64)).convert("RGB")).stddev) < 2:
            output["status"] = "likely blank capture"
            return output
        width, height = first.size
        regions = {"whole_screen": (0, 0, width, height),
                   "map_center": (.2, .2, .8, .8),
                   "upper_left_ui": (0, 0, .2, .2),
                   "lower_right_ui": (.8, .8, 1, 1)}
        for label, bounds in regions.items():
            box = tuple(round(value*dimension) for value, dimension in zip(
                bounds, (width, height, width, height))) if label != "whole_screen" else bounds
            left = first.crop(box).convert("RGB")
            right = second.crop(box).convert("RGB")
            changed = ImageChops.difference(left, right).convert("L").point(
                lambda value: 255 if value > 2 else 0)
            histogram = changed.histogram()
            output[f"{label}_changed_fraction"] = round(
                histogram[255]/max(1, changed.width*changed.height), 6)
    return output


def write_report(run_dir: Path, phases: list[dict], anchor: dict,
                 game_pid: int | None = None) -> dict:
    telemetry = summarize_telemetry(telemetry_rows(run_dir / "telemetry.csv"), phases, anchor)
    raw = []
    for raw_path in (run_dir / "powermetrics.pliststream",
                     run_dir / "powermetrics-interaction.pliststream"):
        if raw_path.is_file():
            raw.extend(base.read_plist_stream(raw_path))
    power = summarize_power(raw, phases, anchor, game_pid)
    ranking = candidate_ranking(telemetry, power)
    images = image_differences(run_dir)
    output = {"phases": {}, "candidate_ranking": ranking, "screen_images": images,
              "limitations": ["Swap calls are not independently measured displayed frames.",
                              "Call durations are sampled inclusive elapsed time, not GPU time.",
                              "Redundancy is inferred from per-thread shadow state and can be uncertain if a GL context moves between threads or an unwrapped call changes state.",
                              "Draw-state matches are batching upper bounds; pass hashes do not prove equal pixels.",
                              "Power values are estimated system-wide SoC power, not EU IV-only watts."]}
    lines = ["# GOG EU IV root-cause diagnostic", "", "Passive telemetry; no optimization was enabled.", ""]
    counter_frames = telemetry.get("calibration_passthrough", {}).get("frames", [])
    full_frames = telemetry.get("calibration_full", {}).get("frames", [])
    if counter_frames and full_frames:
        counter_rate = statistics.median(counter_frames)
        full_rate = statistics.median(full_frames)
        delta = 100*(full_rate/counter_rate-1) if counter_rate else 0
        output["calibration"] = {"counter_median_swaps_s": counter_rate,
                                 "full_median_swaps_s": full_rate,
                                 "relative_change_pct": round(delta, 2)}
        lines.extend([f"In-session telemetry calibration: {counter_rate:.1f} → {full_rate:.1f} swap calls/s ({delta:+.1f}%). Short windows and scene drift limit this overhead estimate.", ""])
    for phase in phases:
        name = phase["name"]
        item = telemetry[name]
        seconds = item["seconds"]
        frames = item["frames"]
        frame_rate = sum(frames)/len(frames) if frames else None
        output["phases"][name] = {
            "seconds": seconds, "snapshot_count": len(frames),
            "frames_per_snapshot_mean": frame_rate,
            "power": power.get(name, {}), "calls": dict(item["calls"]),
            "redundant": dict(item["redundant"]), "unknown_gl_lookups": dict(item["unknown_gl"]),
            "draws": item["draws"], "adjacent_draws": dict(item["adjacent"]),
            "submitted_vertices": item["submitted_vertices"],
            "draw_modes": dict(item["draw_modes"]),
            "draw_sizes": dict(item["draw_sizes"]),
            "pass_draws": item["pass_draws"], "pass_repeated_draws": item["pass_repeated_draws"],
            "passes": {fbo: dict(stats) for fbo, stats in item["passes"].items()},
            "overflow": dict(item["overflow"]),
            "deep_stacks": item["deep_stacks"],
            "uniform_calls": sum(count for function, count in item["calls"].items()
                                 if function.startswith("glUniform")),
            "uniform_redundant": sum(count for function, count in item["redundant"].items()
                                     if function.startswith("glUniform")),
            "wait_buckets": {f"{key[0]}:{key[1]}:{key[2]}": value for key, value in item["wait_buckets"].items()},
            "top_callers": [{"thread_id": key[0], "function": key[1], "image": key[2],
                             "offset": key[3], "sampled_calls": value}
                            for key, value in item["callers"].most_common(20)],
            "thread_calls": dict(item["thread_calls"])}
        lines.extend([f"## {name.replace('_', ' ').title()}", "",
                      f"Swap calls/s (mean of complete one-second snapshots): {frame_rate:.1f}" if frame_rate is not None else "Swap calls/s: unavailable",
                      f"Estimated CPU / GPU / combined power: {power.get(name, {}).get('cpu_w', '—')} / {power.get(name, {}).get('gpu_w', '—')} / {power.get(name, {}).get('combined_w', '—')} W",
                      f"EU IV short timer wakeups: {power.get(name, {}).get('eu4_timer_wakeups_per_s', '—')}/s; idle wakeups: {power.get(name, {}).get('eu4_idle_wakeups_per_s', '—')}/s",
                      f"Reported GPU frequency: {power.get(name, {}).get('gpu_mhz', '—')} MHz; CPU cluster frequencies: " + ", ".join(f"{key}={value} MHz" for key, value in power.get(name, {}).items() if key.startswith("cpu_") and key.endswith("_mhz")),
                      f"Draw calls: {item['draws']:,}; structurally repeated-pass draws: {item['pass_repeated_draws']:,} / {item['pass_draws']:,}",
                      f"Repeated uniform updates: {output['phases'][name]['uniform_redundant']:,} / {output['phases'][name]['uniform_calls']:,}",
                      "", "| Function | Calls/s | Repeated state | Estimated inclusive ms/s |", "|---|---:|---:|---:|"])
        for function, count in item["calls"].most_common(30):
            samples = item["timed_samples"][function]
            estimate = (item["timed_ns"][function]/samples*count/seconds/1e6
                        if samples else None)
            lines.append(f"| {function} | {count/seconds:.1f} | {item['redundant'][function]:,} | {estimate:.2f} ({samples} samples) |" if estimate is not None else
                         f"| {function} | {count/seconds:.1f} | {item['redundant'][function]:,} | — |")
        lines.extend(["", "Top threads by intercepted calls:", ""])
        for thread_id, count in item["thread_calls"].most_common(8):
            lines.append(f"- Thread {thread_id}: {count:,} calls")
        lines.extend(["", "Top sampled callers (image-relative offsets):", ""])
        for (thread_id, function, image, offset), count in item["callers"].most_common(12):
            lines.append(f"- Thread {thread_id}, {function}: {image}+{offset}, {count} sampled calls")
        if item["deep_stacks"]:
            lines.extend(["", "Occasional deeper stack samples:", ""])
            for stack in item["deep_stacks"][:12]:
                lines.append(f"- {stack['function']}: " + " → ".join(stack["frames"]))
        if item["wait_buckets"]:
            labels = ("<1 ms", "1–2 ms", "2–3 ms", "3–5 ms", ">=5 ms")
            lines.extend(["", "Observed wait duration buckets:", ""])
            for (function, kind, bucket), count in sorted(item["wait_buckets"].items()):
                lines.append(f"- {function}, {kind} {labels[bucket]}: {count:,} calls")
        if item["passes"]:
            lines.extend(["", "Observed framebuffer passes (draw calls are submission counts):", ""])
            for fbo, stats in sorted(item["passes"].items(), key=lambda entry: entry[1]["draws"], reverse=True)[:12]:
                lines.append(f"- FBO {fbo}: {stats['count']:,} passes, {stats['draws']:,} draws, {stats['repeated_draws']:,} in structurally repeated passes")
        if item["draw_sizes"]:
            labels = ("0–3", "4–31", "32–255", "256–4095", "4096+")
            lines.extend(["", "Draw size (submitted vertices or indices per call):", ""])
            for bin, count in sorted(item["draw_sizes"].items(), key=lambda entry: int(entry[0])):
                lines.append(f"- {labels[int(bin)]}: {count:,} draws")
            lines.append(f"- Submitted vertices/indices across draws: {item['submitted_vertices']:,}")
            lines.append("- Primitive-mode counts: " + ", ".join(
                f"GL enum {mode}={count:,}" for mode, count in sorted(item["draw_modes"].items(),
                                                              key=lambda entry: int(entry[0]))))
        if item["overflow"]:
            lines.extend(["", "Telemetry table overflows: " + ", ".join(f"{key}={value}" for key, value in item["overflow"].items()), ""])
        if item["unknown_gl"]:
            lines.extend(["", "Unwrapped runtime GL lookups: " + ", ".join(sorted(item["unknown_gl"])), ""])
        lines.append("")
    lines.extend(["## Candidate ranking", ""])
    for candidate in ranking:
        lines.append(f"- **{candidate['family']}** ({candidate['status']}): {candidate['reason']}.")
    lines.extend(["", "## Screen-image check", "",
                  f"Capture status: {images['status']}."])
    for key, value in images.items():
        if key.endswith("_changed_fraction"):
            lines.append(f"- {key.replace('_', ' ')}: {value:.2%}")
    lines.extend(["", "## Interpretation limits", ""])
    lines.extend(f"- {item}" for item in output["limitations"])
    lines.append("")
    (run_dir / "root-cause.json").write_text(json.dumps(output, indent=2) + "\n")
    (run_dir / "root-cause.md").write_text("\n".join(lines))
    return output


def screenshot(path: Path) -> bool:
    result = subprocess.run(["screencapture", "-x", "-D", "1", str(path)],
                            capture_output=True, text=True, check=False)
    return result.returncode == 0 and path.is_file() and path.stat().st_size > 0


def run(args: argparse.Namespace) -> None:
    evidence = preflight()
    with tempfile.TemporaryDirectory(prefix="eu4-diag-screen-") as temporary:
        screen_capture_available = screenshot(Path(temporary) / "permission-check.png")
    try:
        base.running_game_pid("idle")
    except base.BenchmarkError as exc:
        raise base.BenchmarkError("Close EU IV before this controlled diagnostic launch") from exc
    save = base.DEFAULT_SAVE
    if not save.is_file():
        raise base.BenchmarkError(f"Disposable Venice save missing: {save}")
    save_hash = base.sha256(save)
    auth = subprocess.run(["sudo", "-v"], check=False)
    if auth.returncode:
        raise base.BenchmarkError("Administrator access is required for powermetrics")
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    if launcher.get("exePath") != "./eu4.app/Contents/MacOS/eu4" or not isinstance(launcher.get("exeArgs"), list):
        raise base.BenchmarkError("Unexpected GOG launcher configuration")
    root = Path(args.output).expanduser().resolve()
    run_dir = root / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-diagnostic"
    run_dir.mkdir(parents=True, exist_ok=False)
    control_path = run_dir / "control.bin"
    control_path.write_bytes(b"\3" + bytes(4095))
    events = run_dir / "events.jsonl"
    metadata = {"status": "launching", "gog": evidence["gog"], "preflight": evidence,
                "save_sha256": save_hash, "library_sha256": evidence["library_sha256"],
                "run_dir": str(run_dir),
                "screen_capture_available": screen_capture_available}
    metadata_path = run_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    game = None
    pm = None
    phases: list[dict] = []
    try:
        with control_path.open("r+b") as control_file, mmap.mmap(control_file.fileno(), 4096) as control:
            env = os.environ.copy()
            env.update(EU4_DIAG_LOG=str(run_dir / "telemetry.csv"),
                       EU4_DIAG_CONTROL=str(control_path), DYLD_INSERT_LIBRARIES=str(LIBRARY))
            with (run_dir / "game.stdout").open("wb") as stdout, (run_dir / "game.stderr").open("wb") as stderr:
                game = subprocess.Popen(["./eu4", *launcher["exeArgs"]], cwd=base.GOG_EXE.parent,
                                        env=env, stdout=stdout, stderr=stderr)
            metadata["game_pid"] = game.pid
            print("Launched GOG EU IV with passive diagnostic telemetry.")
            print("Load the disposable Venice save, pause it, and return to Terminal.")
            if base.prompt("Confirm the expected save and DLC/mods are loaded (yes/no): ", True).lower() != "yes":
                raise base.BenchmarkError("Game state was not confirmed")
            if game.poll() is not None or base.running_game_pid("paused") != game.pid:
                raise base.BenchmarkError("The expected GOG process is not running")
            if subprocess.run(["sudo", "-v"], check=False).returncode:
                raise base.BenchmarkError("Administrator authorization expired before capture")
            base.prompt("Keep the game paused. Press Enter, return to EU IV, and leave it untouched until the second sound: ")
            print("After a five-second lead-in: 10s near-pass-through, 10s full telemetry, 60s paused diagnostic.", flush=True)
            time.sleep(5)
            display = base.display_mode()
            settings = base.game_settings()
            power = base.power_state()
            mods = base.mod_settings()
            if (not display.get("verified") or abs(display.get("refresh_hz", 0)-120)>1 or
                    settings.get("fullScreen") != "yes" or settings.get("borderless") != "no" or
                    settings.get("game_resolution") != "3456x2234" or power.get("mode") != "normal"):
                raise base.BenchmarkError("Expected verified 120 Hz, 3456x2234 fullscreen, Normal power mode")
            metadata.update(display=display, settings=settings, power=power, dlc_mods=mods)
            anchor = mark(events, "clock_anchor")
            metadata["clock_anchor"] = anchor
            if screen_capture_available:
                screenshot(run_dir / "paused-before.png")
            command = ["sudo", "-n", "powermetrics", "-i", "1000", "-n", "80",
                       "-s", "tasks,cpu_power,gpu_power,thermal", "-f", "plist", "--show-process-gpu"]
            with (run_dir / "powermetrics.pliststream").open("wb") as raw, (run_dir / "powermetrics.stderr").open("wb") as errors:
                pm = subprocess.Popen(command, stdout=raw, stderr=errors)
                diagnostic_mode = 1
                for name, seconds, mode in (("calibration_passthrough", 10, 3),
                                            ("calibration_full", 10, 1),
                                            ("paused_idle", 60, 1)):
                    if name == "paused_idle":
                        mode = diagnostic_mode
                    control[0] = mode
                    start = mark(events, "phase_start", phase=name, mode=mode)
                    if name == "paused_idle": base.cue()
                    deadline = time.monotonic() + seconds
                    while time.monotonic() < deadline:
                        if game.poll() is not None:
                            raise base.BenchmarkError("GOG EU IV exited during diagnosis")
                        if pm.poll() is not None and pm.returncode:
                            raise base.BenchmarkError("powermetrics exited during diagnosis")
                        time.sleep(min(0.5, max(0, deadline-time.monotonic())))
                    end = mark(events, "phase_end", phase=name)
                    phases.append({"name": name, "start_ns": start["monotonic_ns"],
                                   "end_ns": end["monotonic_ns"]})
                    if name == "calibration_full":
                        calibration = summarize_telemetry(telemetry_rows(run_dir / "telemetry.csv"), phases, anchor)
                        light = calibration["calibration_passthrough"]["frames"]
                        full = calibration["calibration_full"]["frames"]
                        if light and full and statistics.median(full) < .95*statistics.median(light):
                            diagnostic_mode = 2
                            print("Telemetry calibration exceeded the 5% swap-rate screen; using counters and state tracking without sampled timing.", flush=True)
                metadata["diagnostic_mode"] = diagnostic_mode
                base.cue()
                try:
                    pm.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    pm.terminate(); pm.wait(timeout=5)
            if pm.returncode:
                raise base.BenchmarkError(f"powermetrics failed: {(run_dir/'powermetrics.stderr').read_text(errors='replace').strip()}")
            if screen_capture_available:
                screenshot(run_dir / "paused-after.png")
            if base.sha256(save) != save_hash:
                raise base.BenchmarkError("Disposable save changed during paused diagnosis")
            metadata["phases"] = phases
            report = write_report(run_dir, phases, anchor, game.pid)
            paused = report["phases"]["paused_idle"]
            if paused["snapshot_count"] < 45 or paused["power"].get("samples", 0) < 45:
                raise base.BenchmarkError("The paused phase lacks enough aligned frame or power samples; inspect its raw logs")
            repeated = paused["pass_repeated_draws"] / max(1, paused["pass_draws"])
            stable_map = report["screen_images"].get("map_center_changed_fraction", 1) < .01
            if repeated >= .3 and stable_map:
                print("Repeated render-pass structure warrants one optional map-interaction phase in this launch.")
                base.prompt("Press Enter, return to EU IV, then pan/zoom the paused map until the second sound: ")
                time.sleep(5)
                control[0] = diagnostic_mode
                with (run_dir / "powermetrics-interaction.pliststream").open("wb") as raw, \
                     (run_dir / "powermetrics-interaction.stderr").open("wb") as errors:
                    pm = subprocess.Popen(["sudo", "-n", "powermetrics", "-i", "1000", "-n", "20",
                                           "-s", "tasks,cpu_power,gpu_power,thermal", "-f", "plist",
                                           "--show-process-gpu"], stdout=raw, stderr=errors)
                    start = mark(events, "phase_start", phase="paused_map_interaction", mode=diagnostic_mode)
                    base.cue()
                    time.sleep(20)
                    base.cue()
                    end = mark(events, "phase_end", phase="paused_map_interaction")
                    try:
                        pm.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        pm.terminate(); pm.wait(timeout=5)
                if pm.returncode:
                    raise base.BenchmarkError("Interaction powermetrics capture failed")
                phases.append({"name": "paused_map_interaction", "start_ns": start["monotonic_ns"],
                               "end_ns": end["monotonic_ns"]})
                metadata["phases"] = phases
                report = write_report(run_dir, phases, anchor, game.pid)
            metadata["status"] = "complete"
            print(f"Root-cause report: {run_dir / 'root-cause.md'}")
            print("Quit this measured game session normally when finished.")
    except (base.BenchmarkError, OSError, KeyboardInterrupt) as exc:
        metadata["status"] = "incomplete"
        metadata["error"] = str(exc)
        raise
    finally:
        if pm is not None and pm.poll() is None:
            pm.terminate()
        metadata["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        metadata_path.write_text(json.dumps(metadata, indent=2, default=str) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="compile and verify telemetry in an offline x86_64 OpenGL harness")
    collect = sub.add_parser("run", help="launch GOG EU IV for the one diagnostic session")
    collect.add_argument("--output", default=str(base.ROOT / "results"))
    analyze = sub.add_parser("report", help="regenerate a root-cause report from one diagnostic run")
    analyze.add_argument("run_dir")
    args = parser.parse_args()
    try:
        if args.command == "preflight":
            print(json.dumps(preflight(), indent=2))
        elif args.command == "run":
            run(args)
        else:
            path = Path(args.run_dir).expanduser().resolve()
            metadata = json.loads((path / "metadata.json").read_text())
            write_report(path, metadata["phases"], metadata["clock_anchor"], metadata.get("game_pid"))
            print(path / "root-cause.md")
    except (base.BenchmarkError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
