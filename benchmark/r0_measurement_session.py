#!/usr/bin/env python3
"""One-launch R0 measurement session: epochs A (CPU), B (sample), C (census)."""

from __future__ import annotations

import argparse
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
import r0_probe
import thread_cpu_sampler


ROOT = Path(__file__).resolve().parents[1]
EPOCH_A_SECONDS = 30.0
EPOCH_B_SAMPLE_SECONDS = 10
MIN_CENSUS_FRAMES = 120
MIN_CENSUS_SECONDS = 2.0
CPU_RECONCILE_TOLERANCE_MS_PER_S = 50.0
TRANSITION_DISCARD_FRAMES = 3


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


def session(output_dir: Path, *, dry_run: bool = False) -> dict[str, Any]:
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

    auto.build()
    r0_probe.build()
    preflight = thread_cpu_sampler.preflight()
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
    # Launch is operator-driven for GOG EU IV; this session documents the protocol and
    # supports attaching to an already running game when EU4_R0_ATTACH_PID is set.
    attach_pid = os.environ.get("EU4_R0_ATTACH_PID")
    if not attach_pid:
        manifest["status"] = "awaiting_game_launch"
        manifest["launch_env"] = {key: env[key] for key in ("EU4_R0_CONTROL", "EU4_R0_LOG", "DYLD_INSERT_LIBRARIES")}
        write_manifest(output_dir / "manifest.json", manifest)
        return manifest

    pid = int(attach_pid)
    epoch_a_start = time.time()
    cpu_report = thread_cpu_sampler.poll_window(pid, duration_s=EPOCH_A_SECONDS, interval_s=0.1)
    (output_dir / "epoch_a_thread_cpu.json").write_text(json.dumps(cpu_report, indent=2) + "\n")
    manifest["epochs"].append({"id": "A", "started_at": epoch_a_start, "thread_cpu": cpu_report})

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
    args = parser.parse_args()
    payload = session(args.output_dir, dry_run=args.dry_run)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
