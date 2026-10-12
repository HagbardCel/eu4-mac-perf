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
BASELINE_MIN_FRAMES = 240
MIN_CENSUS_SECONDS = 2.0
SCENARIO_1_MIN_FRAMES = 30
SCENARIO_1_MIN_SECONDS = 1.0
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


def _ensure_repo_import_path() -> None:
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def sample_main_thread_corroboration(sample_text: str, main_thread_id: int | None) -> str:
    if main_thread_id is None:
        return "unavailable"
    _ensure_repo_import_path()
    from analysis.tools.r0_stack_analyze import partition_sample_call_graph, thread_header_id

    preamble, call_graph = partition_sample_call_graph(sample_text)
    call_graph_ids: list[int] = []
    for line in call_graph:
        parsed = thread_header_id(line)
        if parsed:
            call_graph_ids.append(parsed[1])
    if call_graph_ids:
        return "matched" if main_thread_id in call_graph_ids else "unavailable"
    for line in preamble:
        parsed = thread_header_id(line)
        if parsed and parsed[1] == main_thread_id:
            return "matched"
    return "unavailable"


def _analyzer_ran(command: list[str], env: dict[str, str], output_path: Path) -> tuple[int, dict[str, Any] | None, str | None]:
    temp_path = output_path.with_suffix(".tmp.json")
    command = command.copy()
    output_index = command.index("--output") + 1
    command[output_index] = str(temp_path)
    try:
        if output_path.is_file():
            output_path.unlink()
        if temp_path.is_file():
            temp_path.unlink()
        result = subprocess.run(command, capture_output=True, text=True, check=False, cwd=str(ROOT), env=env)
        if result.returncode != 0:
            return result.returncode, None, "nonzero_exit"
        if not temp_path.is_file():
            return result.returncode, None, "missing_output"
        try:
            payload = json.loads(temp_path.read_text())
        except json.JSONDecodeError:
            return result.returncode, None, "malformed_json"
        temp_path.replace(output_path)
        return result.returncode, payload, None
    except OSError as exc:
        return 1, None, str(exc)
    finally:
        if temp_path.is_file():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _apply_positive_control_status(manifest: dict[str, Any], payload: dict[str, Any] | None) -> None:
    if manifest.get("positive_control_status") == "skipped_unsupported_drawable":
        return
    if not payload:
        manifest["positive_control_status"] = "failed"
        return
    status = payload.get("positive_control", {}).get("status")
    manifest["positive_control_status"] = "passed" if status == "passed" else "failed"


def _positive_control_gate(manifest: dict[str, Any]) -> bool:
    report = manifest.get("frame_evolution_report")
    if report is not None:
        return report.get("positive_control", {}).get("status") == "passed"
    if manifest.get("schema") != "r0_measurement_session_v1":
        return manifest.get("positive_control_status") == "passed"
    return False


def _flush_single_owner_gate(manifest: dict[str, Any], output_dir: Path) -> bool:
    log_path = _probe_log_path_from_manifest(manifest, output_dir)
    if not log_path.is_file():
        return False
    return r0_probe.flush_single_owner_passes(r0_probe.parse_log(log_path))


def _roi_verified_gate(manifest: dict[str, Any]) -> bool:
    report = manifest.get("frame_evolution_report")
    if not report:
        return False
    return report.get("roi_config", {}).get("verified") is True


def _probe_log_path_from_manifest(manifest: dict[str, Any], output_dir: Path) -> Path:
    if manifest.get("probe_log_path"):
        return Path(manifest["probe_log_path"])
    for epoch in manifest.get("epochs", []):
        if epoch.get("log_path"):
            return Path(epoch["log_path"])
    return output_dir / "r0_probe.log"


def run_offline_analyzers(
    output_dir: Path, manifest: dict[str, Any], *, roi_config: Path | None = None
) -> None:
    log_path = _probe_log_path_from_manifest(manifest, output_dir)
    events_path = output_dir / "events.jsonl"
    frame_out = output_dir / "frame_evolution_report.json"
    stack_out = output_dir / "stack_report.json"
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    manifest["analyzer_results"] = {}
    manifest.pop("frame_evolution_report", None)
    manifest.pop("stack_report", None)
    for artifact in (frame_out, stack_out):
        if artifact.is_file():
            try:
                artifact.unlink()
            except OSError:
                pass
    if not log_path.is_file():
        manifest["analyzer_results"]["frame_evolution"] = {
            "exit_code": 1,
            "error": "missing_probe_log",
        }
        _apply_positive_control_status(manifest, None)
        return
    captures = manifest.get("captures", {})
    pre_gen = captures.get("pre", {}).get("generation")
    post_gen = captures.get("post", {}).get("generation")
    frame_cmd = [
        sys.executable,
        str(ROOT / "analysis/tools/frame_evolution_analyze.py"),
        str(log_path),
        "--output",
        str(frame_out),
        "--events",
        str(events_path),
    ]
    capture_dir = output_dir / "captures"
    if capture_dir.is_dir() and pre_gen and post_gen:
        frame_cmd.extend(
            [
                "--capture-dir",
                str(capture_dir),
                "--pre-generation",
                str(pre_gen),
                "--post-generation",
                str(post_gen),
            ]
        )
    roi_path = roi_config or manifest.get("roi_config_path")
    if roi_path:
        frame_cmd.extend(["--roi-config", str(roi_path)])
    elif manifest.get("roi_pending_verification"):
        frame_cmd.append("--allow-unverified-roi")
    if manifest.get("frame_evolution_status") == "skipped_unsupported_drawable":
        manifest["analyzer_results"]["frame_evolution"] = {"exit_code": 0, "skipped": True}
    else:
        try:
            code, payload, error = _analyzer_ran(frame_cmd, env, frame_out)
            manifest["analyzer_results"]["frame_evolution"] = {"exit_code": code, "error": error}
            if payload:
                manifest["frame_evolution_report"] = payload
            _apply_positive_control_status(manifest, payload)
        except Exception as exc:  # noqa: BLE001 — must not break integrated cleanup
            manifest["analyzer_results"]["frame_evolution"] = {
                "exit_code": 1,
                "error": f"analyzer_exception:{exc}",
            }
            _apply_positive_control_status(manifest, None)

    sample_path = output_dir / "epoch_b.sample.txt"
    if sample_path.is_file():
        stack_cmd = [
            sys.executable,
            str(ROOT / "analysis/tools/r0_stack_analyze.py"),
            str(sample_path),
            "--output",
            str(stack_out),
        ]
        if manifest.get("main_thread_id"):
            stack_cmd.extend(["--main-thread-id", str(manifest["main_thread_id"])])
        try:
            code, payload, error = _analyzer_ran(stack_cmd, env, stack_out)
            manifest["analyzer_results"]["stack"] = {"exit_code": code, "error": error}
            if payload:
                manifest["stack_report"] = payload
        except Exception as exc:  # noqa: BLE001
            manifest["analyzer_results"]["stack"] = {"exit_code": 1, "error": f"analyzer_exception:{exc}"}


def _qualified_windows_pass(report: dict[str, Any] | None, manifest: dict[str, Any]) -> bool:
    if not report:
        return False
    windows = report.get("qualified_windows") or {}
    thresholds = {
        "0": (BASELINE_MIN_FRAMES, MIN_CENSUS_SECONDS, "baseline"),
        "1": (SCENARIO_1_MIN_FRAMES, SCENARIO_1_MIN_SECONDS, "scenario_1_census"),
    }
    acquisition = manifest.get("acquisition_generations") or {}
    for key, (min_frames, min_seconds, gen_key) in thresholds.items():
        window = windows.get(key) or {}
        if not window.get("qualified"):
            return False
        if int(window.get("frame_count", 0)) < min_frames:
            return False
        if float(window.get("duration_seconds") or 0) < min_seconds:
            return False
        if window.get("continuity", {}).get("status") != "passed":
            return False
        expected_gen = acquisition.get(gen_key)
        if expected_gen is not None and window.get("control_generation") != expected_gen:
            return False
    return True


def _stack_report_valid(report: dict[str, Any] | None) -> bool:
    if not report:
        return False
    if "functional_caller" not in report or "attributed_sample_weight" not in report:
        return False
    attributed = int(report.get("attributed_sample_weight", 0))
    root_inclusive = int(report.get("root_inclusive", 0))
    if attributed <= 0 or root_inclusive <= 0:
        return False
    return attributed == root_inclusive


def acceptance_gates(manifest: dict[str, Any], output_dir: Path | None = None) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    cpu = manifest.get("epoch_a_thread_cpu", {})
    gates["cpu_reconcile"] = cpu.get("reconcile_ok") is True
    if manifest.get("schema") == "r0_measurement_session_v1":
        if output_dir is None:
            gates["flush_single_owner"] = False
        else:
            gates["flush_single_owner"] = _flush_single_owner_gate(manifest, output_dir)
    elif output_dir is not None:
        gates["flush_single_owner"] = _flush_single_owner_gate(manifest, output_dir)
    else:
        gates["flush_single_owner"] = True
    gates["main_thread_mapping"] = cpu.get("main_thread_mapping_status") == "verified"
    gates["thread_cpu_preflight"] = manifest.get("thread_cpu_preflight", {}).get("status") == "ready"
    clock_status = manifest.get("event_clock_alignment", {}).get("status")
    gates["event_clock_alignment"] = clock_status == "passed"
    analyzers = manifest.get("analyzer_results", {})
    frame_report = manifest.get("frame_evolution_report")
    stack_report = manifest.get("stack_report")
    gates["stack_analyzer"] = analyzers.get("stack", {}).get("exit_code") == 0 and _stack_report_valid(
        stack_report
    )
    drawable = manifest.get("drawable_capability", {})
    partial = drawable.get("failure_class") == "unsupported_drawable"
    if partial:
        gates["qualified_windows"] = True
        gates["positive_control"] = True
        gates["roi_verified"] = True
        gates["frame_analyzer"] = analyzers.get("frame_evolution", {}).get("skipped") is True
    else:
        gates["qualified_windows"] = _qualified_windows_pass(frame_report, manifest)
        gates["positive_control"] = _positive_control_gate(manifest)
        gates["roi_verified"] = _roi_verified_gate(manifest)
        gates["frame_analyzer"] = analyzers.get("frame_evolution", {}).get("exit_code") == 0
    prior = manifest.get("status")
    if prior in TERMINAL_FAILURE_STATUSES:
        pass
    elif partial and all(gates.values()):
        manifest["status"] = "partial_complete"
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
    manifest["probe_log_path"] = str(log_path)
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
                capture_dir = output_dir / "captures"
                env = os.environ.copy()
                env.update(r0_probe.probe_env(control_path, log_path, capture_dir=capture_dir))
                env["EU4_R0_EXPECT_WIDTH"] = str(EXPECT_WIDTH)
                env["EU4_R0_EXPECT_HEIGHT"] = str(EXPECT_HEIGHT)
                with (output_dir / "game.stdout").open("wb") as stdout, (output_dir / "game.stderr").open(
                    "wb"
                ) as stderr:
                    game = subprocess.Popen(
                        ["./eu4", *evidence["launcher_args"], "--continuelastsave"],
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

                generation = 1
                manifest["event_clock_alignment"] = r0_probe.paired_clock_alignment(
                    control_path, log_path, generation
                )
                generation += 1
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
                failure_class = drawable.get("failure_class", "unknown_failure")
                if drawable.get("supported"):
                    manifest["drawable_capability"]["status"] = "supported"
                    manifest["captures"] = {}

                    generation += 1
                    gen_baseline = generation
                    r0_probe.write_control(
                        control_path,
                        generation=gen_baseline,
                        mode=r0_control.EU4_R0_MODE_CENSUS,
                        scenario_id=0,
                    )
                    r0_probe.wait_for_ack(log_path, gen_baseline)
                    baseline_window = r0_probe.wait_until_qualified_window(
                        log_path,
                        gen_baseline,
                        min_frames=BASELINE_MIN_FRAMES,
                        min_seconds=MIN_CENSUS_SECONDS,
                    )
                    manifest["baseline_window"] = baseline_window
                    if baseline_window.get("status") != "qualified":
                        raise base.BenchmarkError(
                            f"baseline window incomplete: {baseline_window.get('reason')}"
                        )

                    generation += 1
                    gen_pre = generation
                    r0_probe.write_control(
                        control_path,
                        generation=gen_pre,
                        mode=r0_control.EU4_R0_MODE_CENSUS_CAPTURE,
                        scenario_id=0,
                    )
                    r0_probe.wait_for_ack(log_path, gen_pre)
                    events_so_far = [
                        json.loads(line)
                        for line in events_path.read_text(errors="replace").splitlines()
                        if line.strip()
                    ]
                    pre_cap = r0_probe.wait_for_capture_verified(capture_dir, log_path, gen_pre)
                    pre_verify = r0_probe.verify_capture_artifact(capture_dir, gen_pre, log_path)
                    if pre_verify.get("status") != "verified":
                        raise base.BenchmarkError(f"pre capture provenance failed: {pre_verify}")
                    manifest["captures"]["pre"] = {"generation": gen_pre, **pre_cap}

                    generation += 1
                    gen_passive = generation
                    r0_probe.write_control(
                        control_path,
                        generation=gen_passive,
                        mode=r0_control.EU4_R0_MODE_PASSIVE,
                        scenario_id=0,
                    )
                    r0_probe.wait_for_ack(log_path, gen_passive)
                    manifest["passive_generation"] = gen_passive
                    mark_uptime(events_path, "pre_window_end")

                    print(
                        "Operator: open the province panel, restore game focus, then press Enter.",
                        file=sys.stderr,
                    )
                    try:
                        input()
                    except EOFError:
                        time.sleep(2.0)
                    mark_uptime(events_path, "ui_action_confirmed")
                    time.sleep(TRANSITION_DISCARD_FRAMES / 60.0)
                    focus_state = auto.ensure_game_focus_interior(game.pid)
                    if not focus_state.frontmost or not focus_state.interior:
                        raise base.BenchmarkError("failed to restore game focus before post window")
                    mark_uptime(events_path, "post_window_start")
                    time.sleep(1.0)

                    generation += 1
                    gen_post = generation
                    r0_probe.write_control(
                        control_path,
                        generation=gen_post,
                        mode=r0_control.EU4_R0_MODE_CENSUS_CAPTURE,
                        scenario_id=1,
                    )
                    r0_probe.wait_for_ack(log_path, gen_post)
                    events_so_far = [
                        json.loads(line)
                        for line in events_path.read_text(errors="replace").splitlines()
                        if line.strip()
                    ]
                    post_cap = r0_probe.wait_for_capture_verified(capture_dir, log_path, gen_post)
                    post_verify = r0_probe.verify_capture_artifact(
                        capture_dir, gen_post, log_path, events=events_so_far, window="post"
                    )
                    if post_verify.get("status") != "verified":
                        raise base.BenchmarkError(f"post capture provenance failed: {post_verify}")
                    manifest["captures"]["post"] = {"generation": gen_post, **post_cap}

                    generation += 1
                    gen_scenario_1 = generation
                    r0_probe.write_control(
                        control_path,
                        generation=gen_scenario_1,
                        mode=r0_control.EU4_R0_MODE_CENSUS,
                        scenario_id=1,
                    )
                    r0_probe.wait_for_ack(log_path, gen_scenario_1)
                    scenario_1_window = r0_probe.wait_until_qualified_window(
                        log_path,
                        gen_scenario_1,
                        min_frames=SCENARIO_1_MIN_FRAMES,
                        min_seconds=SCENARIO_1_MIN_SECONDS,
                    )
                    manifest["scenario_1_window"] = scenario_1_window
                    manifest["acquisition_generations"] = {
                        "baseline": gen_baseline,
                        "pre_capture": gen_pre,
                        "passive": gen_passive,
                        "post_capture": gen_post,
                        "scenario_1_census": gen_scenario_1,
                    }
                    manifest["roi_pending_verification"] = True
                    manifest["roi_config_path"] = str(ROOT / "fixtures" / "venice_panel_roi.json")

                    parsed = r0_probe.parse_log(log_path)
                    census_frames = [
                        f
                        for f in parsed["frames"]
                        if f.get("census_attempted") and f.get("census_ok") and f.get("flush_ok")
                    ]
                    manifest["census_frame_count"] = len(census_frames)
                    by_scenario: dict[str, int] = {}
                    for frame in census_frames:
                        key = str(frame.get("scenario_id", 0))
                        by_scenario[key] = by_scenario.get(key, 0) + 1
                    manifest["census_frame_count_by_scenario"] = by_scenario
                    manifest["epochs"].append(
                        {
                            "id": "C",
                            "acks": parsed["acks"],
                            "frames": len(parsed["frames"]),
                            "log_path": str(log_path),
                        }
                    )
                    manifest["positive_control_status"] = "pending_offline_analysis"
                    manifest["frame_evolution_status"] = "run"
                elif failure_class == "unsupported_drawable":
                    manifest["drawable_capability"]["status"] = "unsupported_drawable"
                    manifest["positive_control_status"] = "skipped_unsupported_drawable"
                    manifest["frame_evolution_status"] = "skipped_unsupported_drawable"
                    manifest["census_frame_count"] = 0
                    manifest["census_frame_count_by_scenario"] = {}
                else:
                    manifest["drawable_capability"]["status"] = "failed"
                    manifest["status"] = "incomplete"
                    manifest["error"] = f"drawable probe failed: {failure_class}"

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
                    try:
                        manifest["working_save_changed"] = fixture.finish(output_dir)
                    except Exception as exc:
                        manifest["status"] = "cleanup_error"
                        manifest["cleanup_error"] = str(exc)
            finally:
                run_offline_analyzers(output_dir, manifest)
                if manifest.get("status") not in TERMINAL_FAILURE_STATUSES:
                    acceptance_gates(manifest, output_dir)
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


def requalify_session(output_dir: Path, *, roi_config: Path | None = None) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        raise base.BenchmarkError(f"missing manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("status") == "cleanup_error":
        return manifest
    if roi_config:
        manifest["roi_config_path"] = str(roi_config)
        manifest["roi_pending_verification"] = False
    run_offline_analyzers(output_dir, manifest, roi_config=roi_config)
    if manifest.get("status") not in TERMINAL_FAILURE_STATUSES:
        acceptance_gates(manifest, output_dir)
    manifest["requalified_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    write_manifest(manifest_path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-integrated", action="store_true")
    parser.add_argument("--requalify", action="store_true")
    parser.add_argument("--roi-config", type=Path)
    args = parser.parse_args()
    if args.requalify:
        payload = requalify_session(args.output_dir, roi_config=args.roi_config)
    else:
        payload = session(args.output_dir, dry_run=args.dry_run, integrated=args.run_integrated)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
