"""Multi-hypothesis observer interpretation (protocol v3)."""

from __future__ import annotations

from typing import Any

from submission_counter_schema import HYPOTHESES, SAFETY_HYPOTHESIS_MASK_DEFAULT, SUPPORTED_HYPOTHESIS_MASK_DEFAULT


def _counter_delta(start: dict[str, int], end: dict[str, int], name: str) -> int:
    return int(end.get(name, 0)) - int(start.get(name, 0))


def check_candidate_invariants(delta: dict[str, int]) -> list[str]:
    errors: list[str] = []
    same = delta.get("same_parent_pairs", 0)
    cross = delta.get("cross_parent_pairs", 0)
    adjacent = delta.get("adjacent_draw_pairs_total", 0)
    if same + cross != adjacent:
        errors.append(f"pair partition mismatch: {same}+{cross}!={adjacent}")
    site = delta.get("candidate_site_entries", 0)
    nonempty = delta.get("candidate_nonempty_invocations", 0)
    if site - nonempty != adjacent:
        errors.append(f"nonempty identity mismatch: {site}-{nonempty}!={adjacent}")
    return errors


def hypothesis_support(advertised_supported: int, advertised_safety: int) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in HYPOTHESES:
        bit = 1 << int(row["bit"])
        rows[row["id"]] = {
            "supported": bool(advertised_supported & bit),
            "safety_qualified": bool(advertised_safety & bit),
        }
    return rows


def classify_hypothesis(
    *,
    supported: bool,
    safety_qualified: bool,
    evaluated: int,
    hits: int,
    hits_per_swap: float | None,
    material_threshold_calls_per_swap: float | None,
) -> dict[str, Any]:
    if not supported:
        return {"status": "unsupported", "evaluated": 0, "hits": 0, "stage2_recommended": False}
    if evaluated <= 0:
        return {"status": "not_exercised", "evaluated": evaluated, "hits": hits, "stage2_recommended": False}
    rate = hits / evaluated if evaluated else 0.0
    out: dict[str, Any] = {
        "status": "evaluated_no_recurrence" if hits == 0 else "recurrence_observed",
        "evaluated": evaluated,
        "hits": hits,
        "rate": rate,
        "hits_per_swap": hits_per_swap,
        "safety_qualified": safety_qualified,
        "stage2_recommended": False,
    }
    if hits > 0 and safety_qualified and hits_per_swap is not None and material_threshold_calls_per_swap is not None:
        removable = 2.0 * hits_per_swap
        out["estimated_removable_helper_calls_per_swap"] = removable
        if removable >= material_threshold_calls_per_swap:
            out["status"] = "material"
            out["stage2_recommended"] = True
    elif hits > 0 and not safety_qualified:
        out["followup_recommended"] = "static_re"
    return out


def evaluate_b_phase(
    start: dict[str, int],
    end: dict[str, int],
    *,
    measurement_paused_swaps: int,
    measurement_wall_seconds: float,
    supported_mask: int = SUPPORTED_HYPOTHESIS_MASK_DEFAULT,
    safety_mask: int = SAFETY_HYPOTHESIS_MASK_DEFAULT,
    material_threshold_calls_per_swap: float = 0.02,
) -> dict[str, Any]:
    delta = {name: _counter_delta(start, end, name) for name in end if isinstance(end[name], int)}
    invariant_errors = check_candidate_invariants(delta)
    support = hypothesis_support(supported_mask, safety_mask)
    swaps = max(1, int(measurement_paused_swaps))
    hypotheses: dict[str, Any] = {}
    mapping = {
        "same_parent_buffer_signature": ("same_parent_buffer_signature", "same_parent_pairs"),
        "cross_parent_buffer_signature": ("cross_parent_buffer_signature", "cross_parent_pairs"),
        "cross_parent_buffer_elision": ("cross_parent_buffer_elision_eligible", "cross_parent_pairs"),
        "same_subrecord_pointer_cross_parent": ("same_subrecord_pointer_cross_parent", "cross_parent_pairs"),
        "same_texture_input_signature": ("same_texture_input_signature", "adjacent_draw_pairs_total"),
    }
    for hyp_id, (counter, denom_name) in mapping.items():
        hits = int(delta.get(counter, 0))
        evaluated = int(delta.get(denom_name, 0))
        hps = hits / swaps
        hypotheses[hyp_id] = classify_hypothesis(
            supported=support[hyp_id]["supported"],
            safety_qualified=support[hyp_id]["safety_qualified"],
            evaluated=evaluated,
            hits=hits,
            hits_per_swap=hps if hits else 0.0,
            material_threshold_calls_per_swap=material_threshold_calls_per_swap,
        )
    return {
        "invariant_errors": invariant_errors,
        "harness_ok": len(invariant_errors) == 0,
        "measurement_paused_swaps": measurement_paused_swaps,
        "measurement_wall_seconds": measurement_wall_seconds,
        "hypotheses": hypotheses,
        "counter_delta": delta,
    }


def multi_hypothesis_smoke_gate(phases: list[dict]) -> dict[str, Any]:
    """Health phases + B1 frozen-bank invariants (no v1 pair-hit pass requirement)."""
    health_ok = True
    health_reports: list[dict[str, Any]] = []
    for phase in phases:
        if phase.get("role") != "reference":
            continue
        cv = phase.get("control_validation") or {}
        ok = cv.get("status") == "passed" and int(cv.get("effective_actions_delta", 0)) == 0
        health_ok = health_ok and ok
        health_reports.append({"name": phase.get("name"), "phase_ok": ok, "control_validation": cv})
    b1 = next((p for p in phases if p.get("name") == "b1"), None)
    mh = (b1 or {}).get("multi_hypothesis") or {}
    b_ok = bool(mh.get("harness_ok"))
    stage2 = any(row.get("stage2_recommended") for row in (mh.get("hypotheses") or {}).values())
    passed = health_ok and b_ok
    return {
        "status": "passed" if passed else "failed",
        "health_phases": health_reports,
        "b1_multi_hypothesis": mh,
        "stage2_recommended": stage2,
    }
