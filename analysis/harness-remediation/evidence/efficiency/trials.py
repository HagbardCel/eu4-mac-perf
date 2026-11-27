#!/usr/bin/env python3
"""Three paired A/B trials on one fixed sealed workload (present 904).

Arm A is the legacy two-step interface: execute the independent replay and read
the producer's full ``replay-report.json`` (6.2 MB on this case). Arm B is the
canonical agent interface: ``eu4_replay independent`` executes the same replay
and returns the compact ``gfx-agent-diagnosis/v1`` envelope. The renderer,
frame, shaders and checkpoints are identical; only the projection the caller
consumes differs.

The trial measures machine-side wall time to a correct first-divergence
classification, payload bytes, replay invocations, cache state and process
launches. It does not measure model reasoning time.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_REPO = Path(__file__).resolve().parents[4]
REPO = DEFAULT_REPO
FRAME = DEFAULT_REPO / ".local/m7/goal-round1/capture-c/frame"
SHADERS = DEFAULT_REPO / ".local/m7/goal-round1/capture-c/shadow/shaders"
EVIDENCE = Path(__file__).resolve().parent
EXPECTED_EVENT = 15
EXPECTED_PIXELS = 4
EXPECTED_MAX = 1


def _machine() -> dict:
    return {
        "host": platform.node(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
    }


def _eu4_processes() -> list[str]:
    try:
        out = subprocess.run(["pgrep", "-fl", "eu4.app/Contents/MacOS/eu4"],
                             capture_output=True, text=True, check=False)
    except OSError:
        return []
    return [line for line in out.stdout.splitlines() if line.strip()]


def _shader_inventory() -> dict:
    files = sorted(p for p in SHADERS.iterdir() if p.is_file())
    return {
        "path": str(SHADERS),
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
    }


def _checkpoint_stats(checkpoints: list[dict], event: int) -> dict | None:
    for row in checkpoints:
        if row.get("event") != event:
            continue
        stats = row.get("statistics") or {}
        return {
            "mismatched_pixels": stats.get("mismatched_pixels"),
            "max_channel_error": stats.get("max_channel_error"),
            "alpha_different_pixels": stats.get("alpha_different_pixels"),
            "first_differing_pixel": stats.get("first_differing_pixel"),
            "status": row.get("status"),
            "metal_sha256": row.get("metal_sha256"),
            "reference_sha256": row.get("reference_sha256"),
        }
    return None


def _run_legacy(out: Path, repo: Path) -> dict:
    command = [sys.executable, "-m", "automation.gfx_independent_replay",
               "--frame", str(FRAME), "--shaders", str(SHADERS),
               "--out", str(out)]
    completed = subprocess.run(command, capture_output=True, text=True,
                               check=False, timeout=600, cwd=repo)
    report_path = out / "replay-report.json"
    report_bytes = report_path.stat().st_size if report_path.is_file() else 0
    report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    report = report.get("diagnosis") or report
    first = (report.get("first_divergence") or {}).get("event")
    stats = _checkpoint_stats(report.get("checkpoints") or [], EXPECTED_EVENT)
    qualification = report.get("qualification") or {}
    return {
        "arm": "A-legacy-full-report",
        "command": command,
        "exit_code": completed.returncode,
        "replay_invocations": 1,
        "diagnosis_invocations": 1,
        "payload_bytes": report_bytes,
        "first_divergence_event": first,
        "checkpoint_15": stats,
        "classification": qualification.get("classification")
                        or report.get("classification"),
        "axes": qualification.get("axes"),
        "tool_failure": False,
        "stderr_tail": (completed.stderr or "").strip()[-500:],
        "stdout_bytes": len(completed.stdout or ""),
    }


def _run_canonical(out: Path, repo: Path) -> dict:
    command = [sys.executable, "-m", "automation.agent_tools",
               "eu4_replay", "independent", "--frame", str(FRAME),
               "--shaders", str(SHADERS), "--replay-out", str(out)]
    completed = subprocess.run(command, capture_output=True, text=True,
                               check=False, timeout=600, cwd=repo)
    text = (completed.stdout or "").strip()
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        envelope = {}
    result = envelope.get("result") or {}
    outcome = envelope.get("outcome") or {}
    failure = envelope.get("failure") or {}
    checkpoints = result.get("checkpoints") or []
    checkpoint = next((row for row in checkpoints
                       if row.get("event") == EXPECTED_EVENT), None)
    return {
        "arm": "B-canonical-compact",
        "command": command,
        "exit_code": completed.returncode,
        "replay_invocations": 1,
        "diagnosis_invocations": 1,
        "payload_bytes": len(text.encode("utf-8")),
        "envelope_bytes": envelope.get("output_bytes"),
        "truncated": envelope.get("truncated") or result.get("truncated"),
        "first_divergence_event": (result.get("first_divergence") or {}).get("event"),
        "checkpoint_15": ({
            "mismatched_pixels": checkpoint.get("mismatched_pixels"),
            "max_channel_error": checkpoint.get("max_channel_error"),
            "alpha_different_pixels": checkpoint.get("alpha_different_pixels"),
            "first_differing_pixel": (result.get("first_divergence") or {}).get(
                "pixel_location"),
            "status": checkpoint.get("status"),
        } if checkpoint else None),
        "classification": result.get("classification"),
        "axes": result.get("axes"),
        "tool_failure": failure.get("kind") == "tool_failure",
        "failure_kind": failure.get("kind"),
        "stderr_tail": (completed.stderr or "").strip()[-500:],
        "stdout_bytes": len(text.encode("utf-8")),
    }


def _verdict(record: dict) -> dict:
    stats = record.get("checkpoint_15") or {}
    correct_first = record.get("first_divergence_event") == EXPECTED_EVENT
    correct_pixels = stats.get("mismatched_pixels") == EXPECTED_PIXELS
    correct_max = stats.get("max_channel_error") == EXPECTED_MAX
    correct = bool(correct_first and correct_pixels and correct_max
                   and not record.get("tool_failure"))
    return {
        "correct_first_divergence": correct_first,
        "correct_checkpoint_pixels": correct_pixels,
        "correct_checkpoint_max_error": correct_max,
        "not_a_tool_failure": not record.get("tool_failure"),
        "correct": correct,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--order", default="AB,BA,AB")
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--frame", type=Path, default=None)
    parser.add_argument("--shaders", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=EVIDENCE / "trials.json")
    args = parser.parse_args(argv)
    global REPO, FRAME, SHADERS
    REPO = args.repo.resolve()
    FRAME = (args.frame or REPO / ".local/m7/goal-round1/capture-c/frame").resolve()
    SHADERS = (args.shaders or REPO / ".local/m7/goal-round1/capture-c/shadow/shaders").resolve()
    for path in (FRAME, SHADERS):
        if not path.exists():
            raise SystemExit(f"missing fixed-workload input: {path}")
    order = [item.strip() for item in args.order.split(",") if item.strip()]
    inventory = _shader_inventory()
    runs_root = EVIDENCE / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    for trial in range(1, args.trials + 1):
        sequence = order[(trial - 1) % len(order)]
        for arm in sequence:
            out = runs_root / f"trial-{trial}-{arm}"
            shutil.rmtree(out, ignore_errors=True)
            out.mkdir(parents=True)
            started = time.perf_counter()
            try:
                record = (_run_legacy(out, REPO) if arm == "A"
                          else _run_canonical(out, REPO))
            except (OSError, subprocess.SubprocessError) as exc:
                record = {"arm": arm, "exit_code": None,
                          "error": f"{type(exc).__name__}: {exc}"}
            record["wall_seconds"] = round(time.perf_counter() - started, 3)
            record["trial"] = trial
            record["order_position"] = arm
            record["verdict"] = _verdict(record)
            record["eu4_processes"] = _eu4_processes()
            record["shader_cache"] = inventory
            record["out"] = str(out)
            records.append(record)
            print(json.dumps({key: record.get(key) for key in (
                "trial", "order_position", "arm", "wall_seconds", "exit_code",
                "payload_bytes", "first_divergence_event", "classification",
                "tool_failure")}, sort_keys=True), flush=True)
    document = {
        "schema": "harness-efficiency-trials/v1",
        "machine": _machine(),
        "workload": {
            "frame": str(FRAME),
            "present_index": 904,
            "shaders": inventory,
            "expected": {"first_divergence_event": EXPECTED_EVENT,
                         "checkpoint_15_pixels": EXPECTED_PIXELS,
                         "checkpoint_15_max_channel_error": EXPECTED_MAX},
        },
        "arms": {
            "A": "independent replay + full producer replay-report.json",
            "B": "eu4_replay independent compact gfx-agent-diagnosis/v1",
        },
        "trials": records,
    }
    args.out.write_text(json.dumps(document, indent=2) + "\n",
                        encoding="utf-8")
    return 0 if all(record["verdict"]["correct"] for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
