#!/usr/bin/env python3
"""One-launch GOG EU IV A-B-A-C-A-D GL state-cache validation."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import mmap
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import eu4_benchmark as base
import eu4_diagnostic as diagnostic


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).with_suffix(".c")
LIBRARY = SOURCE.parent / ".build/libeu4_state_cache.dylib"
HARNESS = ROOT / "tests/state_cache_harness.c"
HARNESS_EXE = SOURCE.parent / ".build/state_cache_harness"
GOG_SHA256 = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
PHASES = (("a1", 0), ("b", 1), ("a2", 0), ("c", 2), ("a3", 0), ("d", 3))
SECONDS = 20
GLEW_STATE_REFERENCES = {
    "ActiveTextureARB", "BindBufferARB", "DeleteBuffersARB",
    "DisableVertexAttribArrayARB", "EnableVertexAttribArrayARB",
    "LinkProgramARB", "DeleteObjectARB", "Uniform1i", "Uniform4fvARB",
    "UseProgramObjectARB", "VertexAttribDivisorARB", "VertexAttribPointerARB",
}


def actual_glew_state_references() -> set[str]:
    """Ignore GLEW's pointer-initialization stores; inspect real binary uses."""
    command = ["xcrun", "llvm-objdump", "-d", "--symbolize-operands", str(base.GOG_EXE)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    seen = set()
    for line in process.stdout:
        if "___glew" not in line or re.search(r"movq\s+%rax,", line):
            continue
        match = re.search(r"<___glew([^>]+)>", line)
        if match:
            seen.add(match.group(1))
    stderr = process.stderr.read() if process.stderr else ""
    if process.wait():
        raise base.BenchmarkError(f"Could not audit GOG GLEW call sites: {stderr.strip()}")
    prefixes = ("ActiveTexture", "BindBuffer", "DeleteBuffers", "BindTexture",
                "TexParameter", "TextureParameter", "TexEnv", "BindVertexArray",
                "BindVertexBuffer", "EnableVertexAttribArray", "DisableVertexAttribArray",
                "VertexAttribPointer", "VertexAttribIPointer", "VertexAttribLPointer",
                "VertexAttribDivisor", "Uniform", "ProgramUniform", "UseProgram",
                "LinkProgram", "DeleteObject")
    relevant = {name for name in seen if name.startswith(prefixes)}
    if relevant != GLEW_STATE_REFERENCES:
        raise base.BenchmarkError(f"GOG GLEW state call sites changed: "
                                  f"missing {sorted(GLEW_STATE_REFERENCES-relevant)}, "
                                  f"uncovered {sorted(relevant-GLEW_STATE_REFERENCES)}")
    return relevant


def build() -> None:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    for source, output, extra in ((SOURCE, LIBRARY, ["-dynamiclib"]),
                                  (HARNESS, HARNESS_EXE, [])):
        command = ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
                   *extra, "-framework", "OpenGL", "-o", str(output), str(source)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise base.BenchmarkError(f"Could not build {source.name}: {result.stderr.strip()}")
        if base.command("lipo", "-archs", str(output)).strip() != "x86_64":
            raise base.BenchmarkError(f"{output.name} is not x86_64")


def rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    result = []
    with path.open(newline="") as source:
        for row in csv.reader(source):
            if len(row) not in (14, 16) or row[0] != "S":
                continue
            try:
                values = list(map(int, row[1:]))
                if values[3] <= 0:
                    continue
                item = dict(zip(("clock_ns", "wall_ns", "mode", "interval_ns",
                                        "swaps", "texture_forwarded", "texture_suppressed",
                                        "vertex_forwarded", "vertex_suppressed",
                                        "uniform_forwarded", "uniform_suppressed",
                                        "multiple_contexts", "overflow",
                                        "context_switches", "other_thread_calls"), values))
                result.append(item)
            except ValueError:
                continue
    return result


def preflight() -> dict:
    identity = base.gog_identity()
    if identity["executable_sha256"] != GOG_SHA256:
        raise base.BenchmarkError("GOG executable changed; review the version-pinned renderer analysis")
    build()
    direct = base.command("nm", "-u", str(base.GOG_EXE))
    required = {"_glBindTexture", "_glTexEnvf", "_glTexParameteri", "_glTexParameterf",
                "_glDeleteTextures"}
    if not required.issubset(set(direct.split())):
        raise base.BenchmarkError("GOG direct OpenGL import set changed")
    state_references = actual_glew_state_references()
    with tempfile.TemporaryDirectory(prefix="eu4-cache-preflight-") as temporary:
        root = Path(temporary)
        log, control = root / "state.csv", root / "control.bin"
        control.write_bytes(bytes(4096))
        env = os.environ.copy()
        env.update(DYLD_INSERT_LIBRARIES=str(LIBRARY), EU4_CACHE_LOG=str(log),
                   EU4_CACHE_CONTROL=str(control))
        test = subprocess.run([str(HARNESS_EXE)], env=env, capture_output=True,
                              text=True, check=False, timeout=30)
        if test.returncode:
            raise base.BenchmarkError(f"Offline GL harness failed ({test.returncode}): "
                                      f"{test.stderr.strip() or test.stdout.strip()}")
        observed = rows(log)
        by_mode = {mode: [r for r in observed if r["mode"] == mode]
                   for mode in range(4)}
        if any(not group for group in by_mode.values()):
            raise base.BenchmarkError("Offline harness did not log every cache mode")
        if any(any(r[f"{family}_suppressed"] for family in
                   ("texture", "vertex", "uniform")) for r in by_mode[0]):
            raise base.BenchmarkError("Pass-through mode suppressed a GL call")
        for mode, family in ((1, "texture"), (2, "vertex"), (3, "uniform")):
            if sum(r[f"{family}_suppressed"] for r in by_mode[mode]) == 0:
                raise base.BenchmarkError(f"Offline harness did not exercise {family} suppression")
        if any(r["multiple_contexts"] for r in observed):
            raise base.BenchmarkError("Offline harness unexpectedly encountered multiple GL contexts")
        context_log = root / "context.csv"
        context_env = {**env, "EU4_CACHE_LOG": str(context_log)}
        context_test = subprocess.run([str(HARNESS_EXE), "--contexts"], env=context_env,
                                      capture_output=True, text=True, check=False, timeout=15)
        context_rows = rows(context_log)
        if (context_test.returncode or
                not any(r.get("context_switches", 0) for r in context_rows) or
                not any(r["texture_suppressed"] for r in context_rows) or
                any(r["multiple_contexts"] for r in context_rows)):
            raise base.BenchmarkError("Multi-context harness did not preserve safe caching and shared state")
        uniform_log = root / "uniform.csv"
        uniform_env = {**env, "EU4_CACHE_LOG": str(uniform_log)}
        uniform_test = subprocess.run([str(HARNESS_EXE), "--uniform-invalidation"],
                                      env=uniform_env, capture_output=True,
                                      text=True, check=False, timeout=15)
        uniform_rows = rows(uniform_log)
        uniform_skipped = sum(r["uniform_suppressed"] for r in uniform_rows)
        if (uniform_test.returncode or
                uniform_skipped < 1000):
            raise base.BenchmarkError("Uniform invalidation harness failed "
                                      f"(exit={uniform_test.returncode}, skipped={uniform_skipped}): "
                                      f"{uniform_test.stderr.strip() or uniform_test.stdout.strip()}")
        stress = {"bare": [], "pass_through": []}
        for _ in range(3):
            for name, extra_env in (("bare", {}), ("pass_through", env)):
                timed = subprocess.run([str(HARNESS_EXE), "--stress"],
                                       env={**os.environ, **extra_env}, capture_output=True,
                                       text=True, check=False, timeout=30)
                if timed.returncode:
                    raise base.BenchmarkError(f"Offline {name} stress run failed: {timed.stderr.strip()}")
                stress[name].append(int(timed.stdout.strip().split("stress_ns=")[-1]))
        stress = {name: statistics.median(samples) for name, samples in stress.items()}
        overhead = 100 * (stress["pass_through"] / stress["bare"] - 1)
        # Stress submits one draw after 20 state calls. The diagnostic saw about
        # 5,700 draws at 48 swaps/s, i.e. a 20.83 ms observed frame interval.
        # This compares the extra work for that call mix with the actual frame
        # interval rather than the much shorter synthetic GL-only loop.
        projected_ns_per_frame = max(0, stress["pass_through"] - stress["bare"]) / 1_000_000 * 5700
        frame_overhead = 100 * projected_ns_per_frame / (1e9 / 48)
        if frame_overhead > 5:
            raise base.BenchmarkError(f"Projected pass-through cost {frame_overhead:.1f}% of "
                                      f"the diagnostic frame exceeds 5% budget (raw loop {overhead:.1f}%)")
    return {"gog": identity, "library_sha256": base.sha256(LIBRARY),
            "glew_state_call_sites": sorted(state_references),
            "harness": "passed", "raw_stress_overhead_pct": round(overhead, 2),
            "projected_frame_overhead_pct": round(frame_overhead, 2),
            "projected_ns_per_frame": round(projected_ns_per_frame),
            "suppressed_by_mode": {str(mode): {family: sum(r[f"{family}_suppressed"]
                                for r in group) for family in ("texture", "vertex", "uniform")}
                                for mode, group in by_mode.items()}}


def mark(path: Path, event: str, **values: object) -> dict:
    item = {"event": event, "monotonic_ns": time.monotonic_ns(),
            "wall_ns": time.time_ns(), **values}
    with path.open("a") as output:
        output.write(json.dumps(item, sort_keys=True) + "\n")
    return item


def phases_from_events(path: Path) -> list[dict]:
    events = [json.loads(line) for line in path.read_text().splitlines() if line]
    starts = {item["phase"]: item for item in events if item.get("event") == "phase_start"}
    ends = {item["phase"]: item for item in events if item.get("event") == "phase_end"}
    if set(starts) != {name for name, _ in PHASES} or set(ends) != set(starts):
        raise base.BenchmarkError("The run lacks a complete set of six phase markers")
    phases = []
    for name, selected_mode in PHASES:
        start, end = starts[name], ends[name]
        if (start["mode"] != selected_mode or
                end["monotonic_ns"] <= start["monotonic_ns"]):
            raise base.BenchmarkError(f"Invalid phase markers for {name}")
        phases.append({"name": name, "mode": selected_mode,
                       "start_ns": start["monotonic_ns"],
                       "end_ns": end["monotonic_ns"]})
    return phases


def summarize_rows(raw: list[dict], phases: list[dict], anchor: dict) -> dict:
    if not raw:
        raise base.BenchmarkError("No state-cache snapshots were written")
    offset = statistics.median_low([r["wall_ns"] - r["clock_ns"] for r in raw])
    offset += anchor["monotonic_ns"] - anchor["wall_ns"]
    output = {}
    for phase in phases:
        selected = [r for r in raw if r["mode"] == phase["mode"] and
                    phase["start_ns"] + 3_000_000_000 <= r["clock_ns"] + offset < phase["end_ns"]]
        output[phase["name"]] = {
            "samples": len(selected),
            "median_swaps_s": round(statistics.median(r["swaps"] * 1e9 / r["interval_ns"]
                                                        for r in selected), 2) if selected else None,
            **{f"{family}_{verb}": sum(r[f"{family}_{verb}"] for r in selected)
               for family in ("texture", "vertex", "uniform")
               for verb in ("forwarded", "suppressed")},
            "multiple_contexts": any(r["multiple_contexts"] for r in selected),
            "shadow_overflow": sum(r["overflow"] for r in selected),
            "context_switches": sum(r.get("context_switches", 0) for r in selected),
            "other_thread_gl_calls": sum(r.get("other_thread_calls", 0) for r in selected),
        }
    return output


def write_report(run_dir: Path, phases: list[dict], anchor: dict, game_pid: int,
                 visual_defect: bool | None = None, save_changed: bool = False) -> dict:
    gl = summarize_rows(rows(run_dir / "state.csv"), phases, anchor)
    power = diagnostic.summarize_power(base.read_plist_stream(
        run_dir / "powermetrics.pliststream"), phases, anchor, game_pid)
    baselines = {"b": ("a1", "a2"), "c": ("a2", "a3"), "d": ("a3",)}
    metrics = ("eu4_cputime_ms_per_s", "combined_w", "cpu_w", "gpu_w", "median_swaps_s")
    comparison = {}
    for candidate, controls in baselines.items():
        comparison[candidate] = {}
        for metric in metrics:
            source = gl if metric == "median_swaps_s" else power
            prior = [source[name].get(metric) for name in controls]
            tested = source[candidate].get(metric)
            baseline = statistics.median(prior) if all(isinstance(v, (int, float)) for v in prior) else None
            comparison[candidate][metric] = {
                "baseline": baseline, "candidate": tested,
                "change_pct": round(100 * (tested / baseline - 1), 2)
                if baseline not in (None, 0) and isinstance(tested, (int, float)) else None}
    baseline_values = [power[name].get("eu4_cputime_ms_per_s") for name in ("a1", "a2", "a3")]
    stable = (all(isinstance(x, (int, float)) and x > 0 for x in baseline_values) and
              max(baseline_values) / min(baseline_values) <= 1.05)
    baseline_watts = [power[name].get("combined_w") for name in ("a1", "a2", "a3")]
    stable_watts = (all(isinstance(x, (int, float)) and x > 0 for x in baseline_watts) and
                    max(baseline_watts) / min(baseline_watts) <= 1.05)
    quality = {"adequate_samples": all(gl[p["name"]]["samples"] >= 15 and
                                       power[p["name"]].get("samples", 0) >= 15 for p in phases),
               "stable_baseline_cpu": stable,
               "stable_baseline_power": stable_watts,
               "single_context": not any(item["multiple_contexts"] for item in gl.values()),
               "engaged": {"b": gl["b"]["texture_suppressed"] > 0,
                           "c": gl["c"]["vertex_suppressed"] > 0,
                           "d": gl["d"]["uniform_suppressed"] > 0},
               "visual_defect_reported": visual_defect,
               "save_changed_accepted": save_changed}
    decisions = {}
    for name in ("b", "c", "d"):
        cpu = comparison[name]["eu4_cputime_ms_per_s"]["change_pct"]
        watts = comparison[name]["combined_w"]["change_pct"]
        swaps = comparison[name]["median_swaps_s"]["change_pct"]
        usable = (quality["adequate_samples"] and quality["stable_baseline_cpu"] and
                  quality["stable_baseline_power"] and
                  quality["single_context"] and gl[name]["shadow_overflow"] <= 100 and
                  visual_defect is False and
                  quality["engaged"][name] and
                  isinstance(swaps, (int, float)) and swaps >= -3)
        benefit = any(isinstance(value, (int, float)) and value <= -5 for value in (cpu, watts))
        decisions[name] = ("promising" if usable and benefit else
                           "no_measured_benefit" if usable else "inconclusive")
    output = {"phases": {name: {**item, "power": power[name]} for name, item in gl.items()},
              "comparison": comparison, "quality": quality, "decision": decisions}
    lines = ["# GOG EU IV state-cache validation", "",
             "One paused, hands-off A–B–A–C–A–D launch; B=texture, C=texture+vertex, D=texture+vertex+uniform."]
    if not any(quality["engaged"].values()):
        lines.extend(["", "**No cache stage engaged. These power comparisons do not measure state deduplication.**"])
    lines.extend(["", "| Phase | Swaps/s | EU IV CPU ms/s | CPU W | GPU W | Texture skipped | Vertex skipped | Uniform skipped |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"])
    for name, _ in PHASES:
        item, p = gl[name], power[name]
        lines.append(f"| {name} | {item['median_swaps_s']} | {p.get('eu4_cputime_ms_per_s')} | "
                     f"{p.get('cpu_w')} | {p.get('gpu_w')} | {item['texture_suppressed']:,} | "
                     f"{item['vertex_suppressed']:,} | {item['uniform_suppressed']:,} |")
    lines.extend(["", "## Paired comparison", ""])
    for candidate in baselines:
        values = comparison[candidate]
        lines.append(f"- {candidate}: EU IV CPU {values['eu4_cputime_ms_per_s']['change_pct']}%, "
                     f"combined power {values['combined_w']['change_pct']}%, "
                     f"swaps {values['median_swaps_s']['change_pct']}% versus adjacent A phase(s); "
                     f"{decisions[candidate]}.")
    lines.extend(["", "## Quality", "",
                  f"- At least 15 aligned power and swap samples per phase: {quality['adequate_samples']}",
                  f"- A-phase EU IV CPU drift at most 5%: {quality['stable_baseline_cpu']}",
                  f"- A-phase combined-power drift at most 5%: {quality['stable_baseline_power']}",
                  f"- Global context/thread fail-open guard inactive: {quality['single_context']}",
                  f"- Context switches by phase: "
                  f"{ {name: item['context_switches'] for name, item in gl.items()} }",
                  f"- GL calls on other threads by phase: "
                  f"{ {name: item['other_thread_gl_calls'] for name, item in gl.items()} }",
                  f"- Shadow-table overflow by phase: "
                  f"{ {name: item['shadow_overflow'] for name, item in gl.items()} }",
                  f"- Family suppression engaged: {quality['engaged']}",
                  f"- Visible rendering defect reported: {visual_defect}",
                  f"- Save file changed after capture; accepted for this analysis: {save_changed}",
                  "- D has only a preceding A control; treat its effect as less certain.",
                  "- Powermetrics CPU/GPU watts are system-wide estimates; swaps are an in-process frame proxy.", ""])
    (run_dir / "validation.json").write_text(json.dumps(output, indent=2) + "\n")
    (run_dir / "validation.md").write_text("\n".join(lines))
    return output


def run(args: argparse.Namespace) -> None:
    evidence = preflight()
    try:
        base.running_game_pid("idle")
    except base.BenchmarkError as exc:
        raise base.BenchmarkError("Close EU IV before the controlled GOG launch") from exc
    save = base.DEFAULT_SAVE
    if not save.is_file():
        raise base.BenchmarkError(f"Disposable Venice save is missing: {save}")
    save_hash = base.sha256(save)
    if subprocess.run(["sudo", "-v"], check=False).returncode:
        raise base.BenchmarkError("Administrator access is required for powermetrics")
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    if launcher.get("exePath") != "./eu4.app/Contents/MacOS/eu4" or not isinstance(launcher.get("exeArgs"), list):
        raise base.BenchmarkError("Unexpected GOG launcher configuration")
    run_dir = Path(args.output).expanduser().resolve() / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-state-cache"
    run_dir.mkdir(parents=True, exist_ok=False)
    control_path, events = run_dir / "control.bin", run_dir / "events.jsonl"
    control_path.write_bytes(bytes(4096))
    metadata = {"status": "launching", "gog": evidence["gog"], "preflight": evidence,
                "save_sha256": save_hash, "library_sha256": evidence["library_sha256"]}
    metadata_path = run_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    game = pm = None
    phases: list[dict] = []
    try:
        with control_path.open("r+b") as control_file, mmap.mmap(control_file.fileno(), 4096) as control:
            env = os.environ.copy()
            env.update(EU4_CACHE_LOG=str(run_dir / "state.csv"),
                       EU4_CACHE_CONTROL=str(control_path), DYLD_INSERT_LIBRARIES=str(LIBRARY))
            with (run_dir / "game.stdout").open("wb") as stdout, (run_dir / "game.stderr").open("wb") as stderr:
                game = subprocess.Popen(["./eu4", *launcher["exeArgs"]], cwd=base.GOG_EXE.parent,
                                        env=env, stdout=stdout, stderr=stderr)
            metadata["game_pid"] = game.pid
            print("Launched GOG EU IV with the state cache OFF. Load the disposable Venice save and pause.")
            if base.prompt("Confirm the save and intended DLC/mods are loaded (yes/no): ", True).lower() != "yes":
                raise base.BenchmarkError("Game state was not confirmed")
            if game.poll() is not None or base.running_game_pid("paused") != game.pid:
                raise base.BenchmarkError("Expected GOG EU IV process is not running")
            if subprocess.run(["sudo", "-v"], check=False).returncode:
                raise base.BenchmarkError("Administrator authorization expired")
            base.prompt("Keep EU IV paused. Press Enter, return to the game, and leave it untouched through seven sounds: ")
            time.sleep(5)
            display, settings, power = base.display_mode(), base.game_settings(), base.power_state()
            if (not display.get("verified") or abs(display.get("refresh_hz", 0)-120) > 1 or
                settings.get("fullScreen") != "yes" or settings.get("borderless") != "no" or
                settings.get("game_resolution") != "3456x2234" or power.get("mode") != "normal"):
                raise base.BenchmarkError("Expected 120 Hz, 3456×2234 fullscreen, Normal power mode")
            metadata.update(display=display, settings=settings, power=power, dlc_mods=base.mod_settings())
            anchor = mark(events, "clock_anchor")
            metadata["clock_anchor"] = anchor
            command = ["sudo", "-n", "powermetrics", "-i", "1000", "-n", "124",
                       "-s", "tasks,cpu_power,gpu_power,thermal", "-f", "plist", "--show-process-gpu"]
            with (run_dir / "powermetrics.pliststream").open("wb") as raw, \
                 (run_dir / "powermetrics.stderr").open("wb") as errors:
                pm = subprocess.Popen(command, stdout=raw, stderr=errors)
                time.sleep(2)
                if pm.poll() is not None:
                    raise base.BenchmarkError("powermetrics exited before the first phase")
                for index, (name, selected_mode) in enumerate(PHASES):
                    control[0] = selected_mode
                    base.cue()
                    print(f"Phase {index+1}/6: {name.upper()} for {SECONDS}s", flush=True)
                    start = mark(events, "phase_start", phase=name, mode=selected_mode)
                    deadline = time.monotonic() + SECONDS
                    engagement_checked = False
                    while time.monotonic() < deadline:
                        if game.poll() is not None:
                            raise base.BenchmarkError("GOG EU IV exited during validation")
                        if pm.poll() is not None and pm.returncode:
                            raise base.BenchmarkError("powermetrics exited during validation")
                        if (name == "b" and not engagement_checked and
                                time.monotonic_ns()-start["monotonic_ns"] >= 8_000_000_000):
                            engagement_checked = True
                            texture_calls = sum(r["texture_forwarded"] + r["texture_suppressed"]
                                                for r in rows(run_dir / "state.csv") if r["mode"] == 1)
                            if texture_calls == 0:
                                raise base.BenchmarkError(
                                    "Stopped early: the texture shim saw no GL calls in B; "
                                    "do not spend the remaining phases on an inactive cache")
                        time.sleep(min(.5, max(0, deadline-time.monotonic())))
                    end = mark(events, "phase_end", phase=name)
                    phases.append({"name": name, "mode": selected_mode,
                                   "start_ns": start["monotonic_ns"], "end_ns": end["monotonic_ns"]})
                control[0] = 0
                base.cue()
                try:
                    pm.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    pm.terminate(); pm.wait(timeout=5)
            if pm.returncode:
                raise base.BenchmarkError(f"powermetrics failed: {(run_dir/'powermetrics.stderr').read_text(errors='replace').strip()}")
            metadata["save_changed_accepted"] = base.sha256(save) != save_hash
            if metadata["save_changed_accepted"]:
                print("The disposable save changed after capture; recording this without discarding the phases.")
            visual_answer = base.prompt("Did any cache phase show a visible rendering defect (yes/no)? ", True).lower()
            if visual_answer not in ("yes", "no"):
                raise base.BenchmarkError("Visual-equivalence answer must be yes or no")
            metadata["visual_defect_reported"] = visual_answer == "yes"
            metadata["phases"] = phases
            report = write_report(run_dir, phases, anchor, game.pid,
                                  metadata["visual_defect_reported"],
                                  metadata["save_changed_accepted"])
            metadata["status"] = "complete" if report["quality"]["adequate_samples"] else "inconclusive"
            print(f"Validation report: {run_dir/'validation.md'}")
            print("Quit the measured game session normally when finished.")
    except (base.BenchmarkError, OSError, KeyboardInterrupt) as exc:
        metadata["status"] = "incomplete"
        metadata["error"] = str(exc)
        raise
    finally:
        if control_path.is_file():
            with control_path.open("r+b") as control_file:
                control_file.write(b"\0")
                control_file.flush()
        if pm is not None and pm.poll() is None:
            pm.terminate()
        metadata["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        metadata_path.write_text(json.dumps(metadata, indent=2, default=str) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="build and test the state cache without launching EU IV")
    collect = sub.add_parser("run", help="one controlled GOG A-B-A-C-A-D launch")
    collect.add_argument("--output", default=str(ROOT / "results"))
    report = sub.add_parser("report", help="regenerate a validation report")
    report.add_argument("run_dir")
    report.add_argument("--ignore-save-change", action="store_true",
                        help="accept a save-file change recorded after the timed phases")
    args = parser.parse_args()
    try:
        if args.command == "preflight":
            print(json.dumps(preflight(), indent=2))
        elif args.command == "run":
            run(args)
        else:
            path = Path(args.run_dir).expanduser().resolve()
            metadata_path = path / "metadata.json"
            metadata = json.loads(metadata_path.read_text())
            if metadata.get("error") == "Disposable save changed during paused validation":
                if not args.ignore_save_change:
                    raise base.BenchmarkError("The save changed; pass --ignore-save-change to analyze these phases")
                metadata["save_changed_accepted"] = True
            phases = metadata.get("phases") or phases_from_events(path / "events.jsonl")
            metadata["phases"] = phases
            write_report(path, phases, metadata["clock_anchor"], metadata["game_pid"],
                         metadata.get("visual_defect_reported"),
                         metadata.get("save_changed_accepted", False))
            metadata["status"] = "analyzed_inconclusive" if metadata.get("visual_defect_reported") is None else "complete"
            metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
            print(path / "validation.md")
    except (base.BenchmarkError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
