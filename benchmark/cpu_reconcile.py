"""Process CPU reconciliation against powermetrics plist samples."""

from __future__ import annotations

from typing import Any

import autonomous_runner as auto

CPU_ACCOUNTING_CONTRACT_V1 = "r0_cpu_finalize_v1"
LEGACY_OCT12_RUN_DIR = "20261012T210806Z-r0-measurement"
LEGACY_OCT12_ACQUISITION_COMMIT_PREFIX = "a9048e1"


def mean_process_cpu_ms_per_s(
    samples: list[dict],
    pid: int,
    anchor: dict[str, Any],
    start_ns: int,
    end_ns: int,
) -> tuple[float | None, int]:
    values = auto.cpu_samples_between(samples, pid, anchor, start_ns, end_ns)
    if not values:
        return None, 0
    return float(sum(value for _, value in values) / len(values)), len(values)


def tail_finalization_acceptable(finish_result: dict[str, Any] | None) -> bool:
    if finish_result is None:
        return True
    if finish_result.get("status") == "incomplete":
        return False
    if int(finish_result.get("trailing_partial_bytes") or 0) > 0:
        return False
    return True


def reconcile_cpu(
    thread_report: dict[str, Any],
    *,
    powermetrics_samples: list[dict],
    clock_anchor: dict[str, Any],
    pid: int,
    reconcile_tolerance_ms_per_s: float,
    tail_finalization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge thread poll_window report with finalized process CPU from powermetrics."""
    out = dict(thread_report)
    out["cpu_reconciliation_phase"] = "finalized"
    if tail_finalization is not None:
        out["tail_finalization_status"] = tail_finalization
        out["tail_trailing_partial_bytes"] = int(tail_finalization.get("trailing_partial_bytes") or 0)

    if not tail_finalization_acceptable(tail_finalization):
        out["reconcile_ok"] = False
        return out

    start_ns = int(thread_report["window_start_ns"])
    end_ns = int(thread_report["window_end_ns"])
    process_cpu, sample_count = mean_process_cpu_ms_per_s(
        powermetrics_samples, pid, clock_anchor, start_ns, end_ns
    )
    out["process_cpu_sample_count"] = sample_count
    if process_cpu is None:
        out["process_cpu_ms_per_s"] = None
        out["reconciliation_residual_ms_per_s"] = None
        out["reconcile_ok"] = False
        return out

    thread_cpu = float(thread_report["thread_cpu_ms_per_s"])
    residual = process_cpu - thread_cpu
    out["process_cpu_ms_per_s"] = process_cpu
    out["reconciliation_residual_ms_per_s"] = residual
    out["reconcile_ok"] = abs(residual) <= reconcile_tolerance_ms_per_s
    return out


def legacy_cpu_gate_eligible(manifest: dict[str, Any], output_dir: Any | None) -> bool:
    if output_dir is None:
        return False
    name = getattr(output_dir, "name", None)
    if name != LEGACY_OCT12_RUN_DIR:
        return False
    commit = manifest.get("acquisition_commit")
    if not commit and isinstance(manifest.get("measurement_source"), dict):
        commit = manifest["measurement_source"].get("git_head")
    if commit and not str(commit).startswith(LEGACY_OCT12_ACQUISITION_COMMIT_PREFIX):
        return False
    return True


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
        tail_status = cpu.get("tail_finalization_status")
        if isinstance(tail_status, dict) and not tail_finalization_acceptable(tail_status):
            return False
        if int(cpu.get("tail_trailing_partial_bytes") or 0) > 0:
            return False
        if not cpu.get("process_cpu_sample_count"):
            return False
        return True
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
