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
import submission_control as control
import submission_validation as validation
from autonomous_runner import capture_scene, focus, mark, stop_process, summarize, warm_up
from fixture_manager import FixtureManager
from submission_control import MODE_CANDIDATE, MODE_REFERENCE


ROOT = Path(__file__).resolve().parents[1]
PHASE_SCHEDULE = (
    ("a1", "reference", MODE_REFERENCE),
    ("b1", "candidate", MODE_CANDIDATE),
    ("a2", "reference", MODE_REFERENCE),
    ("b2", "candidate", MODE_CANDIDATE),
    ("a3", "reference", MODE_REFERENCE),
)
PHASE_SECONDS = 30
SETTLE_SECONDS = 5
EXPECTED_CANDIDATE_CAPABILITY_ID = 0


def _validate_control_deltas(
    role: str,
    start: dict[str, int],
    end: dict[str, int],
    *,
    expected_capability_id: int,
) -> dict:
    hook_delta = end["candidate_hook_attempts"] - start["candidate_hook_attempts"]
    action_delta = end["candidate_effective_actions"] - start["candidate_effective_actions"]
    if end["candidate_capability_id"] != expected_capability_id:
        return {
            "status": "failed",
            "reason": "candidate_capability_id mismatch",
            "start": start,
            "end": end,
        }
    if role == "candidate" and expected_capability_id == 0:
        return {
            "status": "unsupported_candidate",
            "hook_attempts_delta": hook_delta,
            "effective_actions_delta": action_delta,
            "start": start,
            "end": end,
        }
    if role == "candidate":
        ok = end["ack_mode"] == MODE_CANDIDATE and hook_delta > 0 and action_delta > 0
        reason = None if ok else "candidate phase requires hook and effective action deltas"
    else:
        ok = end["ack_mode"] == MODE_REFERENCE and hook_delta > 0 and action_delta == 0
        reason = None if ok else "reference phase requires hook delta without effective actions"
    return {
        "status": "passed" if ok else "failed",
        "reason": reason,
        "hook_attempts_delta": hook_delta,
        "effective_actions_delta": action_delta,
        "start": start,
        "end": end,
    }


def _run_phase(
    game: subprocess.Popen,
    run_dir: Path,
    events: Path,
    tail,
    anchor: dict,
    submission: control.SubmissionControl,
    name: str,
    role: str,
    mode: int,
    duration: float,
    *,
    expected_capability_id: int,
) -> dict:
    generation = submission.request_mode(mode)
    ack = submission.wait_ack(generation)
    if ack["ack_mode"] != mode:
        raise base.BenchmarkError(f"{name}: ack mode {ack['ack_mode']} != requested {mode}")
    settle_deadline = time.monotonic() + SETTLE_SECONDS
    while time.monotonic() < settle_deadline:
        if game.poll() is not None:
            raise base.BenchmarkError(f"EU IV exited during {name} settle")
        if not focus("interior", game.pid):
            raise base.BenchmarkError(f"EU IV lost focus during {name} settle")
        time.sleep(0.25)
    screenshot = run_dir / f"measure-scene-{name}.png"
    capture_scene(screenshot)
    if not focus("interior", game.pid):
        raise base.BenchmarkError(f"EU IV lost focus after {name} screenshot")
    counter_start = submission.snapshot()
    measurement_start = mark(events, "measurement_start", phase=name, mode=mode)
    measurement_deadline = time.monotonic() + duration
    while time.monotonic() < measurement_deadline:
        if game.poll() is not None:
            raise base.BenchmarkError(f"EU IV exited during {name} measurement")
        if not focus("interior", game.pid):
            raise base.BenchmarkError(f"EU IV lost focus during {name} measurement")
        time.sleep(min(1.0, max(0.0, measurement_deadline - time.monotonic())))
    measurement_end = mark(events, "measurement_end", phase=name, mode=mode)
    counter_end = submission.snapshot()
    tail.poll()
    phase = {
        "name": name,
        "role": role,
        "mode": mode,
        "run_dir": str(run_dir),
        "start_ns": measurement_start["monotonic_ns"],
        "end_ns": measurement_end["monotonic_ns"],
        "screenshot": str(screenshot),
        "control_validation": _validate_control_deltas(
            role,
            counter_start,
            counter_end,
            expected_capability_id=expected_capability_id,
        ),
    }
    phase["summary"] = summarize(run_dir, [phase], anchor, game.pid, tail.samples)
    return phase


def run_experiment(output_root: Path) -> Path:
    auto.build()
    control.build()
    evidence = auto.preflight()
    base.running_game_pid("idle")
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-submission-experiment"
    run_dir.mkdir(parents=True, exist_ok=False)
    expected_capability = EXPECTED_CANDIDATE_CAPABILITY_ID
    manifest: dict = {
        "experiment": "submission_optimization_ababa_v1",
        "status": "starting",
        "instrumentation": "libeu4_auto_probe.dylib + libeu4_submission_experiment.dylib",
        "expected_candidate_capability_id": expected_capability,
        "phase_schedule": [{"name": n, "role": r, "mode": m} for n, r, m in PHASE_SCHEDULE],
        "phase_seconds": PHASE_SECONDS,
        "repository_scene_reference": str(validation.SCENE_REFERENCE_MANIFEST.relative_to(ROOT)),
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    events = run_dir / "events.jsonl"
    anchor = mark(events, "clock_anchor")
    game_log = base.USER_DATA / "logs/game.log"
    old_log = game_log.read_bytes() if game_log.is_file() else b""
    game = pm = None
    installed = False
    control_path = run_dir / "submission_control.bin"
    control_path.write_bytes(bytes(control.CONTROL_SIZE))
    with FixtureManager(output_root, base.USER_DATA, auto.FIXTURE, evidence["fixture"]["save_sha256"]) as fixture:
        fixture.recover()
        working = fixture.install()
        installed = True
        manifest["working_save"] = str(working)
        pm_path = run_dir / "powermetrics.pliststream"
        submission = control.SubmissionControl(control_path, candidate_capability_id=expected_capability)
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
                env = control.dylib_env(
                    control_path,
                    auto.PROBE,
                    active_capability_id=expected_capability,
                )
                env["EU4_AUTO_PROBE_LOG"] = str(run_dir / "auto-probe.csv")
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
                    phases.append(
                        _run_phase(
                            game,
                            run_dir,
                            events,
                            tail,
                            anchor,
                            submission,
                            name,
                            role,
                            mode,
                            PHASE_SECONDS,
                            expected_capability_id=expected_capability,
                        ),
                    )
                manifest["phases"] = phases
                manifest["pareto_gate"] = validation.bracket_normalized_ababa_gate(
                    phases,
                    expected_candidate_capability_id=expected_capability,
                )
                manifest["status"] = "complete"
                stop_process(pm, 5)
                pm = None
                drain = tail.finish()
                if drain.get("status") == "incomplete":
                    raise base.BenchmarkError(f"Powermetrics tail incomplete: {drain}")
        finally:
            submission.close()
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
