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


def _runtime_smoke_v5_proven(payload: dict) -> bool:
    smoke = payload.get("runtime_smoke") or {}
    if smoke.get("status") != "PASS_VENICE_MEASURED":
        return False
    schema = int(smoke.get("counter_schema_version", 0))
    if schema == 0:
        # Legacy flat runtime_smoke without schema version.
        return True
    return schema == 5


def _validate_domain_census_authorization(payload: dict, status: str) -> None:
    schema_top = int(payload.get("counter_schema_version", 0))
    if schema_top < 6:
        return
    domain_census = payload.get("domain_census_live")
    if not isinstance(domain_census, dict):
        raise AssertionError("counter_schema_version >= 6 requires domain_census_live object")
    if int(domain_census.get("counter_schema_version", 0)) != 6:
        raise AssertionError("domain_census_live.counter_schema_version must be 6")
    dc_status = domain_census.get("status")
    if dc_status == "PASS_VENICE_MEASURED":
        if domain_census.get("domain_gate") != "passed":
            raise AssertionError("domain_census_live PASS requires domain_gate passed")
        return
    if dc_status != "PENDING":
        raise AssertionError(f"unexpected domain_census_live.status {dc_status!r}")
    census_ready = domain_census.get("ready_for_single_venice_census_run")
    offline_verify = payload.get("offline_verification") or {}
    verified_commit = offline_verify.get("verified_commit")
    dc_offline = domain_census.get("offline_verified_commit")
    if status == "READY_OFFLINE":
        if not verified_commit:
            raise AssertionError("READY_OFFLINE requires offline_verification.verified_commit")
        if not dc_offline:
            raise AssertionError("READY_OFFLINE requires domain_census_live.offline_verified_commit")
        if verified_commit != dc_offline:
            raise AssertionError("offline_verification and domain_census_live commits must match")
        if census_ready is not True:
            raise AssertionError(
                "READY_OFFLINE requires domain_census_live.ready_for_single_venice_census_run true"
            )
    elif census_ready is not False:
        raise AssertionError(
            "PENDING instrumentation requires domain_census_live.ready_for_single_venice_census_run false"
        )


def _domain_census_live_proven(payload: dict) -> bool:
    dc = payload.get("domain_census_live") or {}
    return (
        dc.get("status") == "PASS_VENICE_MEASURED"
        and int(dc.get("counter_schema_version", 0)) == 6
        and dc.get("domain_gate") == "passed"
    )


def validate_roi_readiness_payload(payload: dict) -> None:
    if payload.get("evidence_kind") != "roi_instrumentation_readiness":
        raise AssertionError("evidence_kind must be roi_instrumentation_readiness")
    if payload.get("mutation_authorized") is not False:
        raise AssertionError("mutation_authorized must be false")
    if payload.get("observer_timing_usable") is not False:
        raise AssertionError("observer_timing_usable must be false")

    roi_gate = payload.get("roi_gate")
    if roi_gate == "PENDING_NUMERIC_EVIDENCE":
        for key in (
            "semantic_eliminations_per_frame",
            "implementable_eliminations_per_frame",
            "structural_eliminations_per_frame",
        ):
            if payload.get(key) is not None:
                raise AssertionError(f"{key} must be null while roi_gate is PENDING_NUMERIC_EVIDENCE")
    elif roi_gate == "FAIL_BELOW_THRESHOLD":
        if not _domain_census_live_proven(payload):
            raise AssertionError("FAIL_BELOW_THRESHOLD requires proven schema-v6 domain census")
        if payload.get("mode0_domain_status") != "empty":
            raise AssertionError("FAIL_BELOW_THRESHOLD requires mode0_domain_status empty")
        for key in (
            "semantic_eliminations_per_frame",
            "implementable_eliminations_per_frame",
            "structural_eliminations_per_frame",
        ):
            val = payload.get(key)
            if val != 0.0:
                raise AssertionError(f"{key} must be 0.0 for validated empty mode-0 domain")
    else:
        raise AssertionError(f"unsupported roi_gate {roi_gate!r}")

    status = payload.get("roi_instrumentation_status")
    if status not in ("READY_OFFLINE", "PENDING", "FAILED_SELF_TEST"):
        raise AssertionError(f"unexpected roi_instrumentation_status {status!r}")

    v5_runtime_proven = _runtime_smoke_v5_proven(payload)
    if status == "READY_OFFLINE":
        if v5_runtime_proven:
            if payload.get("ready_for_single_venice_observer_run") is not False:
                raise AssertionError(
                    "ready_for_single_venice_observer_run must be false after v5 ABABA live proof"
                )
        elif payload.get("ready_for_single_venice_observer_run") is not True:
            raise AssertionError(
                "ready_for_single_venice_observer_run must be true when READY_OFFLINE before live Venice"
            )
        if payload.get("runtime_gate_0_probe_ready") is not True:
            raise AssertionError("runtime_gate_0_probe_ready must be true when READY_OFFLINE")
        if payload.get("runtime_detour_installer_ready") is not True:
            raise AssertionError("runtime_detour_installer_ready must be true when READY_OFFLINE")
    elif status == "PENDING":
        if payload.get("ready_for_single_venice_observer_run") is not False:
            raise AssertionError("ready_for_single_venice_observer_run must be false while PENDING")

    _validate_domain_census_authorization(payload, status)

    for runtime_key in ("runtime_gate_0", "runtime_detour_installation"):
        val = payload.get(runtime_key)
        if v5_runtime_proven:
            if val != "PASS":
                raise AssertionError(f"{runtime_key} must be PASS when schema-v5 runtime_smoke is proven")
        elif status == "READY_OFFLINE" and val not in ("PENDING",):
            raise AssertionError(f"{runtime_key} must remain PENDING until Venice proves runtime")
        elif status in ("PENDING", "FAILED_SELF_TEST") and not v5_runtime_proven and val not in ("PENDING",):
            raise AssertionError(f"{runtime_key} must be PENDING during verification")

    _validate_self_tests(status, payload.get("self_tests", {}))
