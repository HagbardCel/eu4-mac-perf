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
import submission_multi_hypothesis as multi
import submission_border_validation as border_validation
import submission_validation as validation
from autonomous_runner import capture_scene, focus, mark, probe_rows, stop_process, summarize, warm_up
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
OBSERVER_PHASE_SCHEDULE = (
    ("a1", "reference", MODE_REFERENCE),
    ("b1", "candidate", MODE_CANDIDATE),
    ("a2", "reference", MODE_REFERENCE),
)
OBSERVER_PHASE_SECONDS = 10
OBSERVER_SETTLE_SECONDS = 3
BORDER_PHASE_SCHEDULE = (
    ("n1", "neutral", MODE_REFERENCE, True, False),
    ("a1", "reference", MODE_CANDIDATE, False, False),
    ("b1", "candidate", MODE_CANDIDATE, False, True),
    ("a2", "reference", MODE_CANDIDATE, False, False),
    ("b2", "candidate", MODE_CANDIDATE, False, True),
    ("a3", "reference", MODE_CANDIDATE, False, False),
    ("n2", "neutral", MODE_REFERENCE, True, False),
)
BORDER_PHASE_SECONDS = 10
BORDER_SETTLE_SECONDS = 3
EXPECTED_CANDIDATE_CAPABILITY_ID = 0
OBSERVER_CAPABILITY_ID = 1
MUTATING_CAPABILITY_ID = 2


def _validate_control_deltas(
    role: str,
    start: dict[str, int],
    end: dict[str, int],
    *,
    expected_capability_id: int,
    require_v1_pair_hits: bool = True,
) -> dict:
    tick_delta = end["control_ticks"] - start["control_ticks"]
    site_delta = end.get("site_entries", end["candidate_site_entries"]) - start.get(
        "site_entries", start["candidate_site_entries"]
    )
    candidate_site_delta = end["candidate_site_entries"] - start["candidate_site_entries"]
    hits_delta = end["eligible_pair_hits"] - start["eligible_pair_hits"]
    action_delta = end["candidate_effective_actions"] - start["candidate_effective_actions"]
    if end["requested_capability_id"] != expected_capability_id:
        return {
            "status": "failed",
            "reason": "requested_capability_id mismatch",
            "start": start,
            "end": end,
        }
    if end["advertised_capability_id"] != expected_capability_id:
        return {
            "status": "failed",
            "reason": "advertised_capability_id mismatch",
            "start": start,
            "end": end,
        }
    if role == "candidate" and expected_capability_id == 0:
        return {
            "status": "unsupported_candidate",
            "control_ticks_delta": tick_delta,
            "candidate_site_entries_delta": site_delta,
            "armed_candidate_site_entries_delta": candidate_site_delta,
            "eligible_pair_hits_delta": hits_delta,
            "effective_actions_delta": action_delta,
            "start": start,
            "end": end,
        }
    if role == "reference" and expected_capability_id == OBSERVER_CAPABILITY_ID:
        ok = (
            end["ack_mode"] == MODE_REFERENCE
            and tick_delta > 0
            and site_delta > 0
            and hits_delta == 0
            and action_delta == 0
        )
        status = "passed" if ok else "failed"
        reason = (
            None
            if ok
            else "observer reference requires mesh site traffic without pair hits or effective actions"
        )
    elif role == "candidate" and expected_capability_id == OBSERVER_CAPABILITY_ID:
        hits_ok = hits_delta > 0 if require_v1_pair_hits else True
        ok = (
            end["ack_mode"] == MODE_CANDIDATE
            and tick_delta > 0
            and candidate_site_delta > 0
            and hits_ok
            and action_delta == 0
        )
        status = "engagement_only" if ok and require_v1_pair_hits else ("passed" if ok else "failed")
        reason = None if ok else "observer candidate requires mesh site traffic without mutation"
    elif role == "candidate":
        ok = (
            end["ack_mode"] == MODE_CANDIDATE
            and site_delta > 0
            and hits_delta > 0
            and action_delta > 0
        )
        status = "passed" if ok else "failed"
        reason = None if ok else "mutating candidate requires site entries, pair hits, and effective actions"
    else:
        ok = end["ack_mode"] == MODE_REFERENCE and tick_delta > 0 and action_delta == 0
        status = "passed" if ok else "failed"
        reason = None if ok else "reference phase requires control ticks without effective actions"
    renderbuckets_delta = end.get("renderbuckets_invocations", 0) - start.get("renderbuckets_invocations", 0)
    return {
        "status": status,
        "reason": reason,
        "control_ticks_delta": tick_delta,
        "candidate_site_entries_delta": site_delta,
        "armed_candidate_site_entries_delta": candidate_site_delta,
        "renderbuckets_invocations_delta": renderbuckets_delta,
        "eligible_pair_hits_delta": hits_delta,
        "effective_actions_delta": action_delta,
        "hook_attempts_delta": tick_delta,
        "start": start,
        "end": end,
    }


def _count_paused_swaps(run_dir: Path, anchor: dict, start_ns: int, end_ns: int) -> int:
    rows = [r for r in probe_rows(run_dir / "auto-probe.csv", anchor) if start_ns <= r["monotonic_ns"] < end_ns]
    return sum(int(r.get("paused_swaps", 0)) for r in rows)


def _run_phase(
    game: subprocess.Popen,
    run_dir: Path,
    events: Path,
    tail,
    submission: control.SubmissionControl,
    name: str,
    role: str,
    mode: int,
    duration: float,
    anchor: dict,
    *,
    expected_capability_id: int,
    settle_seconds: float = SETTLE_SECONDS,
    multi_hypothesis: bool = False,
    border_minimal: bool = False,
    border_mutate: bool = False,
    border_experiment: bool = False,
) -> dict:
    submission.set_border_flags(minimal=border_minimal, mutate=border_mutate)
    generation = submission.request_mode(mode)
    ack = submission.wait_ack(generation)
    if ack["ack_mode"] != mode:
        raise base.BenchmarkError(f"{name}: ack mode {ack['ack_mode']} != requested {mode}")
    settle_deadline = time.monotonic() + settle_seconds
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
    obs_arm_gen: int | None = None
    if mode == MODE_CANDIDATE:
        obs_arm_gen = submission.request_observation_arm()
        submission.wait_observation_ack(obs_arm_gen, expected_state=control.OBS_ACK_ARMED)
        counter_start = submission.snapshot()
    else:
        counter_start = submission.snapshot()
    measurement_start = mark(events, "measurement_start", phase=name, mode=mode)
    measurement_deadline = time.monotonic() + duration
    while time.monotonic() < measurement_deadline:
        if game.poll() is not None:
            raise base.BenchmarkError(f"EU IV exited during {name} measurement")
        if not focus("interior", game.pid):
            raise base.BenchmarkError(f"EU IV lost focus during {name} measurement")
        tail.poll()
        time.sleep(min(1.0, max(0.0, measurement_deadline - time.monotonic())))
    if mode == MODE_CANDIDATE and obs_arm_gen is not None:
        freeze_gen = submission.request_observation_freeze()
        submission.wait_observation_ack(freeze_gen, expected_state=control.OBS_ACK_FROZEN)
    measurement_end = mark(events, "measurement_end", phase=name, mode=mode)
    counter_end = submission.snapshot_frozen_candidate_bank() if mode == MODE_CANDIDATE else submission.snapshot()
    phase_payload = {
        "name": name,
        "role": role,
        "mode": mode,
        "run_dir": str(run_dir),
        "start_ns": measurement_start["monotonic_ns"],
        "end_ns": measurement_end["monotonic_ns"],
        "screenshot": str(screenshot),
    }
    if border_experiment and expected_capability_id == MUTATING_CAPABILITY_ID:
        phase_payload["control_validation"] = {
            "status": "skipped",
            "reason": "border experiment uses border_validation, not mesh pair counters",
            "start": counter_start,
            "end": counter_end,
        }
        phase_payload["border_validation"] = border_validation.border_phase_validation(
            role,
            counter_start,
            counter_end,
            border_mutate=border_mutate,
            border_minimal=border_minimal,
        )
        phase_payload["border_minimal"] = border_minimal
        phase_payload["border_mutate"] = border_mutate
    else:
        phase_payload["control_validation"] = _validate_control_deltas(
            role,
            counter_start,
            counter_end,
            expected_capability_id=expected_capability_id,
            require_v1_pair_hits=not multi_hypothesis and expected_capability_id != MUTATING_CAPABILITY_ID,
        )
    if multi_hypothesis and role == "candidate":
        wall_s = max(1e-6, (measurement_end["monotonic_ns"] - measurement_start["monotonic_ns"]) / 1e9)
        swaps = _count_paused_swaps(run_dir, anchor, measurement_start["monotonic_ns"], measurement_end["monotonic_ns"])
        cv = phase_payload["control_validation"]
        phase_payload["multi_hypothesis"] = multi.evaluate_b_phase(
            counter_end,
            health_start=counter_start,
            health_end=counter_end,
            measurement_paused_swaps=swaps,
            measurement_wall_seconds=wall_s,
        )
        control_ok = cv.get("status") == "passed"
        mh_ok = phase_payload["multi_hypothesis"]["harness_ok"]
        phase_payload["multi_hypothesis"]["phase_ok"] = control_ok and mh_ok
    return phase_payload


def _min_metric_samples_for_phase(phase: dict) -> int:
    duration_s = max(1.0, (phase["end_ns"] - phase["start_ns"]) / 1_000_000_000)
    # Auto-probe rows are ~1 Hz during measurement; require most of the window.
    return max(5, int(duration_s * 0.75))


def _attach_phase_summaries(
    phases: list[dict],
    run_dir: Path,
    anchor: dict,
    pid: int,
    tail: auto.PowerTail,
) -> None:
    for phase in phases:
        min_samples = _min_metric_samples_for_phase(phase)
        phase["summary"] = summarize(
            run_dir,
            [phase],
            anchor,
            pid,
            tail.samples,
            swap_warmup_ns=0,
            min_swap_samples=min_samples,
            min_power_samples=min_samples,
            write_shared_summary=False,
        )


def run_border_multidraw_experiment(output_root: Path) -> Path:
    control.build_border_multidraw()
    auto.build()
    evidence = auto.preflight()
    base.running_game_pid("idle")
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-submission-experiment"
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict = {
        "experiment": "submission_border_multidraw_v1",
        "status": "starting",
        "instrumentation": "libeu4_auto_probe.dylib + libeu4_submission_border_multidraw.dylib",
        "expected_candidate_capability_id": MUTATING_CAPABILITY_ID,
        "phase_schedule": [
            {"name": n, "role": r, "mode": m, "border_minimal": mn, "border_mutate": mu}
            for n, r, m, mn, mu in BORDER_PHASE_SCHEDULE
        ],
        "phase_seconds": BORDER_PHASE_SECONDS,
        "repository_scene_reference": str(validation.SCENE_REFERENCE_MANIFEST.relative_to(ROOT)),
        "border_api_runtime_assert": "recorded_in_counters",
        "pr_b_mutation_go": "NO-GO_interpose",
        "mutation_enabled": False,
        "venice_observer_run_authorized": True,
        "render_mutation_authorized": False,
        "note": "Observer-only Venice ROI run authorized; GL/render mutation remains unauthorized.",
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    events = run_dir / "events.jsonl"
    anchor = mark(events, "clock_anchor")
    game_log = base.USER_DATA / "logs/game.log"
    old_log = game_log.read_bytes() if game_log.is_file() else b""
    game = pm = None
    installed = False
    tail = None
    control_path = run_dir / "submission_control.bin"
    control_path.write_bytes(bytes(control.CONTROL_SIZE))
    error: str | None = None
    with FixtureManager(output_root, base.USER_DATA, auto.FIXTURE, evidence["fixture"]["save_sha256"]) as fixture:
        fixture.recover()
        working = fixture.install()
        installed = True
        manifest["working_save"] = str(working)
        pm_path = run_dir / "powermetrics.pliststream"
        submission = control.SubmissionControl(control_path, candidate_capability_id=MUTATING_CAPABILITY_ID)
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
                env = control.dylib_env(control_path, auto.PROBE, submission_dylib=control.LIBRARY_BORDER)
                env.pop("EU4_SUBMISSION_TEST_STUB_HOOK", None)
                manifest["test_stub_hook_enabled"] = False
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
                for name, role, mode, border_minimal, border_mutate in BORDER_PHASE_SCHEDULE:
                    phases.append(
                        _run_phase(
                            game,
                            run_dir,
                            events,
                            tail,
                            submission,
                            name,
                            role,
                            mode,
                            BORDER_PHASE_SECONDS,
                            anchor,
                            expected_capability_id=MUTATING_CAPABILITY_ID,
                            settle_seconds=BORDER_SETTLE_SECONDS,
                            border_minimal=border_minimal,
                            border_mutate=border_mutate,
                            border_experiment=True,
                        ),
                    )
                stop_process(pm, 5)
                pm = None
                _attach_phase_summaries(phases, run_dir, anchor, game.pid, tail)
                manifest["phases"] = phases
                manifest["border_roi_primary_phase"] = border_validation.BORDER_ROI_PRIMARY_PHASE
                manifest["border_roi_validation_phases"] = list(border_validation.ARMED_ROI_PHASE_NAMES)
                manifest["border_legacy_gl_engagement_gate"] = border_validation.border_experiment_gate(phases)
                roi_phase_gates: dict[str, dict] = {}
                roi_failures: list[str] = []
                primary_snapshot: dict | None = None
                for phase in phases:
                    name = str(phase.get("name", ""))
                    if name not in border_validation.ARMED_ROI_PHASE_NAMES:
                        continue
                    cv = phase.get("control_validation") or {}
                    end = cv.get("end") or {}
                    gates = border_validation.border_phase_roi_authoritative_gates(end)
                    roi_phase_gates[name] = gates
                    if gates["status"] != "passed":
                        roi_failures.append(f"{name}: {gates.get('reason')}")
                    if name == border_validation.BORDER_ROI_PRIMARY_PHASE:
                        primary_snapshot = end
                manifest["border_roi_phase_gates"] = roi_phase_gates
                if primary_snapshot is not None:
                    manifest["border_roi_primary_snapshot"] = primary_snapshot
                if roi_failures:
                    raise base.BenchmarkError(
                        "border_roi_authoritative_gates failed: " + "; ".join(roi_failures)
                    )
                manifest["status"] = "complete"
        except (base.BenchmarkError, OSError, subprocess.SubprocessError) as exc:
            error = str(exc)
            manifest["status"] = "incomplete"
            manifest["error"] = error
            raise
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


def run_experiment(
    output_root: Path,
    *,
    expected_capability_id: int = EXPECTED_CANDIDATE_CAPABILITY_ID,
    engagement_smoke: bool = False,
    multi_hypothesis_smoke: bool = False,
) -> Path:
    expected_capability = int(expected_capability_id)
    observer_run = engagement_smoke or multi_hypothesis_smoke or expected_capability == OBSERVER_CAPABILITY_ID
    if not observer_run:
        raise base.BenchmarkError(
            "No mutating submission candidate is compiled; use --engagement-smoke for capability 1 observer."
        )
    if expected_capability not in (0, OBSERVER_CAPABILITY_ID):
        raise base.BenchmarkError("Only capability 1 observer runs are supported by the current dylib build.")
    expected_capability = OBSERVER_CAPABILITY_ID
    auto.build()
    control.build()
    evidence = auto.preflight()
    base.running_game_pid("idle")
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-submission-experiment"
    run_dir.mkdir(parents=True, exist_ok=False)
    phase_schedule = OBSERVER_PHASE_SCHEDULE if observer_run else PHASE_SCHEDULE
    phase_seconds = OBSERVER_PHASE_SECONDS if observer_run else PHASE_SECONDS
    settle_seconds = OBSERVER_SETTLE_SECONDS if observer_run else SETTLE_SECONDS
    experiment_kind = "submission_optimization_ababa_v1"
    if multi_hypothesis_smoke:
        experiment_kind = "submission_observer_multi_hypothesis_v2"
    elif observer_run:
        experiment_kind = "submission_observer_smoke_v1"
    manifest: dict = {
        "experiment": experiment_kind,
        "status": "starting",
        "instrumentation": "libeu4_auto_probe.dylib + libeu4_submission_experiment.dylib",
        "expected_candidate_capability_id": expected_capability,
        "phase_schedule": [{"name": n, "role": r, "mode": m} for n, r, m in phase_schedule],
        "phase_seconds": phase_seconds,
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
    tail = None
    control_path = run_dir / "submission_control.bin"
    control_path.write_bytes(bytes(control.CONTROL_SIZE))
    error: str | None = None
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
                env = control.dylib_env(control_path, auto.PROBE)
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
                for name, role, mode in phase_schedule:
                    phases.append(
                        _run_phase(
                            game,
                            run_dir,
                            events,
                            tail,
                            submission,
                            name,
                            role,
                            mode,
                            phase_seconds,
                            anchor,
                            expected_capability_id=expected_capability,
                            settle_seconds=settle_seconds,
                            multi_hypothesis=multi_hypothesis_smoke,
                        ),
                    )
                stop_process(pm, 5)
                pm = None
                drain = tail.finish()
                manifest["power_tail_finish"] = drain
                if drain.get("status") == "incomplete":
                    raise base.BenchmarkError(f"Powermetrics tail incomplete: {drain}")
                _attach_phase_summaries(phases, run_dir, anchor, game.pid, tail)
                manifest["phases"] = phases
                if multi_hypothesis_smoke:
                    manifest["observer_scene_gate"] = validation.observer_scene_gate(phases)
                    manifest["multi_hypothesis_gate"] = multi.multi_hypothesis_smoke_gate(
                        phases, scene_gate=manifest["observer_scene_gate"]
                    )
                    manifest["v3_reference_health_gate"] = manifest["multi_hypothesis_gate"]["reference_health"]
                    manifest["engagement_gate"] = {
                        "status": manifest["multi_hypothesis_gate"]["status"],
                        "pareto_eligible": False,
                        "multi_hypothesis": True,
                    }
                    manifest["observer_gate"] = {
                        "status": "skipped",
                        "reason": "v3 uses multi_hypothesis_gate + observer_scene_gate",
                    }
                elif observer_run:
                    manifest["observer_gate"] = validation.observer_engagement_gate(
                        phases, expected_capability_id=expected_capability
                    )
                    manifest["engagement_gate"] = control.engagement_smoke_gate(
                        phases, expected_capability_id=expected_capability
                    )
                    manifest["pareto_gate"] = {
                        "status": "skipped",
                        "reason": "observer capability; Pareto not applicable",
                    }
                else:
                    manifest["pareto_gate"] = validation.bracket_normalized_ababa_gate(
                        phases,
                        expected_candidate_capability_id=expected_capability,
                    )
                validation_path = run_dir / "validation.json"
                validation_path.write_text(
                    json.dumps(
                        {
                            "pareto_gate": manifest.get("pareto_gate"),
                            "engagement_gate": manifest.get("engagement_gate"),
                            "observer_gate": manifest.get("observer_gate"),
                            "multi_hypothesis_gate": manifest.get("multi_hypothesis_gate"),
                            "observer_scene_gate": manifest.get("observer_scene_gate"),
                            "expected_candidate_capability_id": expected_capability,
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                manifest["status"] = "complete"
        except (base.BenchmarkError, OSError, subprocess.SubprocessError) as exc:
            error = str(exc)
            manifest["status"] = "incomplete"
            manifest["error"] = error
            raise
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
    parser.add_argument(
        "--engagement-smoke",
        action="store_true",
        help="capability 1 observer run (no Pareto); short validation semantics",
    )
    parser.add_argument(
        "--multi-hypothesis-smoke",
        action="store_true",
        help="protocol v3 multi-hypothesis observer A-B-A (ARM/FREEZE on B1)",
    )
    parser.add_argument(
        "--border-multidraw-ababa",
        action="store_true",
        help="capability 2 border multidraw N-A-B-A-B-A-N (profiler-off)",
    )
    args = parser.parse_args()
    mode_count = sum(
        1 for flag in (args.engagement_smoke, args.multi_hypothesis_smoke, args.border_multidraw_ababa) if flag
    )
    if mode_count != 1:
        print(
            "Error: pass exactly one of --engagement-smoke, --multi-hypothesis-smoke, --border-multidraw-ababa.",
            file=sys.stderr,
        )
        return 1
    try:
        if args.border_multidraw_ababa:
            run_dir = run_border_multidraw_experiment(Path(args.output).expanduser().resolve())
        else:
            run_dir = run_experiment(
                Path(args.output).expanduser().resolve(),
                expected_capability_id=OBSERVER_CAPABILITY_ID,
                engagement_smoke=args.engagement_smoke,
                multi_hypothesis_smoke=args.multi_hypothesis_smoke,
            )
        print(run_dir)
    except (base.BenchmarkError, OSError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
