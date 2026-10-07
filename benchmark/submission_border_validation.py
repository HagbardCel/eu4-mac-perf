"""Border multidraw experiment validation (capability 2, protocol v3 counters)."""

from __future__ import annotations

from typing import Any


def _delta(start: dict[str, Any], end: dict[str, Any], key: str) -> int:
    return int(end.get(key, 0)) - int(start.get(key, 0))


def border_phase_validation(
    role: str,
    start: dict[str, Any],
    end: dict[str, Any],
    *,
    border_mutate: bool,
    border_minimal: bool,
    legacy_gl_engagement: bool = True,
) -> dict[str, Any]:
    if not legacy_gl_engagement:
        return {
            "status": "skipped",
            "reason": "GL-tail pass-through observer removed; loop-head ROI gates are authoritative",
            "legacy_gl_engagement": False,
            "border_candidate_draws_delta": 0,
            "border_multidraw_calls_delta": 0,
            "border_draw_calls_eliminated_delta": 0,
            "border_fallback_mutate_disabled_delta": 0,
            "border_minimal": border_minimal,
            "border_mutate": border_mutate,
            "role": role,
        }
    draws = _delta(start, end, "border_candidate_draws")
    multidraw = _delta(start, end, "border_multidraw_calls")
    eliminated = _delta(start, end, "border_draw_calls_eliminated")
    mutate_disabled = _delta(start, end, "border_fallback_mutate_disabled")

    if border_minimal or role == "neutral":
        ok = draws == 0 and multidraw == 0 and eliminated == 0
        reason = None if ok else "neutral/minimal phases must not observe border candidate draws"
    elif role == "reference" or (role == "candidate" and not border_mutate):
        ok = draws > 0 and multidraw == 0 and eliminated == 0
        reason = None if ok else "reference phase requires border hook engagement without multidraw"
    elif role == "candidate" and border_mutate:
        # Authorized B-phase: multidraw ROI when mutation is enabled in the dylib.
        if multidraw > 0 and eliminated > 0:
            ok = draws > 0
            reason = None if ok else "mutating B-phase requires border candidate draws"
        else:
            # Observer-only / PR-B NO-GO: mutate flag set but engine must still observe draws.
            ok = draws > 0 and multidraw == 0 and eliminated == 0 and mutate_disabled > 0
            reason = (
                None
                if ok
                else "observer B-phase requires border draws with mutation disabled fallback accounting"
            )
    else:
        ok = False
        reason = f"unknown border phase role={role!r}"

    return {
        "status": "passed" if ok else "failed",
        "reason": reason,
        "border_candidate_draws_delta": draws,
        "border_multidraw_calls_delta": multidraw,
        "border_draw_calls_eliminated_delta": eliminated,
        "border_fallback_mutate_disabled_delta": mutate_disabled,
        "border_minimal": border_minimal,
        "border_mutate": border_mutate,
        "role": role,
    }


SEMANTIC_RUN_LEN_HISTOGRAM_KEYS = (
    "border_semantic_run_len_2",
    "border_semantic_run_len_3_4",
    "border_semantic_run_len_5_8",
    "border_semantic_run_len_9_16",
    "border_semantic_run_len_17_32",
    "border_semantic_run_len_33_64",
    "border_semantic_run_len_65_128",
    "border_semantic_run_len_129_plus",
)

IMPLEMENTABLE_RUN_LEN_HISTOGRAM_KEYS = (
    "border_implementable_run_len_2",
    "border_implementable_run_len_3_4",
    "border_implementable_run_len_5_8",
    "border_implementable_run_len_9_16",
    "border_implementable_run_len_17_32",
    "border_implementable_run_len_33_64",
    "border_implementable_run_len_65_128",
    "border_implementable_run_len_129_plus",
)

ARMED_ROI_PHASE_NAMES = ("a1", "b1", "a2", "b2", "a3")
BORDER_ROI_PRIMARY_PHASE = "a1"


def border_gate0_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    checked = int(snapshot.get("border_runtime_context_checked", 0))
    supported = int(snapshot.get("border_runtime_context_multidraw_supported", 0))
    return {
        "context_checked": checked > 0,
        "multidraw_supported": supported > 0,
        "border_runtime_context_checked": checked,
        "border_runtime_context_multidraw_supported": supported,
    }


def border_gate0_passes(snapshot: dict[str, Any]) -> bool:
    gate0 = border_gate0_from_snapshot(snapshot)
    return bool(gate0["context_checked"] and gate0["multidraw_supported"])


def border_roi_validity_gate(snapshot: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "border_loop_decode_failures": int(snapshot.get("border_loop_decode_failures", 0)),
        "border_roi_epoch_invalid": int(snapshot.get("border_roi_epoch_invalid", 0)),
        "border_thread_mismatch_count": int(snapshot.get("border_thread_mismatch_count", 0)),
        "border_roi_freeze_partial_walk": int(snapshot.get("border_roi_freeze_partial_walk", 0)),
        "border_roi_freeze_semantic_suppress": int(snapshot.get("border_roi_freeze_semantic_suppress", 0)),
        "border_roi_freeze_implementable_suppress": int(
            snapshot.get("border_roi_freeze_implementable_suppress", 0)
        ),
    }
    failures = [name for name, value in fields.items() if value != 0]
    ok = not failures
    return {
        "status": "passed" if ok else "failed",
        "reason": None if ok else f"nonzero validity field(s): {', '.join(failures)}",
        **fields,
    }


def border_roi_consistency_gate(snapshot: dict[str, Any]) -> dict[str, Any]:
    impl_elim = int(snapshot.get("border_implementable_eliminations", 0))
    sem_elim = int(snapshot.get("border_semantic_eliminations", 0))
    struct_elim = int(snapshot.get("border_structural_eliminations", 0))
    sem_decisions = int(snapshot.get("border_semantic_decisions", 0))
    impl_decisions = int(snapshot.get("border_implementable_decisions", 0))
    sem_evals = int(snapshot.get("border_semantic_evaluations", 0))
    impl_evals = int(snapshot.get("border_implementable_evaluations", 0))
    sem_hist = sum(int(snapshot.get(k, 0)) for k in SEMANTIC_RUN_LEN_HISTOGRAM_KEYS)
    impl_hist = sum(int(snapshot.get(k, 0)) for k in IMPLEMENTABLE_RUN_LEN_HISTOGRAM_KEYS)
    failures: list[str] = []
    if impl_elim > sem_elim:
        failures.append("implementable_eliminations > semantic_eliminations")
    if sem_elim > struct_elim:
        failures.append("semantic_eliminations > structural_eliminations")
    if sem_hist != sem_decisions:
        failures.append("semantic histogram sum != semantic_decisions")
    if impl_hist != impl_decisions:
        failures.append("implementable histogram sum != implementable_decisions")
    if sem_decisions > sem_evals:
        failures.append("semantic_decisions > semantic_evaluations")
    if impl_decisions > impl_evals:
        failures.append("implementable_decisions > implementable_evaluations")
    ok = not failures
    return {
        "status": "passed" if ok else "failed",
        "reason": None if ok else "; ".join(failures),
        "border_implementable_eliminations": impl_elim,
        "border_semantic_eliminations": sem_elim,
        "border_structural_eliminations": struct_elim,
        "semantic_histogram_sum": sem_hist,
        "implementable_histogram_sum": impl_hist,
    }


def border_roi_domain_coverage_gate(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Schema-v6 mode census validity (measurement vs empty mode-0 domain)."""
    schema = int(snapshot.get("counter_schema_version", 0))
    loop_head = int(snapshot.get("border_loop_head_entries", 0))
    swaps = int(snapshot.get("candidate_swaps", 0))
    m0 = int(snapshot.get("border_mode0_entries", 0))
    m1 = int(snapshot.get("border_mode1_entries", 0))
    m_other = int(snapshot.get("border_mode_other_entries", 0))
    m0_vnz = int(snapshot.get("border_mode0_visible_nonzero_triangle_entries", 0))
    structural = int(snapshot.get("border_structural_draws", 0))

    validity = border_roi_validity_gate(snapshot)
    failures: list[str] = []
    if schema != 6:
        failures.append("counter_schema_version != 6")
    if loop_head <= 0:
        failures.append("border_loop_head_entries == 0")
    if swaps <= 0:
        failures.append("candidate_swaps == 0")
    if validity["status"] != "passed":
        failures.append(f"validity: {validity.get('reason')}")
    mode_partition_valid = m0 + m1 + m_other == loop_head
    if not mode_partition_valid:
        failures.append("mode partition does not sum to loop_head_entries")
    structural_census_consistent = m0_vnz == structural
    if validity["status"] == "passed" and loop_head > 0 and not structural_census_consistent:
        failures.append("mode0_vnz != border_structural_draws")

    measurement_valid = not failures
    mode0_domain_exercised = m0_vnz > 0
    mode0_domain_status = "present" if mode0_domain_exercised else "empty"
    status = "passed" if measurement_valid else "failed"
    return {
        "status": status,
        "measurement_valid": measurement_valid,
        "counter_schema_version": schema,
        "loop_head_engaged": loop_head > 0,
        "mode_partition_valid": mode_partition_valid,
        "structural_census_consistent": structural_census_consistent,
        "mode0_domain_status": mode0_domain_status if measurement_valid else None,
        "mode0_domain_exercised": mode0_domain_exercised if measurement_valid else False,
        "reason": None if measurement_valid else "; ".join(failures),
        "border_roi_validity_gate": validity,
        "border_loop_head_entries": loop_head,
        "candidate_swaps": swaps,
        "border_mode0_visible_nonzero_triangle_entries": m0_vnz,
        "border_structural_draws": structural,
    }


def border_phase_roi_authoritative_gates(snapshot: dict[str, Any]) -> dict[str, Any]:
    engagement = border_roi_engagement_validation(snapshot)
    validity = border_roi_validity_gate(snapshot)
    consistency = border_roi_consistency_gate(snapshot)
    gate0_ok = border_gate0_passes(snapshot)
    failures: list[str] = []
    if engagement["status"] != "passed":
        failures.append("engagement")
    if validity["status"] != "passed":
        failures.append(f"validity: {validity.get('reason')}")
    if consistency["status"] != "passed":
        failures.append(f"consistency: {consistency.get('reason')}")
    if not gate0_ok:
        failures.append("gate0")
    ok = not failures
    return {
        "status": "passed" if ok else "failed",
        "reason": None if ok else "; ".join(failures),
        "border_roi_engagement_gate": engagement,
        "border_roi_validity_gate": validity,
        "border_roi_consistency_gate": consistency,
        "border_gate0": border_gate0_from_snapshot(snapshot),
        "border_gate0_passed": gate0_ok,
    }


def border_roi_engagement_validation(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Observer-only Venice ROI ladder (no GL mutation)."""
    loop_head = int(snapshot.get("border_loop_head_entries", 0))
    semantic = int(snapshot.get("border_semantic_evaluations", 0))
    implementable = int(snapshot.get("border_implementable_evaluations", 0))
    swaps = int(snapshot.get("candidate_swaps", 0))
    multidraw = int(snapshot.get("border_multidraw_calls", 0))
    eliminated = int(snapshot.get("border_draw_calls_eliminated", 0))
    checks = [
        loop_head > 0,
        semantic > 0,
        implementable > 0,
        swaps > 0,
        multidraw == 0,
        eliminated == 0,
    ]
    ok = all(checks)
    return {
        "status": "passed" if ok else "failed",
        "border_loop_head_entries": loop_head,
        "border_semantic_evaluations": semantic,
        "border_implementable_evaluations": implementable,
        "candidate_swaps": swaps,
        "border_multidraw_calls": multidraw,
        "border_draw_calls_eliminated": eliminated,
    }


def border_experiment_gate(phases: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [p for p in phases if p.get("name", "").startswith(("a", "b"))]
    failures: list[str] = []
    ref_draws = 0
    cand_draws = 0
    for phase in measured:
        bv = phase.get("border_validation") or {}
        if bv.get("status") != "passed":
            failures.append(f"{phase.get('name')}: {bv.get('reason')}")
        name = str(phase.get("name", ""))
        if name.startswith("a"):
            ref_draws += int(bv.get("border_candidate_draws_delta", 0))
        if name.startswith("b"):
            cand_draws += int(bv.get("border_candidate_draws_delta", 0))

    if ref_draws <= 0:
        failures.append("no border candidate draws in reference (A) phases")
    if cand_draws <= 0:
        failures.append("no border candidate draws in candidate (B) phases")

    ok = not failures
    return {
        "status": "passed" if ok else "failed",
        "reason": None if ok else "; ".join(failures),
        "reference_border_draws": ref_draws,
        "candidate_border_draws": cand_draws,
    }
