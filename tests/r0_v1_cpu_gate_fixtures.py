"""Synthetic v1 CPU gate evidence (sidecar + SHA) for acceptance-gate tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import cpu_reconcile


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def materialize_v1_sidecar_for_gate(output_dir: Path, manifest: dict[str, Any]) -> None:
    cpu = manifest["epoch_a_thread_cpu"]
    sample_count = int(cpu["process_cpu_sample_count"])
    window_start = int(cpu["window_start_ns"])
    window_end = int(cpu["window_end_ns"])
    pid = int(cpu.get("pid", 42))
    thread_cpu = float(cpu["thread_cpu_ms_per_s"])
    process_cpu = float(cpu.get("process_cpu_ms_per_s", thread_cpu))
    residual = float(cpu.get("reconciliation_residual_ms_per_s", process_cpu - thread_cpu))
    reconcile_ok = cpu.get("reconcile_ok") is True

    cpu["pid"] = pid
    cpu["process_cpu_ms_per_s"] = process_cpu
    cpu["reconciliation_residual_ms_per_s"] = residual

    observations: list[dict[str, Any]] = []
    for index in range(sample_count):
        if sample_count == 1:
            when = window_start
        else:
            span = window_end - window_start
            when = window_start + (index * span) // (sample_count - 1)
        observations.append({"monotonic_ns": when, "cputime_ms_per_s": process_cpu})

    sidecar = cpu_reconcile.build_epoch_a_process_cpu_sidecar(
        observations=observations,
        process_mean=process_cpu,
        thread_report=cpu,
        clock_anchor={"monotonic_ns": window_start, "wall_ns": 0},
        pid=pid,
        reconciliation_residual_ms_per_s=residual,
        reconcile_ok=reconcile_ok,
        powermetrics_stream_path=None,
        run_dir=str(output_dir),
    )
    path = cpu_reconcile.write_epoch_a_process_cpu_sidecar_atomic(output_dir, sidecar)
    manifest["epoch_a_process_cpu_samples_path"] = path.name
    payload = path.read_bytes()
    cpu["process_cpu_sidecar_sha256"] = _sha256_bytes(payload)
