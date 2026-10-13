"""Process CPU reconciliation against powermetrics plist samples."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import autonomous_runner as auto

CPU_ACCOUNTING_CONTRACT_V1 = "r0_cpu_finalize_v1"
LEGACY_OCT12_RUN_DIR = "20261012T210806Z-r0-measurement"
LEGACY_OCT12_ACQUISITION_COMMIT_PREFIX = "a9048e1"
LIVE_SIDECAR_SCHEMA = "r0_epoch_a_process_cpu_samples_v1"
LIVE_OBSERVATION_ORIGIN = "live_finalize_v1"
EPOCH_A_PROCESS_CPU_SIDECAR_NAME = "epoch_a_process_cpu_samples.json"
ACCEPTABLE_TAIL_STATUSES = frozenset({"clean", "appended"})
_ARITHMETIC_REL_TOL = 1e-9
_ARITHMETIC_ABS_TOL = 1e-6


def list_process_cpu_observations(
    samples: list[dict],
    pid: int,
    anchor: dict[str, Any],
    start_ns: int,
    end_ns: int,
) -> list[dict[str, Any]]:
    return [
        {"monotonic_ns": when, "cputime_ms_per_s": value}
        for when, value in auto.cpu_samples_between(samples, pid, anchor, start_ns, end_ns)
    ]


def mean_from_observations(observations: list[dict[str, Any]]) -> tuple[float | None, int]:
    if not observations:
        return None, 0
    values = [float(item["cputime_ms_per_s"]) for item in observations]
    return float(sum(values) / len(values)), len(values)


def mean_process_cpu_ms_per_s(
    samples: list[dict],
    pid: int,
    anchor: dict[str, Any],
    start_ns: int,
    end_ns: int,
) -> tuple[float | None, int]:
    return mean_from_observations(list_process_cpu_observations(samples, pid, anchor, start_ns, end_ns))


def tail_finalization_acceptable(finish_result: dict[str, Any] | None) -> bool:
    if not isinstance(finish_result, dict):
        return False
    status = finish_result.get("status")
    if status not in ACCEPTABLE_TAIL_STATUSES:
        return False
    if int(finish_result.get("trailing_partial_bytes") or 0) > 0:
        return False
    return True


def classify_powermetrics_producer_before_stop(pm: subprocess.Popen | None) -> dict[str, Any]:
    if pm is None:
        return {
            "state_before_stop": "not_started",
            "exit_code_before_stop": None,
            "stop_completed": False,
        }
    exit_code = pm.poll()
    if exit_code is not None:
        return {
            "state_before_stop": "unexpected_early_exit",
            "exit_code_before_stop": int(exit_code),
            "stop_completed": False,
        }
    return {
        "state_before_stop": "running",
        "exit_code_before_stop": None,
        "stop_completed": False,
    }


def shutdown_powermetrics(pm: subprocess.Popen | None, timeout: float = 5.0) -> dict[str, Any]:
    """Capture liveness, perform a single intentional stop, record stop_completed."""
    state = classify_powermetrics_producer_before_stop(pm)
    if pm is None:
        return state
    try:
        auto.stop_process(pm, timeout)
        state["stop_completed"] = True
    except (OSError, subprocess.SubprocessError) as exc:
        state["stop_completed"] = False
        state["stop_error"] = str(exc)
    return state


def producer_shutdown_acceptable(producer: dict[str, Any] | None) -> bool:
    if not isinstance(producer, dict):
        return False
    if producer.get("state_before_stop") != "running":
        return False
    return producer.get("stop_completed") is True


def _sha256_file(path: Path) -> tuple[str | None, int | None]:
    if not path.is_file():
        return None, None
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def build_epoch_a_process_cpu_sidecar(
    *,
    observations: list[dict[str, Any]],
    process_mean: float | None,
    thread_report: dict[str, Any],
    clock_anchor: dict[str, Any],
    pid: int,
    reconciliation_residual_ms_per_s: float | None,
    reconcile_ok: bool,
    powermetrics_stream_path: Path | None,
    run_dir: str | None = None,
) -> dict[str, Any]:
    stream_sha, stream_bytes = (None, None)
    if powermetrics_stream_path is not None:
        stream_sha, stream_bytes = _sha256_file(powermetrics_stream_path)
    return {
        "schema": LIVE_SIDECAR_SCHEMA,
        "observation_origin": LIVE_OBSERVATION_ORIGIN,
        "run_dir": run_dir,
        "clock_anchor": clock_anchor,
        "game_pid": pid,
        "selection": {
            "method": "benchmark.cpu_reconcile.list_process_cpu_observations",
            "start_monotonic_ns": int(thread_report["window_start_ns"]),
            "end_monotonic_ns": int(thread_report["window_end_ns"]),
            "source": "epoch_a_thread_cpu.json window_start_ns/window_end_ns",
        },
        "sample_count": len(observations),
        "samples": observations,
        "recomputed_mean_cputime_ms_per_s": process_mean,
        "manifest_process_cpu_ms_per_s": process_mean,
        "manifest_thread_cpu_ms_per_s": thread_report.get("thread_cpu_ms_per_s"),
        "manifest_reconciliation_residual_ms_per_s": reconciliation_residual_ms_per_s,
        "reconcile_ok": reconcile_ok,
        "powermetrics_pliststream_sha256": stream_sha,
        "powermetrics_pliststream_bytes": stream_bytes,
    }


def write_epoch_a_process_cpu_sidecar_atomic(output_dir: Path, sidecar: dict[str, Any]) -> Path:
    path = output_dir / EPOCH_A_PROCESS_CPU_SIDECAR_NAME
    temp = path.with_suffix(".tmp.json")
    payload = json.dumps(sidecar, indent=2) + "\n"
    temp.write_text(payload)
    temp.replace(path)
    return path


def reconcile_cpu(
    thread_report: dict[str, Any],
    *,
    powermetrics_samples: list[dict],
    clock_anchor: dict[str, Any],
    pid: int,
    reconcile_tolerance_ms_per_s: float,
    tail_finalization: dict[str, Any] | None = None,
    powermetrics_producer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge thread poll_window report with finalized process CPU from powermetrics."""
    out = dict(thread_report)
    out["cpu_reconciliation_phase"] = "finalized"
    out["process_cpu_sidecar_persisted"] = False
    out["reconcile_ok"] = False

    if powermetrics_producer is not None:
        out["powermetrics_producer"] = dict(powermetrics_producer)
    if tail_finalization is not None:
        out["tail_finalization_status"] = tail_finalization
        out["tail_trailing_partial_bytes"] = int(tail_finalization.get("trailing_partial_bytes") or 0)

    if not producer_shutdown_acceptable(powermetrics_producer):
        return out
    if not tail_finalization_acceptable(tail_finalization):
        return out

    start_ns = int(thread_report["window_start_ns"])
    end_ns = int(thread_report["window_end_ns"])
    observations = list_process_cpu_observations(powermetrics_samples, pid, clock_anchor, start_ns, end_ns)
    process_cpu, sample_count = mean_from_observations(observations)
    out["process_cpu_sample_count"] = sample_count
    out["_finalize_observations"] = observations  # stripped before persist
    if process_cpu is None:
        out["process_cpu_ms_per_s"] = None
        out["reconciliation_residual_ms_per_s"] = None
        return out

    thread_cpu = float(thread_report["thread_cpu_ms_per_s"])
    residual = process_cpu - thread_cpu
    out["process_cpu_ms_per_s"] = process_cpu
    out["reconciliation_residual_ms_per_s"] = residual
    if abs(residual) <= reconcile_tolerance_ms_per_s:
        out["reconcile_ok"] = True
    return out


def persist_finalized_cpu_with_sidecar(
    output_dir: Path,
    manifest: dict[str, Any],
    provisional_report: dict[str, Any],
    *,
    tail_samples: list[dict],
    tail_finalization: dict[str, Any],
    clock_anchor: dict[str, Any],
    pid: int,
    powermetrics_producer: dict[str, Any],
    pm_stream_path: Path | None = None,
) -> dict[str, Any]:
    tolerance = float(manifest.get("cpu_reconcile_tolerance_ms_per_s", 50.0))
    finalized = reconcile_cpu(
        provisional_report,
        powermetrics_samples=tail_samples,
        clock_anchor=clock_anchor,
        pid=pid,
        reconcile_tolerance_ms_per_s=tolerance,
        tail_finalization=tail_finalization,
        powermetrics_producer=powermetrics_producer,
    )
    observations = finalized.pop("_finalize_observations", [])
    process_mean = finalized.get("process_cpu_ms_per_s")
    residual = finalized.get("reconciliation_residual_ms_per_s")
    reconcile_ok = finalized.get("reconcile_ok") is True

    sidecar = build_epoch_a_process_cpu_sidecar(
        observations=observations,
        process_mean=process_mean,
        thread_report=provisional_report,
        clock_anchor=clock_anchor,
        pid=pid,
        reconciliation_residual_ms_per_s=residual,
        reconcile_ok=reconcile_ok,
        powermetrics_stream_path=pm_stream_path,
        run_dir=str(output_dir),
    )

    if reconcile_ok:
        try:
            sidecar_path = write_epoch_a_process_cpu_sidecar_atomic(output_dir, sidecar)
            manifest["epoch_a_process_cpu_samples_path"] = sidecar_path.name
            sidecar_sha, _ = _sha256_file(sidecar_path)
            if sidecar_sha is None:
                raise OSError("sidecar missing after write")
            finalized["process_cpu_sidecar_persisted"] = True
            finalized["process_cpu_sidecar_sha256"] = sidecar_sha
        except OSError:
            finalized["reconcile_ok"] = False
            finalized["process_cpu_sidecar_persisted"] = False
            finalized.pop("process_cpu_sidecar_sha256", None)
    else:
        finalized["process_cpu_sidecar_persisted"] = False
        finalized.pop("process_cpu_sidecar_sha256", None)

    return finalized


def _path_resolves_within_dir(path: Path, output_dir: Path) -> bool:
    try:
        resolved = path.resolve()
        base = output_dir.resolve()
        resolved.relative_to(base)
    except (OSError, ValueError):
        return False
    return True


def _sidecar_path_for_verification(output_dir: Path) -> Path:
    return output_dir / EPOCH_A_PROCESS_CPU_SIDECAR_NAME


def _manifest_sidecar_path_valid(manifest: dict[str, Any]) -> bool:
    ref = manifest.get("epoch_a_process_cpu_samples_path")
    if ref is None:
        return False
    if ref != EPOCH_A_PROCESS_CPU_SIDECAR_NAME:
        return False
    if ".." in Path(ref).parts:
        return False
    return True


def _reconcile_tolerance_ms_per_s(manifest: dict[str, Any]) -> float | None:
    raw = manifest.get("cpu_reconcile_tolerance_ms_per_s", 50.0)
    if isinstance(raw, bool):
        return None
    if type(raw) is int:
        value = float(raw)
    elif type(raw) is float:
        value = raw
    else:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def verify_epoch_a_sidecar_integrity(
    cpu: dict[str, Any],
    output_dir: Path,
    manifest: dict[str, Any],
) -> bool:
    if not _manifest_sidecar_path_valid(manifest):
        return False
    if cpu.get("process_cpu_sidecar_persisted") is not True:
        return False
    recorded_sha = cpu.get("process_cpu_sidecar_sha256")
    if not isinstance(recorded_sha, str) or len(recorded_sha) != 64:
        return False

    sidecar_path = _sidecar_path_for_verification(output_dir)
    if sidecar_path.is_symlink() and not _path_resolves_within_dir(sidecar_path, output_dir):
        return False
    if not _path_resolves_within_dir(sidecar_path, output_dir):
        return False
    if not sidecar_path.is_file():
        return False

    file_sha, _ = _sha256_file(sidecar_path)
    if file_sha != recorded_sha:
        return False

    try:
        sidecar = json.loads(sidecar_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False

    if not isinstance(sidecar, dict):
        return False

    if sidecar.get("schema") != LIVE_SIDECAR_SCHEMA:
        return False
    if sidecar.get("observation_origin") != LIVE_OBSERVATION_ORIGIN:
        return False

    pid = cpu.get("pid")
    if type(pid) is not int or sidecar.get("game_pid") != pid:
        return False

    window_start = cpu.get("window_start_ns")
    window_end = cpu.get("window_end_ns")
    if type(window_start) is not int or type(window_end) is not int:
        return False
    if window_start >= window_end:
        return False

    selection = sidecar.get("selection")
    if not isinstance(selection, dict):
        return False
    if selection.get("start_monotonic_ns") != window_start:
        return False
    if selection.get("end_monotonic_ns") != window_end:
        return False

    tolerance = _reconcile_tolerance_ms_per_s(manifest)
    if tolerance is None:
        return False

    process_mean = cpu.get("process_cpu_ms_per_s")
    thread_cpu = cpu.get("thread_cpu_ms_per_s")
    residual = cpu.get("reconciliation_residual_ms_per_s")
    if not all(
        isinstance(value, (int, float)) and math.isfinite(float(value))
        for value in (process_mean, thread_cpu, residual)
    ):
        return False

    sample_count = cpu.get("process_cpu_sample_count")
    if type(sample_count) is not int or sample_count < 1:
        return False
    if sidecar.get("sample_count") != sample_count:
        return False
    samples = sidecar.get("samples")
    if not isinstance(samples, list) or len(samples) != sample_count:
        return False

    ws = window_start
    we = window_end
    values: list[float] = []
    for item in samples:
        if not isinstance(item, dict):
            return False
        when = item.get("monotonic_ns")
        rate = item.get("cputime_ms_per_s")
        if not isinstance(when, int) or when < ws or when > we:
            return False
        if not isinstance(rate, (int, float)) or not math.isfinite(float(rate)) or float(rate) <= 0:
            return False
        values.append(float(rate))

    if not values:
        return False
    recomputed_mean = sum(values) / len(values)
    if not math.isclose(
        recomputed_mean,
        float(process_mean),
        rel_tol=_ARITHMETIC_REL_TOL,
        abs_tol=_ARITHMETIC_ABS_TOL,
    ):
        return False
    sidecar_mean = sidecar.get("recomputed_mean_cputime_ms_per_s")
    if not isinstance(sidecar_mean, (int, float)) or not math.isfinite(float(sidecar_mean)):
        return False
    if not math.isclose(
        float(sidecar_mean),
        float(process_mean),
        rel_tol=_ARITHMETIC_REL_TOL,
        abs_tol=_ARITHMETIC_ABS_TOL,
    ):
        return False

    if sidecar.get("reconcile_ok") is not cpu.get("reconcile_ok"):
        return False
    sidecar_residual = sidecar.get("manifest_reconciliation_residual_ms_per_s")
    if not isinstance(sidecar_residual, (int, float)) or not math.isfinite(float(sidecar_residual)):
        return False
    if not math.isclose(
        float(sidecar_residual),
        float(residual),
        rel_tol=_ARITHMETIC_REL_TOL,
        abs_tol=_ARITHMETIC_ABS_TOL,
    ):
        return False

    sidecar_process = sidecar.get("manifest_process_cpu_ms_per_s")
    sidecar_thread = sidecar.get("manifest_thread_cpu_ms_per_s")
    if not isinstance(sidecar_process, (int, float)) or not math.isfinite(float(sidecar_process)):
        return False
    if not isinstance(sidecar_thread, (int, float)) or not math.isfinite(float(sidecar_thread)):
        return False
    if not math.isclose(
        float(sidecar_process),
        float(process_mean),
        rel_tol=_ARITHMETIC_REL_TOL,
        abs_tol=_ARITHMETIC_ABS_TOL,
    ):
        return False
    if not math.isclose(
        float(sidecar_thread),
        float(thread_cpu),
        rel_tol=_ARITHMETIC_REL_TOL,
        abs_tol=_ARITHMETIC_ABS_TOL,
    ):
        return False

    expected_residual = float(process_mean) - float(thread_cpu)
    if not math.isclose(
        expected_residual,
        float(residual),
        rel_tol=_ARITHMETIC_REL_TOL,
        abs_tol=_ARITHMETIC_ABS_TOL,
    ):
        return False
    if cpu.get("reconcile_ok") is True and abs(expected_residual) > tolerance:
        return False

    return True


def _oct12_acquisition_cpu_fingerprint(manifest: dict[str, Any]) -> bool:
    cpu = manifest.get("epoch_a_thread_cpu") or {}
    return (
        cpu.get("window_start_ns") == 378384359061916
        and cpu.get("window_end_ns") == 378414600898250
        and cpu.get("main_thread_id") == 6650541
        and cpu.get("pid") == 98200
        and cpu.get("reconcile_ok") is True
    )


def legacy_cpu_gate_eligible(manifest: dict[str, Any], output_dir: Any | None) -> bool:
    if manifest.get("cpu_accounting_contract"):
        return False
    name = getattr(output_dir, "name", None) if output_dir is not None else None
    if name == LEGACY_OCT12_RUN_DIR:
        commit = manifest.get("acquisition_commit")
        if not commit and isinstance(manifest.get("measurement_source"), dict):
            commit = manifest["measurement_source"].get("git_head")
        if commit and not str(commit).startswith(LEGACY_OCT12_ACQUISITION_COMMIT_PREFIX):
            return False
        return True
    return _oct12_acquisition_cpu_fingerprint(manifest)


def cpu_reconcile_gate_passes(
    cpu: dict[str, Any],
    manifest: dict[str, Any],
    output_dir: Any | None,
) -> bool:
    contract = manifest.get("cpu_accounting_contract")
    if contract == CPU_ACCOUNTING_CONTRACT_V1:
        if cpu.get("cpu_reconciliation_phase") != "finalized":
            return False
        if cpu.get("reconcile_ok") is not True:
            return False
        if cpu.get("process_cpu_sidecar_persisted") is not True:
            return False
        producer = cpu.get("powermetrics_producer")
        if not producer_shutdown_acceptable(producer if isinstance(producer, dict) else None):
            return False
        tail_status = cpu.get("tail_finalization_status")
        if not tail_finalization_acceptable(tail_status if isinstance(tail_status, dict) else None):
            return False
        if int(cpu.get("tail_trailing_partial_bytes") or 0) > 0:
            return False
        if not cpu.get("process_cpu_sample_count"):
            return False
        if output_dir is None:
            return False
        return verify_epoch_a_sidecar_integrity(cpu, Path(output_dir), manifest)
    if legacy_cpu_gate_eligible(manifest, output_dir):
        return cpu.get("reconcile_ok") is True
    return False


def retrospective_means_from_sidecar(sidecar: dict[str, Any]) -> dict[str, Any]:
    samples = sidecar.get("samples") or []
    if not samples:
        raise ValueError("process CPU sidecar has no samples")
    all_values = [float(item["cputime_ms_per_s"]) for item in samples]
    acquisition = [
        float(item["cputime_ms_per_s"])
        for item in samples
        if item.get("in_acquisition_subset")
    ]
    retrospective_mean = sum(all_values) / len(all_values)
    acquisition_mean = sum(acquisition) / len(acquisition) if acquisition else None
    return {
        "retrospective_sample_count": len(all_values),
        "acquisition_sample_count": len(acquisition),
        "retrospective_mean_cputime_ms_per_s": retrospective_mean,
        "acquisition_mean_cputime_ms_per_s": acquisition_mean,
    }
