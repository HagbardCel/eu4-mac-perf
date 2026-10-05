"""ROI readiness validation and Venice measured-rate helpers (no synthetic ROI pass)."""

from __future__ import annotations

from typing import Literal

RoiInstrumentationStatus = Literal["PENDING", "READY_OFFLINE", "FAILED_SELF_TEST"]
RoiGateStatus = Literal["PENDING_NUMERIC_EVIDENCE", "PASS_VENICE_MEASURED", "FAIL_BELOW_THRESHOLD"]

# Profiler-off paused-Venice cadence reference (diagnostic cross-check only).
FPS_CREDIBLE_BASELINE_PAUSED_VENICE = 55.0


def eliminations_per_frame(delta_eliminations: int, delta_candidate_swaps: int) -> float | None:
    if delta_candidate_swaps <= 0:
        return None
    return delta_eliminations / delta_candidate_swaps


def estimated_production_eliminations_per_second(
    eliminations_per_frame_observer: float,
    fps_baseline: float = FPS_CREDIBLE_BASELINE_PAUSED_VENICE,
) -> float:
    """Do not multiply by observer swaps/s — use credible profiler-off cadence."""
    return eliminations_per_frame_observer * fps_baseline


def validate_roi_readiness_payload(payload: dict) -> None:
    if payload.get("evidence_kind") != "roi_instrumentation_readiness":
        raise AssertionError("evidence_kind must be roi_instrumentation_readiness")
    if payload.get("roi_gate") != "PENDING_NUMERIC_EVIDENCE":
        raise AssertionError("readiness artifact must not pass roi_gate")
    if payload.get("semantic_eliminations_per_frame") is not None:
        raise AssertionError("semantic_eliminations_per_frame must be null at readiness")
    if payload.get("implementable_eliminations_per_frame") is not None:
        raise AssertionError("implementable_eliminations_per_frame must be null at readiness")
    if payload.get("mutation_authorized") is not False:
        raise AssertionError("mutation_authorized must be false")
    if payload.get("observer_timing_usable") is not False:
        raise AssertionError("observer_timing_usable must be false")
    status = payload.get("roi_instrumentation_status")
    if status not in ("READY_OFFLINE", "PENDING", "FAILED_SELF_TEST"):
        raise AssertionError(f"unexpected roi_instrumentation_status {status!r}")
    if status == "READY_OFFLINE":
        if payload.get("ready_for_single_venice_observer_run") is not True:
            raise AssertionError("ready_for_single_venice_observer_run must be true when READY_OFFLINE")
    elif status == "PENDING":
        if payload.get("ready_for_single_venice_observer_run") is not False:
            raise AssertionError("ready_for_single_venice_observer_run must be false while PENDING")
