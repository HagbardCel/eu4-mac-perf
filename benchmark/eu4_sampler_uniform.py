#!/usr/bin/env python3
"""One unattended GOG EU IV A-U-A-U-A sampler-uniform experiment."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import mmap
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import autonomous_runner as auto
import eu4_benchmark as base
import eu4_diagnostic as diagnostic
import eu4_state_cache as prior_cache
from fixture_manager import FixtureManager


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).with_suffix(".c")
SOURCE = SOURCE.with_name("eu4_sampler_probe.c")
LIBRARY = ROOT / "benchmark/.build/libeu4_sampler_probe.dylib"
TEST_LIBRARY = ROOT / "benchmark/.build/libeu4_sampler_test.dylib"
HARNESS_SOURCE = ROOT / "tests/sampler_uniform_harness.c"
HARNESS = ROOT / "benchmark/.build/sampler_uniform_harness"
PHASES = (("a1", 0), ("u1", 1), ("a2", 0), ("u2", 1), ("a3", 0))
PHASE_SECONDS = 30
SETTLE_SECONDS = 5


def build() -> None:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    commands = (
        ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
         "-dynamiclib", "-framework", "OpenGL", "-o", str(LIBRARY), str(SOURCE)],
        ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
         "-DEU4_SAMPLER_TEST_CALLSITE", "-dynamiclib", "-framework", "OpenGL",
         "-o", str(TEST_LIBRARY), str(SOURCE)],
        ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
         "-framework", "OpenGL", "-o", str(HARNESS), str(HARNESS_SOURCE)],
    )
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise base.BenchmarkError(f"Sampler build failed: {result.stderr.strip()}")
    for path in (LIBRARY, TEST_LIBRARY, HARNESS):
        if base.command("lipo", "-archs", str(path)).strip() != "x86_64":
            raise base.BenchmarkError(f"{path.name} is not x86_64")


def rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    output = []
    with path.open(newline="") as source:
        for row in csv.reader(source):
            if len(row) != 10 or row[0] != "U":
                continue
            try:
                (clock, wall, interval, mode, attempted, forwarded,
                 suppressed, unsafe, switches) = map(int, row[1:])
            except ValueError:
                continue
            if (interval <= 0 or mode not in (0, 1) or
                    attempted < 0 or forwarded < 0 or suppressed < 0 or
                    attempted != forwarded + suppressed):
                continue
            output.append({"clock_ns": clock, "wall_ns": wall, "interval_ns": interval,
                           "mode": mode, "attempted": attempted,
                           "forwarded": forwarded, "suppressed": suppressed,
                           "unsafe": unsafe, "context_switches": switches})
    return output


def offline_preflight() -> dict:
    identity = base.gog_identity()
    if identity["executable_sha256"] != prior_cache.GOG_SHA256:
        raise base.BenchmarkError("GOG binary changed; the sampler call site is no longer pinned")
    build()
    refs = prior_cache.actual_glew_state_references()
    required = {"Uniform1i", "Uniform4fvARB", "UseProgramObjectARB",
                "LinkProgramARB", "DeleteObjectARB"}
    if not required.issubset(refs):
        raise base.BenchmarkError("Required GOG GLEW sampler/program calls changed")
    disassembly = base.command("xcrun", "llvm-objdump", "--disassemble-symbols="
                               "__ZN13SShaderOpenGL6SetAllEv", str(base.GOG_EXE))
    if ("1015ebc78: ff d0" not in disassembly or
            "1015ebc7a:" not in disassembly or
            "1015ebc99: ff e0" not in disassembly):
        raise base.BenchmarkError("Pinned SetAll sampler loop or tail call changed")
    with tempfile.TemporaryDirectory(prefix="eu4-sampler-preflight-") as temporary:
        root = Path(temporary)
        control = root / "control.bin"
        control.write_bytes(bytes(4096))
        env = {**os.environ, "DYLD_INSERT_LIBRARIES": str(TEST_LIBRARY),
               "EU4_SAMPLER_LOG": str(root / "sampler.csv"),
               "EU4_SAMPLER_CONTROL": str(control)}
        harness = subprocess.run([str(HARNESS)], env=env, capture_output=True,
                                 text=True, timeout=20, check=False)
        observed = rows(root / "sampler.csv")
        if harness.returncode or not observed:
            raise base.BenchmarkError(f"Offline sampler GL harness failed: "
                                      f"{harness.stderr.strip() or harness.stdout.strip()}")
        off = [item for item in observed if item["mode"] == 0]
        on = [item for item in observed if item["mode"] == 1]
        if (len(off) < 2 or len(on) < 2 or
                not any(item["attempted"] >= 1000 and item["suppressed"] == 0 for item in off) or
                not any(item["suppressed"] >= 999 for item in on) or
                any(item["unsafe"] for item in observed)):
            raise base.BenchmarkError("Offline sampler pass-through or suppression counts are invalid")
        context_log = root / "contexts.csv"
        context_test = subprocess.run([str(HARNESS), "--contexts"],
            env={**env, "EU4_SAMPLER_LOG": str(context_log)},
            capture_output=True, text=True, timeout=15, check=False)
        context_rows = rows(context_log)
        if (context_test.returncode or
                not any(item["context_switches"] >= 2 and item["forwarded"] >= 2
                        for item in context_rows) or
                any(item["unsafe"] for item in context_rows)):
            raise base.BenchmarkError("Sampler did not re-prime after context switches")
        stress = {"bare": [], "pass_through": []}
        for _ in range(3):
            for label, environment in (("bare", os.environ), ("pass_through", {
                    **os.environ, "DYLD_INSERT_LIBRARIES": str(LIBRARY),
                    "EU4_SAMPLER_LOG": str(root / f"stress-{len(stress['pass_through'])}.csv"),
                    "EU4_SAMPLER_CONTROL": str(control)})):
                result = subprocess.run([str(HARNESS), "--stress"], env=environment,
                                        capture_output=True, text=True, timeout=30, check=False)
                if result.returncode:
                    raise base.BenchmarkError(f"Offline sampler {label} stress failed: {result.stderr}")
                stress[label].append(int(result.stdout.strip().split("stress_ns=")[-1]))
        bare = statistics.median(stress["bare"])
        overhead_ns = max(0, statistics.median(stress["pass_through"])-bare)
        projected_frame_pct = overhead_ns/1_000_000 * 909_000 / 1e9 * 100
        if projected_frame_pct > 5:
            raise base.BenchmarkError(f"Sampler pass-through overhead projects to "
                                      f"{projected_frame_pct:.1f}% of elapsed time")
    return {"gog": identity, "library_sha256": base.sha256(LIBRARY),
            "library_source_sha256": base.sha256(SOURCE),
            "harness": "passed", "callsite": "eu4+0x15ebc7a",
            "projected_pass_through_overhead_pct": round(projected_frame_pct, 2)}


def start_power(path: Path) -> subprocess.Popen:
    with path.open("wb") as raw, path.with_suffix(".stderr").open("wb") as errors:
        process = subprocess.Popen(["sudo", "-n", str(auto.HELPER_INSTALLED)],
                                   stdout=raw, stderr=errors)
    time.sleep(2)
    if process.poll() is not None:
        raise base.BenchmarkError(f"Powermetrics helper exited: "
                                  f"{path.with_suffix('.stderr').read_text(errors='replace')}")
    return process


def phase_samples(probe: Path, phases: list[dict], anchor: dict, raw_power: list[dict],
                  game_pid: int) -> dict:
    swap_rows = auto.probe_rows(probe, anchor)
    sampler_rows = rows(probe)
    power_phases = [{**phase, "start_ns": phase["start_ns"] + SETTLE_SECONDS*1_000_000_000}
                    for phase in phases]
    power = diagnostic.summarize_power(raw_power, power_phases, anchor, game_pid)
    output = {}
    for phase in phases:
        name, start, end, mode = (phase["name"], phase["start_ns"],
                                  phase["end_ns"], phase["mode"])
        selected_swaps = [item for item in swap_rows if
                          start + SETTLE_SECONDS*1_000_000_000 <=
                          item["monotonic_ns"]-item["interval_ns"] and
                          item["monotonic_ns"] < end]
        selected_uniforms = [item for item in sampler_rows if
                             item["mode"] == mode and
                             start + SETTLE_SECONDS*1_000_000_000 <=
                             anchor["monotonic_ns"]+item["wall_ns"]-anchor["wall_ns"]-item["interval_ns"] and
                             anchor["monotonic_ns"]+item["wall_ns"]-anchor["wall_ns"] < end]
        if (len(selected_swaps) < 19 or len(selected_uniforms) < 19 or
                power[name].get("samples", 0) < 20 or
                power[name].get("eu4_cputime_ms_per_s") is None or
                power[name].get("combined_w") is None or
                any(item["swaps_s"] < 20 or
                    item["paused_swaps"] < .95*item["swaps"] for item in selected_swaps)):
            raise base.BenchmarkError(f"Insufficient settled, paused samples in {name}")
        output[name] = {"swap_samples": len(selected_swaps),
                        "sampler_samples": len(selected_uniforms),
                        "median_swaps_s": round(statistics.median(
                            item["swaps_s"] for item in selected_swaps), 3),
                        "attempted": sum(item["attempted"] for item in selected_uniforms),
                        "forwarded": sum(item["forwarded"] for item in selected_uniforms),
                        "suppressed": sum(item["suppressed"] for item in selected_uniforms),
                        "context_switches": sum(item["context_switches"]
                                                for item in selected_uniforms),
                        "unsafe": max(item["unsafe"] for item in selected_uniforms),
                        **power[name]}
    return output


def report(phases: dict, visual_ok: bool | None = None, save_changed: bool = False) -> dict:
    phases = {name: {**item,
                     "eu4_cpu_ms_per_swap": round(item["eu4_cputime_ms_per_s"] /
                                                    item["median_swaps_s"], 4),
                     "joules_per_swap": round(item["combined_w"] /
                                               item["median_swaps_s"], 5)}
              for name, item in phases.items()}
    metrics = ("eu4_cputime_ms_per_s", "combined_w", "median_swaps_s",
               "eu4_cpu_ms_per_swap", "joules_per_swap")
    comparisons = {}
    for candidate, controls in (("u1", ("a1", "a2")), ("u2", ("a2", "a3"))):
        comparisons[candidate] = {}
        for metric in metrics:
            baseline = statistics.median(phases[name][metric] for name in controls)
            change = phases[candidate][metric]/baseline-1
            comparisons[candidate][metric] = round(change*100, 3)
    drift_limits = {"eu4_cputime_ms_per_s": .03, "combined_w": .05,
                    "median_swaps_s": .03}
    drift = {metric: max(phases[name][metric] for name in ("a1", "a2", "a3")) /
                     min(phases[name][metric] for name in ("a1", "a2", "a3")) - 1
             for metric in drift_limits}
    quality = {"baseline_stable": all(drift[key] < limit for key, limit in drift_limits.items()),
               "attempts_seen": all(phases[name]["attempted"] > 100_000 for name in ("u1", "u2")),
               "suppression_engaged": all(phases[name]["suppressed"] > 0 for name in ("u1", "u2")),
               "pass_through_exact": all(phases[name]["suppressed"] == 0
                                         for name in ("a1", "a2", "a3")),
               "single_safe_context": all(item["unsafe"] == 0 for item in phases.values()),
               "swap_rate_preserved": all(comparisons[name]["median_swaps_s"] >= -3
                                          for name in ("u1", "u2")),
               "save_unchanged": not save_changed, "visual_ok": visual_ok}
    valid = all(value for key, value in quality.items()
                if key not in ("visual_ok", "suppression_engaged")) and visual_ok is True
    improvements = {metric: [-comparisons[name][metric] for name in ("u1", "u2")]
                    for metric in ("eu4_cputime_ms_per_s", "combined_w")}
    no_material_regression = all(min(values) >= -3 for values in improvements.values())
    strong = (quality["suppression_engaged"] and no_material_regression and
              any(min(values) >= 3 and statistics.median(values) >= 5
                  for values in improvements.values()))
    moderate = (quality["suppression_engaged"] and no_material_regression and
                any(min(values) > 0 and statistics.median(values) >= 2
                    for values in improvements.values()))
    negative = all(max(values) < 2 for values in improvements.values())
    decision = ("strong_positive" if valid and strong else
                "moderate_positive" if valid and moderate else
                "no_major_benefit" if valid and negative else "inconclusive")
    return {"phases": phases, "paired_change_pct": comparisons,
            "baseline_variation_fraction": drift, "quality": quality,
            "decision": decision}


def write_report(run_dir: Path, phases: list[dict], anchor: dict, game_pid: int,
                 visual_ok: bool | None, save_changed: bool) -> dict:
    raw = base.read_plist_stream(run_dir / "powermetrics.pliststream")
    summary = phase_samples(run_dir / "sampler.csv", phases, anchor, raw, game_pid)
    result = report(summary, visual_ok, save_changed)
    (run_dir / "validation.json").write_text(json.dumps(result, indent=2)+"\n")
    lines = ["# GOG EU IV sampler-uniform validation", "",
             "One unattended A–U–A–U–A session; U suppresses duplicate assignments from "
             "the 16 verified `SShaderOpenGL::SetAll()` loop calls.", "",
             "| Phase | Swaps/s | EU IV CPU ms/s | Combined W | CPU ms/swap | J/swap | Attempted | Forwarded | Suppressed |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, _ in PHASES:
        item = result["phases"][name]
        lines.append(f"| {name} | {item['median_swaps_s']} | {item['eu4_cputime_ms_per_s']} | "
                     f"{item['combined_w']} | {item['eu4_cpu_ms_per_swap']} | "
                     f"{item['joules_per_swap']} | {item['attempted']:,} | {item['forwarded']:,} | "
                     f"{item['suppressed']:,} |")
    lines.extend(["", "Paired change versus adjacent A phases (negative means reduction):", ""])
    for name in ("u1", "u2"):
        comparison = result["paired_change_pct"][name]
        lines.append(f"- {name}: CPU {comparison['eu4_cputime_ms_per_s']:+.2f}%, "
                     f"combined power {comparison['combined_w']:+.2f}%, "
                     f"swaps {comparison['median_swaps_s']:+.2f}%, "
                     f"CPU/swap {comparison['eu4_cpu_ms_per_swap']:+.2f}%, "
                     f"J/swap {comparison['joules_per_swap']:+.2f}%.")
    lines.extend(["", f"Decision: **{result['decision']}**. Visual review: {visual_ok}.",
                  "Context switches by phase: " + str({name: summary[name]["context_switches"]
                                                      for name, _ in PHASES}),
                  "Power estimates are system-wide; swaps are an in-process frame proxy.", ""])
    (run_dir / "validation.md").write_text("\n".join(lines))
    return result


def run(output_root: Path) -> Path:
    evidence = auto.preflight(require_privilege=True)
    sampler = offline_preflight()
    base.running_game_pid("idle")
    with FixtureManager(output_root, base.USER_DATA, auto.FIXTURE,
                        evidence["fixture"]["save_sha256"]) as fixture:
        fixture.recover()
        run_dir = output_root / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-sampler-uniform"
        run_dir.mkdir(parents=True, exist_ok=False)
        metadata = {"status": "starting", "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "run_dir": str(run_dir), "baseline_evidence": evidence,
                    "sampler_preflight": sampler}
        manifest = run_dir / "manifest.json"
        events = run_dir / "events.jsonl"
        manifest.write_text(json.dumps(metadata, indent=2)+"\n")
        anchor = auto.mark(events, "clock_anchor")
        metadata["clock_anchor"] = anchor
        control_path = run_dir / "control.bin"
        control_path.write_bytes(bytes(4096))
        game_log = base.USER_DATA / "logs/game.log"
        old_log = game_log.read_bytes() if game_log.is_file() else b""
        game = pm = None
        installed = False
        try:
            working_save = fixture.install()
            installed = True
            metadata["working_save"] = str(working_save)
            pm = start_power(run_dir / "powermetrics-readiness.pliststream")
            env = {**os.environ, "DYLD_INSERT_LIBRARIES": str(LIBRARY),
                   "EU4_SAMPLER_LOG": str(run_dir / "sampler.csv"),
                   "EU4_SAMPLER_CONTROL": str(control_path)}
            with (run_dir / "game.stdout").open("wb") as stdout, \
                 (run_dir / "game.stderr").open("wb") as stderr:
                game = subprocess.Popen(["./eu4", *evidence["launcher_args"],
                                         "--continuelastsave"], cwd=base.GOG_EXE.parent,
                                        env=env, stdout=stdout, stderr=stderr)
            metadata["game_pid"] = game.pid
            readiness_tail = auto.PowerTail(run_dir / "powermetrics-readiness.pliststream")
            metadata["readiness"] = auto.wait_until_ready(game, run_dir / "sampler.csv",
                readiness_tail, anchor, game_log, old_log)
            auto.mark(events, "ready", **metadata["readiness"])
            print("Paused Venice scene ready; checking sampler call site.", flush=True)
            recent_uniforms = rows(run_dir / "sampler.csv")[-5:]
            if (len(recent_uniforms) < 5 or
                    sum(item["attempted"] for item in recent_uniforms) < 50_000 or
                    any(item["unsafe"] for item in recent_uniforms)):
                raise base.BenchmarkError("Pinned sampler call site did not engage safely before warm-up")
            metadata["display_with_game"] = base.display_mode()
            if (not metadata["display_with_game"].get("verified") or
                    abs(metadata["display_with_game"]["refresh_hz"]-120) > 1):
                raise base.BenchmarkError("EU IV changed the display away from 120 Hz")
            auto.stop_process(pm, 5)
            pm = start_power(run_dir / "powermetrics.pliststream")
            power_tail = auto.PowerTail(run_dir / "powermetrics.pliststream")
            metadata["warmup"] = auto.warm_up(game, run_dir / "sampler.csv", power_tail, anchor)
            auto.mark(events, "warmup_complete")
            print("Warm-up complete; starting A-U-A-U-A phases.", flush=True)
            auto.capture_scene(run_dir / "ready-scene.png")
            metadata["scene_alignment"] = auto.verify_scene(run_dir / "ready-scene.png", False)
            phases = []
            with control_path.open("r+b") as file, mmap.mmap(file.fileno(), 4096) as control:
                for name, mode in PHASES:
                    control[0] = mode
                    print(f"Phase {name.upper()}: {'sampler suppression' if mode else 'pass-through'} ",
                          f"for {PHASE_SECONDS}s", flush=True)
                    start = auto.mark(events, "phase_start", phase=name, mode=mode)
                    deadline = time.monotonic() + PHASE_SECONDS
                    engagement_checked = False
                    while time.monotonic() < deadline:
                        if game.poll() is not None or pm.poll() is not None:
                            raise base.BenchmarkError("EU IV or powermetrics exited during sampler validation")
                        if not auto.focus("interior", game.pid):
                            raise base.BenchmarkError("EU IV lost focus or the pointer reached a scrolling edge")
                        recent = auto.probe_rows(run_dir / "sampler.csv", anchor)[-2:]
                        if (not recent or time.monotonic_ns()-recent[-1]["monotonic_ns"] >
                                2_000_000_000 or any(item["swaps_s"] < 20 or
                                          item["paused_swaps"] < .95*item["swaps"] for item in recent)):
                            raise base.BenchmarkError("EU IV stopped rendering the paused campaign")
                        recent_uniforms = rows(run_dir / "sampler.csv")[-2:]
                        if any(item["unsafe"] for item in recent_uniforms):
                            raise base.BenchmarkError("Sampler cache detected another GL thread or context")
                        if (mode and not engagement_checked and
                                time.monotonic_ns()-start["monotonic_ns"] >= 10_000_000_000):
                            engagement_checked = True
                            attempts = sum(item["attempted"] for item in rows(run_dir / "sampler.csv")
                                           if item["mode"] == 1 and item["wall_ns"] >= start["wall_ns"])
                            if attempts < 10_000:
                                raise base.BenchmarkError("Sampler call site did not engage; stopped early")
                        time.sleep(min(1, max(0, deadline-time.monotonic())))
                    end = auto.mark(events, "phase_end", phase=name, mode=mode)
                    phases.append({"name": name, "mode": mode,
                                   "start_ns": start["monotonic_ns"],
                                   "end_ns": end["monotonic_ns"]})
                    image = run_dir / f"scene-{name}.png"
                    auto.capture_scene(image)
                    auto.verify_scene(image, False)
                control[0] = 0
            metadata["phases"] = phases
            auto.stop_process(pm, 5)
            metadata["validation"] = write_report(run_dir, phases, anchor, game.pid,
                                                   None, False)
            metadata["status"] = "complete_pending_visual_review"
        except (base.BenchmarkError, OSError, ValueError, subprocess.SubprocessError,
                KeyboardInterrupt) as exc:
            metadata["status"] = "incomplete"
            metadata["error"] = str(exc)
            raise
        finally:
            try:
                auto.stop_process(pm, 3)
                if game is not None and game.poll() is None and auto.focus("terminate", game.pid):
                    try:
                        game.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                auto.stop_process(game, 10)
                if game is not None and game.poll() is None:
                    raise base.BenchmarkError("EU IV remained running; recovery journal was retained")
                if installed:
                    metadata["working_save_changed"] = fixture.finish(run_dir)
            except BaseException as cleanup_error:
                metadata["status"] = "incomplete"
                metadata["cleanup_error"] = str(cleanup_error)
                raise
            finally:
                metadata["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
                manifest.write_text(json.dumps(metadata, indent=2, default=str)+"\n")
        return run_dir


def review(run_dir: Path, visual_ok: bool) -> dict:
    manifest = run_dir / "manifest.json"
    metadata = json.loads(manifest.read_text())
    if metadata.get("status") not in ("complete_pending_visual_review", "complete"):
        raise base.BenchmarkError("Only a completed sampler run can be reviewed")
    result = write_report(run_dir, metadata["phases"], metadata["clock_anchor"],
                          metadata["game_pid"], visual_ok,
                          metadata.get("working_save_changed", False))
    metadata["validation"] = result
    metadata["status"] = "complete"
    manifest.write_text(json.dumps(metadata, indent=2)+"\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="pinned binary and offline GL checks; no EU IV launch")
    collect = sub.add_parser("run", help="one unattended A-U-A-U-A game session")
    collect.add_argument("--output", default=str(ROOT / "results"))
    inspect = sub.add_parser("review", help="finalize a completed run after scene inspection")
    inspect.add_argument("run_dir")
    outcome = inspect.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--visual-ok", action="store_true")
    outcome.add_argument("--visual-defect", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "preflight":
            print(json.dumps(offline_preflight(), indent=2))
        elif args.command == "run":
            print(run(Path(args.output).expanduser().resolve()))
        else:
            result = review(Path(args.run_dir).expanduser().resolve(), args.visual_ok)
            print(json.dumps(result, indent=2))
    except (base.BenchmarkError, OSError, ValueError, subprocess.SubprocessError,
            KeyboardInterrupt) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
