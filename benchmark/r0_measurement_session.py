#!/usr/bin/env python3
"""One-launch R0 measurement session: epochs A (CPU), B (sample), C (census)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import autonomous_runner as auto
import eu4_benchmark as base
import eu4_r0_control as r0_control
from fixture_manager import FixtureManager
import r0_probe
import r0_probe_rows
import thread_cpu_sampler

ROOT = Path(__file__).resolve().parents[1]
EPOCH_A_SECONDS = 30.0
EPOCH_B_SAMPLE_SECONDS = 10
MIN_CENSUS_FRAMES = 120
MIN_CENSUS_SECONDS = 2.0
CPU_RECONCILE_TOLERANCE_MS_PER_S = 50.0
TRANSITION_DISCARD_FRAMES = 3
EXPECT_WIDTH = 3456
EXPECT_HEIGHT = 2234


@dataclass(frozen=True)
class Scenario:
    scenario_id: int
    name: str
    min_frames: int = MIN_CENSUS_FRAMES
    min_seconds: float = MIN_CENSUS_SECONDS
    positive_control: bool = False


DEFAULT_SCENARIOS = [
    Scenario(0, "paused_baseline_ui_closed"),
    Scenario(1, "positive_control_ui_toggle", min_frames=30, min_seconds=1.0, positive_control=True),
]

TERMINAL_FAILURE_STATUSES = frozenset({"incomplete", "cleanup_error"})


def mark_uptime(events_path: Path, event: str, **fields: object) -> dict[str, Any]:
    item = {
        "event": event,
        "phase": event,
        "event_uptime_ns": r0_probe.event_uptime_ns(),
        "monotonic_ns": time.monotonic_ns(),
        "wall_ns": time.time_ns(),
        **fields,
    }
    with events_path.open("a") as output:
        output.write(json.dumps(item, sort_keys=True) + "\n")
    return item


def run_sample(pid: int, output: Path, seconds: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["sample", str(pid), str(seconds), "-file", str(output), "-mayDie"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(f"sample failed: {result.stderr.strip() or result.stdout.strip()}")


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _process_cpu_ms_per_s(
    samples: list[dict], anchor: dict[str, Any], start_ns: int, end_ns: int, pid: int
) -> float | None:
    values = auto.cpu_samples_between(samples, pid, anchor, start_ns, end_ns)
    if not values:
        return None
    return float(sum(value for _, value in values) / len(values))


def sample_main_thread_corroboration(sample_text: str, main_thread_id: int | None) -> str:
    if main_thread_id is None:
        return "unavailable"
    token = str(main_thread_id)
    if token in sample_text:
        return "matched"
    return "unavailable"


def clock_alignment_check(log_path: Path, host_uptime_ns: int) -> dict[str, Any]:
    from analysis.tools.frame_evolution_analyze import FrameRecord, clock_alignment_sanity, load_frames_from_log

    frames = load_frames_from_log(log_path)
    return clock_alignment_sanity(frames, host_uptime_ns)


def run_offline_analyzers(output_dir: Path, manifest: dict[str, Any]) -> None:
    log_path = output_dir / "r0_probe.log"
    events_path = output_dir / "events.jsonl"
    if not log_path.is_file():
        return
    frame_out = output_dir / "frame_evolution_report.json"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "benchmark")}
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "analysis/tools/frame_evolution_analyze.py"),
            str(log_path),
            "--output",
            str(frame_out),
            "--events",
            str(events_path),
        ],
        check=False,
        cwd=str(ROOT),
        env=env,
    )
    if frame_out.is_file():
        manifest["frame_evolution_report"] = json.loads(frame_out.read_text())
        positive = manifest["frame_evolution_report"].get("positive_control", {})
        if positive.get("status") == "passed":
            manifest["positive_control_status"] = "passed"
        elif manifest.get("positive_control_status") != "unsupported_drawable":
            manifest["positive_control_status"] = "failed"

    sample_path = output_dir / "epoch_b.sample.txt"
    if sample_path.is_file():
        stack_out = output_dir / "stack_report.json"
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "analysis/tools/r0_stack_analyze.py"),
                str(sample_path),
                "--output",
                str(stack_out),
            ],
            check=False,
            cwd=str(ROOT),
            env=env,
        )
        if stack_out.is_file():
            manifest["stack_report"] = json.loads(stack_out.read_text())


def acceptance_gates(manifest: dict[str, Any]) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    cpu = manifest.get("epoch_a_thread_cpu", {})
    gates["cpu_reconcile"] = cpu.get("reconcile_ok") is True
    gates["main_thread_mapping"] = cpu.get("main_thread_mapping_status") == "verified"
    gates["thread_cpu_preflight"] = manifest.get("thread_cpu_preflight", {}).get("status") == "ready"
    gates["event_clock_alignment"] = manifest.get("event_clock_alignment", {}).get("status") == "passed"
    drawable = manifest.get("drawable_capability", {})
    if drawable.get("status") == "unsupported_drawable":
        gates["census_frames"] = True
        gates["positive_control"] = True
        prior = manifest.get("status")
        if prior not in TERMINAL_FAILURE_STATUSES:
            manifest["status"] = "partial_complete" if all(gates.values()) else "incomplete_gates"
    else:
        gates["census_frames"] = manifest.get("census_frame_count", 0) >= MIN_CENSUS_FRAMES
        gates["positive_control"] = manifest.get("positive_control_status") == "passed"
        prior = manifest.get("status")
        if prior in TERMINAL_FAILURE_STATUSES:
            pass
        elif all(gates.values()):
            manifest["status"] = "completed"
        else:
            manifest["status"] = "incomplete_gates"
    manifest["acceptance_gates"] = gates
    return gates


def run_integrated(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema": "r0_measurement_session_v1",
        "mode": "integrated",
        "cpu_reconcile_tolerance_ms_per_s": CPU_RECONCILE_TOLERANCE_MS_PER_S,
        "transition_discard_frames": TRANSITION_DISCARD_FRAMES,
        "expect_width": EXPECT_WIDTH,
        "expect_height": EXPECT_HEIGHT,
        "measurement_valid_for_cpu": {"epoch_a": True, "epoch_b": True, "epoch_c": False},
        "epochs": [],
        "scenarios": [scenario.__dict__ for scenario in DEFAULT_SCENARIOS],
    }
    events_path = output_dir / "events.jsonl"
    control_path = output_dir / "r0_control.bin"
    log_path = output_dir / "r0_probe.log"
    control_path.write_bytes(b"\x00" * r0_control.EU4_R0_CONTROL_PAGE_SIZE)

    evidence = auto.preflight(require_privilege=True)
    manifest["preflight"] = evidence
    r0_probe.build()
    r0_probe.preflight()
    auto.build()
    base.running_game_pid("idle")

    fixture_manifest = json.loads(auto.FIXTURE_MANIFEST.read_text())
    with FixtureManager(
        output_dir, base.USER_DATA, auto.FIXTURE, fixture_manifest["save_sha256"]
    ) as fixture:
        fixture.recover()
        game_log = base.USER_DATA / "logs/game.log"
        old_log = game_log.read_bytes() if game_log.is_file() else b""
        anchor = auto.mark(events_path, "clock_anchor")
        manifest["clock_anchor"] = anchor
        game = pm = None
        installed = False
        generation = 0
        try:
            working_save = fixture.install()
            installed = True
            manifest["working_save"] = str(working_save)
            pm_path = output_dir / "powermetrics.pliststream"
            with pm_path.open("wb") as raw, (output_dir / "powermetrics.stderr").open("wb") as errors:
                pm = subprocess.Popen(
                    ["sudo", "-n", str(auto.HELPER_INSTALLED)],
                    stdout=raw,
                    stderr=errors,
                )
                time.sleep(2)
                if pm.poll() is not None:
                    raise base.BenchmarkError("Powermetrics helper exited before EU IV launch")
                env = os.environ.copy()
                env.update(r0_probe.probe_env(control_path, log_path))
                env["EU4_R0_EXPECT_WIDTH"] = str(EXPECT_WIDTH)
                env["EU4_R0_EXPECT_HEIGHT"] = str(EXPECT_HEIGHT)
                with (output_dir / "game.stdout").open("wb") as stdout, (output_dir / "game.stderr").open(
                    "wb"
                ) as stderr:
                    game = subprocess.Popen(
                        ["./eu4", *evidence["launcher_args"]],
                        cwd=base.GOG_EXE.parent,
                        env=env,
                        stdout=stdout,
                        stderr=stderr,
                    )
                manifest["game_pid"] = game.pid
                preflight = thread_cpu_sampler.preflight(game.pid)
                manifest["thread_cpu_preflight"] = preflight
                if preflight.get("status") != "ready":
                    raise base.BenchmarkError(f"thread CPU preflight failed: {preflight}")

                tail = auto.PowerTail(pm_path)
                ready = auto.wait_until_ready(
                    game,
                    log_path,
                    tail,
                    anchor,
                    game_log,
                    old_log,
                    probe_parser=r0_probe_rows.r0_probe_rows,
                )
                manifest["readiness"] = ready
                manifest["warmup"] = auto.warm_up(
                    game, log_path, tail, anchor, probe_parser=r0_probe_rows.r0_probe_rows
                )
                auto.mark(events_path, "ready", **ready)
                scene_path = output_dir / "ready-scene.png"
                auto.capture_scene(scene_path)
                manifest["scene_alignment"] = auto.verify_scene(scene_path, bootstrap=False)

                host_uptime = r0_probe.event_uptime_ns()
                manifest["event_clock_alignment"] = clock_alignment_check(log_path, host_uptime)

                generation = 1
                r0_probe.write_control(
                    control_path,
                    generation=generation,
                    mode=r0_control.EU4_R0_MODE_PASSIVE,
                    scenario_id=0,
                )
                r0_probe.wait_for_ack(log_path, generation)

                epoch_a_start_ns = time.monotonic_ns()
                mark_uptime(events_path, "epoch_a_start")
                warmup_frames = r0_probe.parse_log(log_path)["frames"]
                main_thread_id = r0_probe.infer_main_thread_id(warmup_frames)
                manifest["main_thread_id"] = main_thread_id

                cpu_report = thread_cpu_sampler.poll_window(
                    game.pid,
                    duration_s=EPOCH_A_SECONDS,
                    interval_s=0.5,
                    main_thread_id=main_thread_id,
                    reconcile_tolerance_ms_per_s=CPU_RECONCILE_TOLERANCE_MS_PER_S,
                )
                tail.poll()
                epoch_a_end_ns = time.monotonic_ns()
                process_cpu = _process_cpu_ms_per_s(
                    tail.samples, anchor, epoch_a_start_ns, epoch_a_end_ns, game.pid
                )
                if process_cpu is not None:
                    cpu_report["process_cpu_ms_per_s"] = process_cpu
                    cpu_report["reconciliation_residual_ms_per_s"] = (
                        process_cpu - cpu_report["thread_cpu_ms_per_s"]
                    )
                    cpu_report["reconcile_ok"] = (
                        abs(cpu_report["reconciliation_residual_ms_per_s"])
                        <= CPU_RECONCILE_TOLERANCE_MS_PER_S
                    )
                else:
                    cpu_report["reconcile_ok"] = False
                (output_dir / "epoch_a_thread_cpu.json").write_text(json.dumps(cpu_report, indent=2) + "\n")
                manifest["epoch_a_thread_cpu"] = cpu_report
                manifest["epochs"].append({"id": "A", "thread_cpu": cpu_report})

                sample_path = output_dir / "epoch_b.sample.txt"
                run_sample(game.pid, sample_path, EPOCH_B_SAMPLE_SECONDS)
                sample_text = sample_path.read_text(errors="replace")
                manifest["sample_main_thread_corroboration"] = sample_main_thread_corroboration(
                    sample_text, main_thread_id
                )
                manifest["epochs"].append({"id": "B", "sample_path": str(sample_path)})

                generation += 1
                drawable = r0_probe.probe_production_drawable(
                    control_path, log_path, generation, timeout_s=20.0
                )
                manifest["drawable_capability"] = drawable
                if drawable.get("supported"):
                    manifest["drawable_capability"]["status"] = "supported"
                else:
                    manifest["drawable_capability"]["status"] = "unsupported_drawable"
                    manifest["positive_control_status"] = "skipped_unsupported_drawable"
                    manifest["census_frame_count"] = 0
                else:
                    generation += 1
                    r0_probe.write_control(
                        control_path,
                        generation=generation,
                        mode=r0_control.EU4_R0_MODE_CENSUS,
                        scenario_id=0,
                    )
                    r0_probe.wait_for_ack(log_path, generation)
                    time.sleep(MIN_CENSUS_SECONDS)

                    mark_uptime(events_path, "pre_window_end")
                    print(
                        "Operator: toggle the province panel UI, then press Enter to confirm.",
                        file=sys.stderr,
                    )
                    try:
                        input()
                    except EOFError:
                        time.sleep(2.0)
                    mark_uptime(events_path, "ui_action_confirmed")
                    time.sleep(TRANSITION_DISCARD_FRAMES / 60.0)
                    mark_uptime(events_path, "post_window_start")

                    generation += 1
                    r0_probe.write_control(
                        control_path,
                        generation=generation,
                        mode=r0_control.EU4_R0_MODE_CENSUS,
                        scenario_id=1,
                    )
                    r0_probe.wait_for_ack(log_path, generation)
                    time.sleep(MIN_CENSUS_SECONDS)

                    parsed = r0_probe.parse_log(log_path)
                    census_frames = [
                        f
                        for f in parsed["frames"]
                        if f.get("census_attempted") and f.get("census_ok") and f.get("flush_ok")
                    ]
                    manifest["census_frame_count"] = len(census_frames)
                    manifest["epochs"].append(
                        {
                            "id": "C",
                            "acks": parsed["acks"],
                            "frames": len(parsed["frames"]),
                            "log_path": str(log_path),
                        }
                    )
                    manifest["positive_control_status"] = "pending_offline_analysis"

                auto.stop_process(pm, 5)
        except (base.BenchmarkError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
            manifest["status"] = "incomplete"
            manifest["error"] = str(exc)
            raise
        finally:
            try:
                auto.stop_process(pm, 3)
                auto.stop_process(game, 10)
                if installed:
                    manifest["working_save_changed"] = fixture.finish(output_dir)
            finally:
                run_offline_analyzers(output_dir, manifest)
                if manifest.get("status") not in TERMINAL_FAILURE_STATUSES:
                    acceptance_gates(manifest)
                manifest["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
                write_manifest(output_dir / "manifest.json", manifest)
    return manifest


def session(output_dir: Path, *, dry_run: bool = False, integrated: bool = False) -> dict[str, Any]:
    if integrated:
        return run_integrated(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema": "r0_measurement_session_v1",
        "cpu_reconcile_tolerance_ms_per_s": CPU_RECONCILE_TOLERANCE_MS_PER_S,
        "transition_discard_frames": TRANSITION_DISCARD_FRAMES,
        "measurement_valid_for_cpu": {"epoch_a": True, "epoch_b": True, "epoch_c": False},
        "epochs": [],
        "scenarios": [scenario.__dict__ for scenario in DEFAULT_SCENARIOS],
    }
    if dry_run:
        manifest["status"] = "dry_run"
        write_manifest(output_dir / "manifest.json", manifest)
        return manifest

    r0_probe.build()
    preflight = thread_cpu_sampler.preflight(1)
    manifest["thread_cpu_preflight"] = preflight

    control_path = output_dir / "r0_control.bin"
    log_path = output_dir / "r0_probe.log"
    control_path.write_bytes(b"\x00" * r0_control.EU4_R0_CONTROL_PAGE_SIZE)

    generation = 1
    r0_probe.write_control(
        control_path, generation=generation, mode=r0_control.EU4_R0_MODE_PASSIVE, scenario_id=0
    )

    env = os.environ.copy()
    env.update(r0_probe.probe_env(control_path, log_path))
    attach_pid = os.environ.get("EU4_R0_ATTACH_PID")
    if not attach_pid:
        manifest["status"] = "awaiting_game_launch"
        manifest["launch_env"] = {key: env[key] for key in ("EU4_R0_CONTROL", "EU4_R0_LOG", "DYLD_INSERT_LIBRARIES")}
        write_manifest(output_dir / "manifest.json", manifest)
        return manifest

    pid = int(attach_pid)
    preflight = thread_cpu_sampler.preflight(pid)
    manifest["thread_cpu_preflight"] = preflight
    if preflight.get("status") != "ready":
        manifest["status"] = "needs_permission"
        write_manifest(output_dir / "manifest.json", manifest)
        return manifest

    cpu_report = thread_cpu_sampler.poll_window(pid, duration_s=EPOCH_A_SECONDS, interval_s=0.5)
    (output_dir / "epoch_a_thread_cpu.json").write_text(json.dumps(cpu_report, indent=2) + "\n")
    manifest["epochs"].append({"id": "A", "thread_cpu": cpu_report})

    sample_path = output_dir / "epoch_b.sample.txt"
    run_sample(pid, sample_path, EPOCH_B_SAMPLE_SECONDS)
    manifest["epochs"].append({"id": "B", "sample_path": str(sample_path)})

    generation += 1
    r0_probe.write_control(
        control_path, generation=generation, mode=r0_control.EU4_R0_MODE_CENSUS, scenario_id=0
    )
    r0_probe.wait_for_ack(log_path, generation)
    time.sleep(MIN_CENSUS_SECONDS)
    generation += 1
    r0_probe.write_control(
        control_path,
        generation=generation,
        mode=r0_control.EU4_R0_MODE_CENSUS,
        scenario_id=1,
    )
    r0_probe.wait_for_ack(log_path, generation)
    time.sleep(1.0)

    parsed = r0_probe.parse_log(log_path)
    manifest["epochs"].append(
        {
            "id": "C",
            "acks": parsed["acks"],
            "frames": len(parsed["frames"]),
            "log_path": str(log_path),
        }
    )
    manifest["status"] = "completed_attach_mode"
    write_manifest(output_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-integrated", action="store_true")
    args = parser.parse_args()
    payload = session(args.output_dir, dry_run=args.dry_run, integrated=args.run_integrated)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
