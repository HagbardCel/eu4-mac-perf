#!/usr/bin/env python3
"""Profiler-off A-B-A-B-A submission optimization experiment (autonomous probe only)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

import autonomous_runner as auto
import eu4_benchmark as base
import submission_validation as validation
from autonomous_runner import capture_scene, focus, mark, stop_process, summarize, warm_up
from fixture_manager import FixtureManager


ROOT = Path(__file__).resolve().parents[1]
PHASE_SCHEDULE = (
    ("a1", "reference", 0),
    ("b1", "candidate", 1),
    ("a2", "reference", 0),
    ("b2", "candidate", 1),
    ("a3", "reference", 0),
)
PHASE_SECONDS = 30
SETTLE_SECONDS = 5


def _run_phase(
    game: subprocess.Popen,
    run_dir: Path,
    events: Path,
    tail,
    anchor: dict,
    name: str,
    role: str,
    mode: int,
    duration: float,
) -> dict:
    start = mark(events, "phase_start", phase=name, mode=mode)
    deadline = time.monotonic() + duration
    screenshot = run_dir / f"measure-scene-{name}.png"
    captured = False
    while time.monotonic() < deadline:
        if game.poll() is not None:
            raise base.BenchmarkError(f"EU IV exited during {name}")
        if not focus("interior", game.pid):
            raise base.BenchmarkError(f"EU IV lost focus during {name}")
        if not captured and time.monotonic() >= start["monotonic_ns"] / 1e9 + SETTLE_SECONDS:
            capture_scene(screenshot)
            captured = True
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    if not captured:
        capture_scene(screenshot)
    end = mark(events, "phase_end", phase=name, mode=mode)
    phase = {
        "name": name,
        "role": role,
        "mode": mode,
        "start_ns": start["monotonic_ns"],
        "end_ns": end["monotonic_ns"],
        "screenshot": str(screenshot.name),
    }
    tail.poll()
    phase["summary"] = summarize(run_dir, [phase], anchor, game.pid, tail.samples)
    return phase


def run_experiment(output_root: Path) -> Path:
    evidence = auto.preflight()
    base.running_game_pid("idle")
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-submission-experiment"
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict = {
        "experiment": "submission_optimization_ababa_v1",
        "status": "starting",
        "instrumentation": "libeu4_auto_probe.dylib only; no frame-model profiler",
        "phase_schedule": [{"name": n, "role": r, "mode": m} for n, r, m in PHASE_SCHEDULE],
        "phase_seconds": PHASE_SECONDS,
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    events = run_dir / "events.jsonl"
    anchor = mark(events, "clock_anchor")
    game_log = base.USER_DATA / "logs/game.log"
    old_log = game_log.read_bytes() if game_log.is_file() else b""
    game = pm = None
    installed = False
    with FixtureManager(output_root, base.USER_DATA, auto.FIXTURE, evidence["fixture"]["save_sha256"]) as fixture:
        fixture.recover()
        working = fixture.install()
        installed = True
        manifest["working_save"] = str(working)
        pm_path = run_dir / "powermetrics.pliststream"
        try:
            with pm_path.open("wb") as raw, (run_dir / "powermetrics.stderr").open("wb") as errors:
                pm = subprocess.Popen(
                    ["sudo", "-n", str(auto.HELPER_INSTALLED)],
                    stdout=raw,
                    stderr=errors,
                )
                time.sleep(2)
                if pm.poll() is not None:
                    raise base.BenchmarkError("Powermetrics helper exited before EU IV launch")
                env = {
                    **__import__("os").environ,
                    "DYLD_INSERT_LIBRARIES": str(auto.PROBE),
                    "EU4_AUTO_PROBE_LOG": str(run_dir / "auto-probe.csv"),
                    "EU4_SUBMISSION_EXPERIMENT_MODE": "0",
                }
                with (run_dir / "game.stdout").open("wb") as stdout, (run_dir / "game.stderr").open("wb") as stderr:
                    game = subprocess.Popen(
                        ["./eu4", *evidence["launcher_args"], "--continuelastsave"],
                        cwd=base.GOG_EXE.parent,
                        env=env,
                        stdout=stdout,
                        stderr=stderr,
                    )
                tail = auto.PowerTail(pm_path)
                ready = auto.wait_until_ready(game, run_dir / "auto-probe.csv", tail, anchor, game_log, old_log)
                manifest["readiness"] = ready
                warm_up(game, run_dir / "auto-probe.csv", tail, anchor)
                capture_scene(run_dir / "ready-scene.png")
                phases = []
                for name, role, mode in PHASE_SCHEDULE:
                    env["EU4_SUBMISSION_EXPERIMENT_MODE"] = str(mode)
                    phases.append(
                        _run_phase(game, run_dir, events, tail, anchor, name, role, mode, PHASE_SECONDS),
                    )
                manifest["phases"] = phases
                manifest["pareto_gate"] = validation.bracket_median_effect(phases)
                manifest["status"] = "complete"
                stop_process(pm, 5)
                pm = None
        finally:
            stop_process(pm, 3)
            if game is not None and game.poll() is None and focus("terminate", game.pid):
                try:
                    game.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            stop_process(game, 10)
            if installed:
                manifest["working_save_changed"] = fixture.finish(run_dir)
            manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "results"))
    args = parser.parse_args()
    try:
        run_dir = run_experiment(Path(args.output).expanduser().resolve())
        print(run_dir)
    except (base.BenchmarkError, OSError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
