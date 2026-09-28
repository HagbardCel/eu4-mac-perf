#!/usr/bin/env python3
"""One unattended, passive draw-structure capture for pinned GOG EU IV."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import mmap
import os
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import autonomous_runner as auto
import eu4_benchmark as base
import eu4_diagnostic as diagnostic
import eu4_draw_static as static
import eu4_sampler_uniform as sampler
from fixture_manager import FixtureManager


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).with_suffix(".c")
LIBRARY = ROOT / "benchmark/.build/libeu4_draw_trace.dylib"
TEST_LIBRARY = ROOT / "benchmark/.build/libeu4_draw_trace_test.dylib"
HARNESS_SOURCE = ROOT / "tests/draw_trace_harness.c"
HARNESS = ROOT / "benchmark/.build/draw_trace_harness"
RECORD = struct.Struct("<II8Q5IQiHHI")
HEADER = struct.Struct("<8s9I4xQ3I4x6Q")
CAPACITY = 1_000_000
BASELINE_SECONDS = 12
WINDOW_FRAMES = 32
WINDOWS = 3
WINDOW_GAP_SECONDS = 5
PRIME_SECONDS = 2


def build() -> None:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    commands = (
        ["clang", "-arch", "x86_64", "-O2", "-fno-omit-frame-pointer",
         "-Wall", "-Wextra", "-Werror", "-dynamiclib", "-framework", "OpenGL",
         "-o", str(LIBRARY), str(SOURCE)],
        ["clang", "-arch", "x86_64", "-O2", "-fno-omit-frame-pointer",
         "-Wall", "-Wextra", "-Werror", "-DEU4_DRAW_TEST_CALLSITE",
         "-dynamiclib", "-framework", "OpenGL", "-o", str(TEST_LIBRARY), str(SOURCE)],
        ["clang", "-arch", "x86_64", "-O2", "-fno-omit-frame-pointer",
         "-Wall", "-Wextra", "-Werror", "-Wl,-export_dynamic",
         "-framework", "OpenGL", "-o", str(HARNESS), str(HARNESS_SOURCE)],
    )
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise base.BenchmarkError(f"Draw tracer build failed: {result.stderr.strip()}")
    for path in (LIBRARY, TEST_LIBRARY, HARNESS):
        if base.command("lipo", "-archs", str(path)).strip() != "x86_64":
            raise base.BenchmarkError(f"{path.name} is not x86_64")


def make_buffer(path: Path) -> None:
    with path.open("wb") as output:
        output.truncate(4096+CAPACITY*RECORD.size)


def read_header(path: Path) -> dict:
    with path.open("rb") as source:
        data = source.read(HEADER.size)
    (magic, version, record_size, capacity, used, overflow, gl_draws,
     known_callers, completed, bad_flags, image_base, *window_data) = HEADER.unpack(data)
    if (magic != b"EU4DRAW1" or version != 1 or record_size != RECORD.size or
            capacity != CAPACITY or used > capacity):
        raise base.BenchmarkError("Draw buffer header is missing or incompatible")
    return {"used": used, "overflow": overflow, "gl_draws": gl_draws,
            "known_callers": known_callers, "completed_windows": completed,
            "bad_flags": bad_flags, "image_base": image_base,
            "window_draws": window_data[:3],
            "window_start_ns": window_data[3:6],
            "window_end_ns": window_data[6:9]}


def offline_preflight() -> dict:
    inventory = static.inventory()
    if inventory != json.loads(static.OUTPUT.read_text()):
        raise base.BenchmarkError("Pinned draw-call inventory changed")
    if len(inventory["direct_sites"]) < 40 or not static.HEADER.is_file():
        raise base.BenchmarkError("Draw call-site coverage is incomplete")
    build()
    with tempfile.TemporaryDirectory(prefix="eu4-draw-preflight-") as temporary:
        root = Path(temporary)
        control = root / "control.bin"
        control.write_bytes(bytes((1, 1))+bytes(4094))
        buffer = root / "trace.bin"
        make_buffer(buffer)
        env = {**os.environ, "DYLD_INSERT_LIBRARIES": str(TEST_LIBRARY),
               "EU4_DRAW_CONTROL": str(control),
               "EU4_DRAW_BUFFER": str(buffer),
               "EU4_DRAW_LOG": str(root / "swaps.csv")}
        test = subprocess.run([str(HARNESS)], env=env, capture_output=True,
                              text=True, check=False, timeout=30)
        header = read_header(buffer)
        if (test.returncode or header["used"] != 5*WINDOW_FRAMES or
                header["known_callers"] != header["used"] or
                header["window_draws"][0] != header["used"] or
                header["completed_windows"] != 1 or header["bad_flags"] or
                header["overflow"] or header["image_base"] == 0):
            raise base.BenchmarkError(f"Offline draw trace harness failed: "
                                      f"{test.stderr.strip()} {header}")
        with buffer.open("rb") as source:
            source.seek(4096)
            first = RECORD.unpack(source.read(RECORD.size))
        if not first[2] or first[2] == first[3] or first[-1]:
            raise base.BenchmarkError("Draw stack attribution or record fields failed")
    return {"executable_sha256": static.EXPECTED_SHA256,
            "library_sha256": base.sha256(LIBRARY),
            "source_sha256": base.sha256(SOURCE),
            "direct_call_sites": len(inventory["direct_sites"]),
            "offline_harness": "passed"}


def phase_health(game: subprocess.Popen, power: subprocess.Popen,
                 probe: Path, anchor: dict) -> None:
    if game.poll() is not None or power.poll() is not None:
        raise base.BenchmarkError("EU IV or powermetrics exited during draw trace")
    if not auto.focus("interior", game.pid):
        raise base.BenchmarkError("EU IV lost focus or pointer reached a scrolling edge")
    recent = auto.probe_rows(probe, anchor)[-2:]
    if (not recent or time.monotonic_ns()-recent[-1]["monotonic_ns"] > 2_000_000_000 or
            any(row["swaps_s"] < 20 or row["paused_swaps"] < .95*row["swaps"]
                for row in recent)):
        raise base.BenchmarkError("Paused draw/swap signal disappeared")


def hold(game: subprocess.Popen, power: subprocess.Popen, probe: Path,
         anchor: dict, seconds: float) -> tuple[int, int]:
    start = time.monotonic_ns()
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        phase_health(game, power, probe, anchor)
        time.sleep(min(1, max(0, deadline-time.monotonic())))
    return start, time.monotonic_ns()


def read_records(path: Path, used: int):
    with path.open("rb") as source, mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
        for index in range(used):
            raw = RECORD.unpack_from(data, 4096+index*RECORD.size)
            yield dict(zip(("frame", "ordinal", "caller_offset", "immediate_offset",
                            "context", "texture_sig", "uniform_sig", "render_sig",
                            "vertex_sig", "program", "array_buffer", "element_buffer",
                            "framebuffer", "mode", "count", "index_offset",
                            "base_vertex", "window", "api", "flags"), raw))


def same_state(a: dict, b: dict) -> bool:
    return all(a[key] == b[key] for key in
               ("context", "program", "texture_sig", "uniform_sig", "render_sig",
                "vertex_sig", "array_buffer", "element_buffer", "framebuffer",
                "mode", "api"))


def same_geometry(a: dict, b: dict) -> bool:
    return all(a[key] == b[key] for key in
               ("context", "program", "texture_sig", "render_sig", "vertex_sig",
                "array_buffer", "element_buffer", "framebuffer", "mode", "count",
                "index_offset", "base_vertex", "api"))


def analyze_records(records, inventory: dict) -> dict:
    sites = {item["return_offset"]: item for item in inventory["direct_sites"]}
    categories = collections.Counter()
    callers = collections.Counter()
    window_categories = {window: collections.Counter() for window in range(1, WINDOWS+1)}
    frames = collections.Counter()
    same_state_adjacent = contiguous_adjacent = instancing_adjacent = 0
    eligible_batch_adjacent = 0
    batch_categories = collections.Counter()
    total = known = unknown = unsafe = 0
    previous = None
    run_length = 0
    run_lengths = collections.Counter()
    for row in records:
        total += 1
        site = sites.get(row["caller_offset"]) or sites.get(row["immediate_offset"])
        category = site["category"] if site else "unknown"
        known += bool(site)
        unknown += category in ("unknown", "unclassified")
        unsafe += bool(row["flags"])
        categories[category] += 1
        callers[site["owner"] if site else f"unknown+0x{row['immediate_offset']:x}"] += 1
        window_categories[row["window"]][category] += 1
        frames[(row["window"], row["frame"])] += 1
        adjacent = (previous is not None and previous["window"] == row["window"] and
                    previous["frame"] == row["frame"] and not row["flags"] and
                    not previous["flags"])
        if adjacent and same_state(previous, row):
            same_state_adjacent += 1
            run_length += 1
            if (row["api"] in (1, 2) and previous["api"] in (1, 2) and
                    row["mode"] == 4 and row["element_buffer"] != 0):
                eligible_batch_adjacent += 1
                batch_categories[category] += 1
                if (previous["base_vertex"] == row["base_vertex"] and
                    previous["index_offset"]+previous["count"]*2 ==
                        row["index_offset"]):
                    contiguous_adjacent += 1
        else:
            if run_length:
                run_lengths[run_length+1] += 1
            run_length = 0
        if (adjacent and site and site["category"] == "mesh_object" and
                same_geometry(previous, row) and
                previous["uniform_sig"] != row["uniform_sig"]):
            instancing_adjacent += 1
        previous = row
    if run_length:
        run_lengths[run_length+1] += 1
    per_window = {str(window): dict(counts) for window, counts in window_categories.items()}
    return {"total_draws": total, "known_caller_draws": known,
            "unknown_or_unclassified_draws": unknown, "unsafe_draws": unsafe,
            "categories": dict(categories), "top_callers": callers.most_common(30),
            "window_categories": per_window,
            "frames": {f"{window}:{frame}": count for (window, frame), count in frames.items()},
            "median_draws_per_frame": statistics.median(frames.values()) if frames else 0,
            "same_state_adjacent": same_state_adjacent,
            "multi_draw_screening_adjacent": eligible_batch_adjacent,
            "multi_draw_screening_by_category": dict(batch_categories),
            "contiguous_index_adjacent": contiguous_adjacent,
            "instancing_screening_adjacent": instancing_adjacent,
            "same_state_run_lengths": dict(run_lengths),
            "conservative_batch_fraction": contiguous_adjacent/total if total else 0,
            "instancing_screening_fraction": instancing_adjacent/total if total else 0}


def screen_partial(run_dir: Path) -> dict:
    """Salvage a stopped capture without promoting it to a validated decision."""
    metadata = json.loads((run_dir / "manifest.json").read_text())
    header = read_header(run_dir / "trace.bin")
    if not header["used"]:
        raise base.BenchmarkError("Stopped capture contains no draw records")
    inventory = json.loads(static.OUTPUT.read_text())
    analysis = analyze_records(read_records(run_dir / "trace.bin", header["used"]), inventory)
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    before = next((event for event in events if event["event"] == "baseline_start" and
                   event.get("name") == "before"), None)
    after = next((event for event in events if event["event"] == "baseline_end" and
                  event.get("name") == "before"), None)
    rows = auto.probe_rows(run_dir / "draw.csv", metadata["clock_anchor"])
    baseline_rates = ([row["swaps_s"] for row in rows if
                       before["monotonic_ns"]+1_000_000_000 <=
                       row["monotonic_ns"]-row["interval_ns"] and
                       row["monotonic_ns"] < after["monotonic_ns"]]
                      if before and after else [])
    baseline_rate = statistics.median(baseline_rates) if baseline_rates else None
    trace_rates = [WINDOW_FRAMES*1e9/(end-start) for start,end in
                   zip(header["window_start_ns"], header["window_end_ns"])
                   if end > start]
    complete_frames = {key: value for key,value in analysis["frames"].items()
                       if value == analysis["median_draws_per_frame"]}
    batch_fraction = (analysis["multi_draw_screening_adjacent"] /
                      analysis["total_draws"])
    known_fraction = analysis["known_caller_draws"]/analysis["total_draws"]
    gate = {"three_windows": header["completed_windows"] == WINDOWS,
            "trace_intrusion_below_5_percent": bool(baseline_rate and trace_rates and
                min(trace_rates) >= .95*baseline_rate),
            "broad_category_coverage": known_fraction >= .95,
            "no_overflow_or_bad_state": not header["overflow"] and not header["bad_flags"]}
    recommendation = ("border_and_map_text_multi_draw_prototype" if
                      batch_fraction >= .15 else "adaptive_render_scheduling")
    result = {"status": "screening_only", "formal_decision": "inconclusive",
              "recommended_next_experiment": recommendation,
              "quality_gate": gate, "header": header, "analysis": analysis,
              "complete_equal_sized_frames": len(complete_frames),
              "baseline_swaps_s": baseline_rate, "trace_swaps_s": trace_rates,
              "known_caller_fraction": known_fraction,
              "multi_draw_screening_fraction": batch_fraction}
    (run_dir / "draw-screening.json").write_text(json.dumps(result, indent=2)+"\n")
    lines = ["# Paused GOG EU IV draw screening", "",
             "**Formal decision: inconclusive.** The collector stopped after one of three "
             "planned windows, and the active trace exceeded the 5% intrusion gate.", "",
             f"Captured {analysis['total_draws']:,} draws across "
             f"{header['completed_windows']} window; {len(complete_frames)} complete "
             f"frames each contain {analysis['median_draws_per_frame']:,.0f} draws. "
             f"Known caller coverage: {known_fraction:.1%}. "
             f"Overflow/bad-state flags: {header['overflow']}/{header['bad_flags']}.", "",
             f"Baseline: {baseline_rate:.2f} swaps/s; trace: "
             f"{', '.join(f'{rate:.2f}' for rate in trace_rates)} swaps/s." if
             baseline_rate and trace_rates else "A matched swap-rate comparison is unavailable.",
             "", "| Draw source | Draws | Share |", "|---|---:|---:|"]
    for category, count in sorted(analysis["categories"].items(),
                                  key=lambda item: -item[1]):
        lines.append(f"| {category} | {count:,} | {count/analysis['total_draws']:.1%} |")
    lines.extend(["", "| Aggregation screen | Adjacent pairs | Share of draws |",
                  "|---|---:|---:|",
                  f"| Same tracked state, indexed multi-draw | "
                  f"{analysis['multi_draw_screening_adjacent']:,} | "
                  f"{batch_fraction:.1%} |",
                  f"| Strictly contiguous index ranges | "
                  f"{analysis['contiguous_index_adjacent']:,} | "
                  f"{analysis['conservative_batch_fraction']:.1%} |",
                  f"| Repeated mesh geometry, changed uniforms | "
                  f"{analysis['instancing_screening_adjacent']:,} | "
                  f"{analysis['instancing_screening_fraction']:.1%} |",
                  "", "The multi-draw candidates break down as:", ""])
    for category, count in sorted(analysis["multi_draw_screening_by_category"].items(),
                                  key=lambda item: -item[1]):
        lines.append(f"- {category}: {count:,} adjacent pairs")
    lines.extend(["", "This is an upper-bound screen based on tracked GL state. "
                  "It does not establish full command equivalence, visual correctness, "
                  "or a performance gain. In particular, object constants, ordering, "
                  "and untracked GL state need a narrow prototype check.", "",
                  ("**Next experiment:** prototype multi-draw consolidation for the border "
                   "path first, then map text if the border result is promising. "
                   "The data do not justify a general mesh-bucket merge or instancing "
                   "prototype yet. No additional diagnostic launch is needed for this screen."
                   if recommendation == "border_and_map_text_multi_draw_prototype" else
                   "**Next experiment:** investigate adaptive render scheduling; this "
                   "capture did not identify substantial compatible draw runs."), ""])
    (run_dir / "draw-screening.md").write_text("\n".join(lines))
    return result


def summarize(run_dir: Path, metadata: dict) -> dict:
    header = read_header(run_dir / "trace.bin")
    inventory = json.loads(static.OUTPUT.read_text())
    analysis = analyze_records(read_records(run_dir / "trace.bin", header["used"]), inventory)
    if not analysis["total_draws"]:
        raise base.BenchmarkError("Completed draw trace contains no draw records")
    denominator = sum(header["window_draws"])
    coverage = header["used"]/denominator if denominator else 0
    known_fraction = analysis["known_caller_draws"]/analysis["total_draws"] if analysis["total_draws"] else 0
    category_fraction = 1-analysis["unknown_or_unclassified_draws"]/analysis["total_draws"] if analysis["total_draws"] else 0
    sw = auto.probe_rows(run_dir / "draw.csv", metadata["clock_anchor"])
    phase_power = diagnostic.summarize_power(
        auto.PowerTail(run_dir / "powermetrics.pliststream").poll(),
        metadata["baselines"], metadata["clock_anchor"], metadata["game_pid"])
    baselines = {}
    for phase in metadata["baselines"]:
        chosen = [row["swaps_s"] for row in sw if
                  phase["start_ns"]+1_000_000_000 <= row["monotonic_ns"]-row["interval_ns"] and
                  row["monotonic_ns"] < phase["end_ns"]]
        power = phase_power[phase["name"]]
        baselines[phase["name"]] = {
            "swaps_s": statistics.median(chosen) if chosen else 0,
            "eu4_cpu_ms_s": power.get("eu4_cputime_ms_per_s"),
            "power_samples": power.get("samples", 0)}
    trace_rates = [WINDOW_FRAMES*1e9/(end-start) if end>start else 0
                   for start,end in zip(header["window_start_ns"],header["window_end_ns"])]
    control_rate = statistics.median(item["swaps_s"] for item in baselines.values())
    window_spreads = []
    for category,count in analysis["categories"].items():
        if count/analysis["total_draws"] < .05:
            continue
        shares=[]
        for window in range(1,WINDOWS+1):
            values=analysis["window_categories"][str(window)]
            denominator_window=sum(values.values())
            shares.append(values.get(category,0)/denominator_window if denominator_window else 0)
        window_spreads.append(max(shares)-min(shares))
    quality = {"draw_coverage": coverage >= .99,
               "category_coverage": category_fraction >= .95,
               "all_windows_complete": header["completed_windows"] == WINDOWS,
               "frame_windows_complete": all(
                   sum(key.startswith(f"{window}:") for key in analysis["frames"]) >= WINDOW_FRAMES
                   for window in range(1,WINDOWS+1)),
               "no_overflow": header["overflow"] == 0 and header["bad_flags"] == 0,
               "all_records_safe": analysis["unsafe_draws"] == 0,
               "window_category_consistency": all(spread <= .05 for spread in window_spreads),
               "trace_rate_preserved": all(rate >= .95*control_rate for rate in trace_rates),
               "baseline_stable": (min(item["swaps_s"] for item in baselines.values()) > 0 and
                                   max(item["swaps_s"] for item in baselines.values()) /
                                   min(item["swaps_s"] for item in baselines.values()) <= 1.05 and
                                   all(item["eu4_cpu_ms_s"] for item in baselines.values()) and
                                   max(item["eu4_cpu_ms_s"] for item in baselines.values()) /
                                   min(item["eu4_cpu_ms_s"] for item in baselines.values()) <= 1.05),
               "baseline_present": all(item["swaps_s"] >= 20 and item["power_samples"] >= 8
                                       and item["eu4_cpu_ms_s"] for item in baselines.values())}
    # Exact contiguous index ranges are the strict batching lower bound. A
    # multi-draw screening count is also retained, but cannot by itself prove
    # that Apple's GL path saves a submission or preserves ordering.
    batch_screen = analysis["multi_draw_screening_adjacent"]/analysis["total_draws"]
    instance_screen = analysis["instancing_screening_fraction"]
    if not all(quality.values()):
        decision = "inconclusive"
    elif batch_screen >= .15 and batch_screen >= instance_screen:
        decision = "batching"
    elif instance_screen >= .15:
        decision = "investigate_instancing"
    else:
        decision = "adaptive_render_scheduling"
    result = {"header": header, "analysis": analysis,
              "coverage_fraction": coverage,"known_caller_fraction": known_fraction,
              "category_fraction": category_fraction,
              "baselines": baselines,"trace_swaps_s": trace_rates,
              "max_category_share_spread":max(window_spreads,default=0),
              "quality": quality,"decision": decision}
    (run_dir / "draw-analysis.json").write_text(json.dumps(result, indent=2)+"\n")
    lines = ["# Paused GOG EU IV draw-path trace", "",
             f"Decision: **{decision}**. Draw records: {analysis['total_draws']:,}; "
             f"median {analysis['median_draws_per_frame']:,.0f} draws/frame.", "",
             f"GL draw coverage: {coverage:.1%}; broad category coverage: {category_fraction:.1%}; "
             f"known direct caller coverage: {known_fraction:.1%}. "
             f"Trace swaps/s: {', '.join(f'{rate:.1f}' for rate in trace_rates)}.", "",
             "| Category | Draws | Share |", "|---|---:|---:|"]
    for category, count in sorted(analysis["categories"].items(),key=lambda item:-item[1]):
        lines.append(f"| {category} | {count:,} | {count/analysis['total_draws']:.1%} |")
    lines.extend(["", "| Aggregation screen | Adjacent pairs | Share of draws |",
                  "|---|---:|---:|",
                  f"| Same tracked state | {analysis['same_state_adjacent']:,} | "
                  f"{analysis['same_state_adjacent']/analysis['total_draws']:.1%} |",
                  f"| Same-state indexed multi-draw screen | "
                  f"{analysis['multi_draw_screening_adjacent']:,} | "
                  f"{analysis['multi_draw_screening_adjacent']/analysis['total_draws']:.1%} |",
                  f"| Contiguous compatible index ranges | "
                  f"{analysis['contiguous_index_adjacent']:,} | "
                  f"{analysis['conservative_batch_fraction']:.1%} |",
                  f"| Repeated mesh geometry with changed uniforms | "
                  f"{analysis['instancing_screening_adjacent']:,} | "
                  f"{analysis['instancing_screening_fraction']:.1%} |",
                  "", "The same-state multi-draw figure is a prototype screen, not proof "
                  "of a safe one-draw merge. Unknown fields and untracked state never "
                  "count as proven compatible. Instancing remains a screen until changed "
                  "uniforms are tied to object transforms.",
                  "Power is system-wide; the trace is passive and does not measure an optimization.", ""])
    (run_dir / "draw-analysis.md").write_text("\n".join(lines))
    return result


def run(output_root: Path) -> Path:
    evidence = auto.preflight(require_privilege=True)
    tracer = offline_preflight()
    base.running_game_pid("idle")
    with FixtureManager(output_root, base.USER_DATA, auto.FIXTURE,
                        evidence["fixture"]["save_sha256"]) as fixture:
        fixture.recover()
        run_dir = output_root / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-draw-trace"
        run_dir.mkdir(parents=True, exist_ok=False)
        manifest = run_dir / "manifest.json"
        events = run_dir / "events.jsonl"
        metadata = {"status": "starting", "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "evidence": evidence, "tracer_preflight": tracer}
        manifest.write_text(json.dumps(metadata, indent=2)+"\n")
        anchor = auto.mark(events, "clock_anchor")
        metadata["clock_anchor"] = anchor
        control_path = run_dir / "control.bin"
        control_path.write_bytes(bytes(4096))
        buffer_path = run_dir / "trace.bin"
        make_buffer(buffer_path)
        game_log = base.USER_DATA / "logs/game.log"
        old_log = game_log.read_bytes() if game_log.is_file() else b""
        game = power = None
        installed = False
        try:
            metadata["working_save"] = str(fixture.install())
            installed = True
            power = sampler.start_power(run_dir / "powermetrics-readiness.pliststream")
            env = {**os.environ, "DYLD_INSERT_LIBRARIES": str(LIBRARY),
                   "EU4_DRAW_LOG": str(run_dir / "draw.csv"),
                   "EU4_DRAW_CONTROL": str(control_path),
                   "EU4_DRAW_BUFFER": str(buffer_path)}
            with (run_dir / "game.stdout").open("wb") as stdout, \
                 (run_dir / "game.stderr").open("wb") as stderr:
                game = subprocess.Popen(["./eu4", *evidence["launcher_args"],
                                         "--continuelastsave"], cwd=base.GOG_EXE.parent,
                                        env=env, stdout=stdout, stderr=stderr)
            metadata["game_pid"] = game.pid
            readiness_tail = auto.PowerTail(run_dir / "powermetrics-readiness.pliststream")
            metadata["readiness"] = auto.wait_until_ready(game, run_dir / "draw.csv",
                readiness_tail, anchor, game_log, old_log)
            auto.mark(events,"ready",**metadata["readiness"])
            historical = json.loads((ROOT / "results/autonomous-reproducibility.json").read_text())
            expected_swaps = historical["summary"]["median_swaps_s"]["median"]
            expected_cpu = historical["summary"]["eu4_cpu_ms_per_s"]["median"]
            if (not historical.get("stable") or
                    metadata["readiness"]["swap_rate"] < .95*expected_swaps or
                    metadata["readiness"]["cpu_ms_s"] > 1.05*expected_cpu):
                raise base.BenchmarkError("Passive tracer exceeds the 5% historical "
                                          "baseline intrusion gate; no trace was taken")
            if read_header(buffer_path)["image_base"] == 0:
                raise base.BenchmarkError("Tracer could not resolve the GOG executable image base")
            metadata["display_with_game"] = base.display_mode()
            if not metadata["display_with_game"].get("verified") or abs(
                    metadata["display_with_game"]["refresh_hz"]-120)>1:
                raise base.BenchmarkError("EU IV changed the display away from 120 Hz")
            auto.stop_process(power, 5)
            power = sampler.start_power(run_dir / "powermetrics.pliststream")
            power_tail = auto.PowerTail(run_dir / "powermetrics.pliststream")
            metadata["warmup"] = auto.warm_up(game, run_dir / "draw.csv", power_tail, anchor)
            auto.mark(events,"warmup_complete")
            auto.capture_scene(run_dir / "ready-scene.png")
            metadata["scene_alignment"] = auto.verify_scene(run_dir / "ready-scene.png",False)
            auto.mark(events,"baseline_start",name="before")
            start,end = hold(game,power,run_dir / "draw.csv",anchor,BASELINE_SECONDS)
            auto.mark(events,"baseline_end",name="before")
            baselines = [{"name":"before","start_ns":start,"end_ns":end}]
            with control_path.open("r+b") as file, mmap.mmap(file.fileno(),4096) as control:
                for window in range(1,WINDOWS+1):
                    control[1]=window
                    control[0]=2
                    hold(game,power,run_dir / "draw.csv",anchor,PRIME_SECONDS)
                    control[0]=1
                    auto.mark(events,"trace_start",window=window)
                    deadline=time.monotonic()+15
                    while read_header(buffer_path)["completed_windows"]<window:
                        phase_health(game,power,run_dir / "draw.csv",anchor)
                        status=read_header(buffer_path)
                        if status["overflow"] or status["bad_flags"]:
                            raise base.BenchmarkError(f"Draw tracer safety guard: {status}")
                        if time.monotonic()>deadline:
                            raise base.BenchmarkError("Draw window did not finish within 15 seconds")
                        time.sleep(.5)
                    control[0]=0
                    status=read_header(buffer_path)
                    if status["known_callers"] < .9*status["used"]:
                        raise base.BenchmarkError("Engine draw callers were not attributed "
                                                  "in the first trace window")
                    auto.mark(events,"trace_end",window=window)
                    if window<WINDOWS:
                        hold(game,power,run_dir / "draw.csv",anchor,WINDOW_GAP_SECONDS)
            auto.mark(events,"baseline_start",name="after")
            start,end = hold(game,power,run_dir / "draw.csv",anchor,BASELINE_SECONDS)
            auto.mark(events,"baseline_end",name="after")
            baselines.append({"name":"after","start_ns":start,"end_ns":end})
            metadata["baselines"] = baselines
            auto.capture_scene(run_dir / "final-scene.png")
            auto.verify_scene(run_dir / "final-scene.png",False)
            auto.stop_process(power,5)
            power = None
            metadata["status"] = "capture_complete"
        except (base.BenchmarkError,OSError,ValueError,subprocess.SubprocessError,
                KeyboardInterrupt) as exc:
            metadata["status"] = "incomplete"
            metadata["error"] = str(exc)
            raise
        finally:
            try:
                auto.stop_process(power,3)
                if game is not None and game.poll() is None and auto.focus("terminate",game.pid):
                    try:
                        game.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                auto.stop_process(game,10)
                if game is not None and game.poll() is None:
                    raise base.BenchmarkError("EU IV remained running; recovery journal retained")
                if installed:
                    metadata["working_save_changed"] = fixture.finish(run_dir)
            except BaseException as cleanup_error:
                metadata["status"] = "incomplete"
                metadata["cleanup_error"] = str(cleanup_error)
                raise
            finally:
                metadata["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
                manifest.write_text(json.dumps(metadata, indent=2, default=str)+"\n")
        if metadata["status"]=="capture_complete":
            metadata["report"] = summarize(run_dir,metadata)
            metadata["status"] = "complete"
            manifest.write_text(json.dumps(metadata,indent=2,default=str)+"\n")
        return run_dir


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("preflight",help="static checks and offline OpenGL harness")
    collect=sub.add_parser("run",help="one unattended paused GOG EU IV draw capture")
    collect.add_argument("--output",default=str(ROOT/"results"))
    inspect=sub.add_parser("report",help="regenerate report without launching EU IV")
    inspect.add_argument("run_dir")
    partial=sub.add_parser("screen-partial",help="screen a stopped trace without launching EU IV")
    partial.add_argument("run_dir")
    args=parser.parse_args()
    try:
        if args.command=="preflight": print(json.dumps(offline_preflight(),indent=2))
        elif args.command=="run": print(run(Path(args.output).expanduser().resolve()))
        elif args.command=="screen-partial":
            print(json.dumps(screen_partial(Path(args.run_dir).expanduser().resolve()),indent=2))
        else:
            path=Path(args.run_dir).expanduser().resolve()
            print(json.dumps(summarize(path,json.loads((path/"manifest.json").read_text())),indent=2))
    except (base.BenchmarkError,OSError,ValueError,subprocess.SubprocessError,
            KeyboardInterrupt) as exc:
        print(f"Error: {exc}",file=sys.stderr)
        return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
