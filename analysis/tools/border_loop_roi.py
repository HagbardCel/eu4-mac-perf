"""ROI readiness validation and Venice measured-rate helpers (no synthetic ROI pass)."""

from __future__ import annotations

from typing import Literal

RoiInstrumentationStatus = Literal["PENDING", "READY_OFFLINE", "FAILED_SELF_TEST"]
RoiGateStatus = Literal["PENDING_NUMERIC_EVIDENCE", "PASS_VENICE_MEASURED", "FAIL_BELOW_THRESHOLD"]
SelfTestBucketStatus = Literal["PASS", "PENDING", "NOT_RUN", "FAIL"]

MANDATORY_SELF_TEST_BUCKETS: tuple[str, ...] = (
    "portable_suite",
    "macos_x86_64_border_build",
    "classifier_python_c_parity",
    "classifier_resolved_core_parity",
    "dual_suppression_replay",
    "gateway_runtime",
    "deferred_install_lifecycle",
    "border_detour_installer",
    "decode_failures",
    "thread_affinity",
    "freeze_state_capture",
    "counter_schema",
)

_VALID_BUCKET_STATUSES = frozenset({"PASS", "PENDING", "NOT_RUN", "FAIL"})

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


def _validate_self_tests(status: str, self_tests: dict) -> None:
    if not isinstance(self_tests, dict):
        raise AssertionError("self_tests must be an object")
    for key in MANDATORY_SELF_TEST_BUCKETS:
        if key not in self_tests:
            raise AssertionError(f"missing mandatory self_tests bucket {key!r}")
        val = self_tests[key]
        if val not in _VALID_BUCKET_STATUSES:
            raise AssertionError(f"self_tests[{key!r}] must be PASS|PENDING|NOT_RUN|FAIL, got {val!r}")

    if any(self_tests[k] == "FAIL" for k in MANDATORY_SELF_TEST_BUCKETS):
        if status != "FAILED_SELF_TEST":
            raise AssertionError("any FAIL bucket requires roi_instrumentation_status FAILED_SELF_TEST")
    if status == "FAILED_SELF_TEST" and not any(self_tests[k] == "FAIL" for k in MANDATORY_SELF_TEST_BUCKETS):
        raise AssertionError("FAILED_SELF_TEST requires at least one FAIL bucket")

    if status == "READY_OFFLINE":
        for key in MANDATORY_SELF_TEST_BUCKETS:
            if self_tests[key] != "PASS":
                raise AssertionError(f"READY_OFFLINE requires self_tests[{key!r}] == PASS")
    elif status == "PENDING":
        if all(self_tests[k] == "PASS" for k in MANDATORY_SELF_TEST_BUCKETS):
            raise AssertionError("PENDING requires at least one mandatory bucket != PASS")


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
        if payload.get("runtime_gate_0_probe_ready") is not True:
            raise AssertionError("runtime_gate_0_probe_ready must be true when READY_OFFLINE")
        if payload.get("runtime_detour_installer_ready") is not True:
            raise AssertionError("runtime_detour_installer_ready must be true when READY_OFFLINE")
    elif status == "PENDING":
        if payload.get("ready_for_single_venice_observer_run") is not False:
            raise AssertionError("ready_for_single_venice_observer_run must be false while PENDING")
    for runtime_key in ("runtime_gate_0", "runtime_detour_installation"):
        val = payload.get(runtime_key)
        if status == "READY_OFFLINE" and val not in ("PENDING",):
            raise AssertionError(f"{runtime_key} must remain PENDING until Venice proves runtime")
        if status in ("PENDING", "FAILED_SELF_TEST") and val not in ("PENDING",):
            raise AssertionError(f"{runtime_key} must be PENDING during verification")
    _validate_self_tests(status, payload.get("self_tests", {}))
