#!/usr/bin/env python3
"""One-launch R0 measurement session: epochs A (CPU), B (sample), C (census)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
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


def wait_for_ack(log_path: Path, generation: int, timeout_s: float = 10.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        parsed = r0_probe.parse_log(log_path)
        for ack in parsed["acks"]:
            if ack["generation"] == generation:
                return ack
        time.sleep(0.1)
    raise base.BenchmarkError(f"Timed out waiting for ACK generation={generation}")


def run_sample(pid: int, output: Path, seconds: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["sample", str(pid), str(seconds), "-file", str(output), "-mayDie"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(f"sample failed: {result.stderr.strip() or result.stdout.strip()}")


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _process_cpu_ms_per_s(samples: list[dict], start_ns: int, end_ns: int, pid: int) -> float | None:
    values = auto.cpu_samples_between(samples, pid, {}, start_ns, end_ns)
    if not values:
        return None
    return float(sum(v for _, v in values) / len(values))


def acceptance_gates(manifest: dict[str, Any]) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    cpu = manifest.get("epoch_a_thread_cpu", {})
    reconcile = cpu.get("reconcile_ok")
    gates["cpu_reconcile"] = reconcile is True or reconcile is None
    gates["thread_cpu_preflight"] = manifest.get("thread_cpu_preflight", {}).get("status") == "ready"
    gates["census_frames"] = manifest.get("census_frame_count", 0) >= MIN_CENSUS_FRAMES
    gates["positive_control"] = manifest.get("positive_control_status") in (
        "passed",
        "not_evaluated_offline",
    )
    manifest["acceptance_gates"] = gates
    manifest["status"] = "completed" if all(gates.values()) else "incomplete_gates"
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

    r0_probe.build()
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
        clock_offset_ns = 0
        game = pm = None
        installed = False
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
                        ["./eu4", "--continuelastsave"],
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

                epoch_a_start_ns = time.monotonic_ns()
                auto.mark(events_path, "epoch_a_start")
                cpu_report = thread_cpu_sampler.poll_window(
                    game.pid,
                    duration_s=EPOCH_A_SECONDS,
                    interval_s=0.5,
                )
                tail.poll()
                epoch_a_end_ns = time.monotonic_ns()
                process_cpu = _process_cpu_ms_per_s(tail.samples, epoch_a_start_ns, epoch_a_end_ns, game.pid)
                if process_cpu is not None:
                    cpu_report["process_cpu_ms_per_s"] = process_cpu
                    cpu_report["reconciliation_residual_ms_per_s"] = process_cpu - cpu_report["thread_cpu_ms_per_s"]
                    cpu_report["reconcile_ok"] = (
                        abs(cpu_report["reconciliation_residual_ms_per_s"]) <= CPU_RECONCILE_TOLERANCE_MS_PER_S
                    )
                (output_dir / "epoch_a_thread_cpu.json").write_text(json.dumps(cpu_report, indent=2) + "\n")
                manifest["epoch_a_thread_cpu"] = cpu_report
                manifest["epochs"].append({"id": "A", "thread_cpu": cpu_report})

                sample_path = output_dir / "epoch_b.sample.txt"
                run_sample(game.pid, sample_path, EPOCH_B_SAMPLE_SECONDS)
                manifest["epochs"].append({"id": "B", "sample_path": str(sample_path)})

                generation = 1
                r0_probe.write_control(
                    control_path,
                    generation=generation,
                    mode=r0_control.EU4_R0_MODE_CENSUS,
                    scenario_id=0,
                )
                wait_for_ack(log_path, generation)
                time.sleep(MIN_CENSUS_SECONDS)

                generation += 1
                r0_probe.write_control(
                    control_path,
                    generation=generation,
                    mode=r0_control.EU4_R0_MODE_CENSUS,
                    scenario_id=1,
                )
                wait_for_ack(log_path, generation)
                auto.mark(events_path, "ui_action_confirmed", monotonic_ns=time.monotonic_ns())
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
                census_frames = [
                    f
                    for f in parsed["frames"]
                    if f.get("census_attempted") and f.get("census_ok") and f.get("flush_ok")
                ]
                manifest["census_frame_count"] = len(census_frames)
                if census_frames and census_frames[0].get("uptime_ns"):
                    clock_offset_ns = int(census_frames[0]["uptime_ns"]) - anchor["monotonic_ns"]
                manifest["clock_offset_ns"] = clock_offset_ns
                manifest["positive_control_status"] = "not_evaluated_offline"
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
                manifest["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
                acceptance_gates(manifest)
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
    wait_for_ack(log_path, generation)
    time.sleep(MIN_CENSUS_SECONDS)
    generation += 1
    r0_probe.write_control(
        control_path,
        generation=generation,
        mode=r0_control.EU4_R0_MODE_CENSUS,
        scenario_id=1,
    )
    wait_for_ack(log_path, generation)
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
