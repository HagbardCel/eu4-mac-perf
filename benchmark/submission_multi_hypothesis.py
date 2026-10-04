"""Multi-hypothesis observer interpretation (protocol v3)."""

from __future__ import annotations

from typing import Any

import submission_control as control
from submission_counter_schema import COUNTER_COUNT, COUNTER_SCHEMA_VERSION, COUNTER_SLOTS, HYPOTHESES

CANDIDATE_BANK_COUNTER_NAMES = tuple(
    name for name, slot in COUNTER_SLOTS.items() if slot >= COUNTER_SLOTS["candidate_site_entries"]
)


def _counter_delta(start: dict[str, int], end: dict[str, int], name: str) -> int:
    return int(end.get(name, 0)) - int(start.get(name, 0))


def _candidate_totals(frozen: dict[str, int]) -> dict[str, int]:
    return {name: int(frozen.get(name, 0)) for name in CANDIDATE_BANK_COUNTER_NAMES}


def check_candidate_invariants(totals: dict[str, int]) -> list[str]:
    """Exact identities on one coherent frozen candidate bank (ARM reset → absolute totals)."""
    errors: list[str] = []
    same = totals.get("same_parent_pairs", 0)
    cross = totals.get("cross_parent_pairs", 0)
    adjacent = totals.get("adjacent_draw_pairs_total", 0)
    if same + cross != adjacent:
        errors.append(f"pair partition mismatch: {same}+{cross}!={adjacent}")
    site = totals.get("candidate_site_entries", 0)
    nonempty = totals.get("candidate_nonempty_invocations", 0)
    if site - nonempty != adjacent:
        errors.append(f"nonempty identity mismatch: {site}-{nonempty}!={adjacent}")
    return errors


def check_frozen_snapshot_meta(frozen: dict[str, int]) -> list[str]:
    errors: list[str] = []
    if frozen.get("protocol_version") != control.PROTOCOL_VERSION:
        errors.append(f"protocol_version {frozen.get('protocol_version')} != {control.PROTOCOL_VERSION}")
    if frozen.get("counter_schema_version") != COUNTER_SCHEMA_VERSION:
        errors.append("counter_schema_version mismatch")
    if frozen.get("counter_count") != COUNTER_COUNT:
        errors.append("counter_count mismatch")
    if frozen.get("observation_ack_state") != control.OBS_ACK_FROZEN:
        errors.append("candidate bank not ACK_FROZEN")
    return errors


def check_observer_exposure(candidate_totals: dict[str, int], health_delta: dict[str, int]) -> list[str]:
    errors: list[str] = []
    if candidate_totals.get("candidate_site_entries", 0) <= 0:
        errors.append("candidate_site_entries is zero")
    if candidate_totals.get("candidate_nonempty_invocations", 0) <= 0:
        errors.append("candidate_nonempty_invocations is zero")
    if health_delta.get("renderbuckets_invocations", 0) <= 0:
        errors.append("renderbuckets_invocations health delta is zero")
    if health_delta.get("site_entries", 0) <= 0:
        errors.append("site_entries health delta is zero")
    if candidate_totals.get("candidate_swaps", 0) <= 0:
        errors.append("candidate_swaps is zero")
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
    frozen_candidate_snapshot: dict[str, int],
    *,
    health_start: dict[str, int],
    health_end: dict[str, int],
    measurement_paused_swaps: int,
    measurement_wall_seconds: float,
    material_threshold_calls_per_swap: float = 0.02,
) -> dict[str, Any]:
    """Interpret candidate bank as absolute ARM→FREEZE totals; health bank as deltas."""
    frozen = frozen_candidate_snapshot
    supported_mask = int(frozen.get("supported_hypothesis_mask", 0))
    safety_mask = int(frozen.get("safety_qualified_hypothesis_mask", 0))
    candidate_totals = _candidate_totals(frozen)
    health_delta = {
        "renderbuckets_invocations": _counter_delta(health_start, health_end, "renderbuckets_invocations"),
        "site_entries": _counter_delta(health_start, health_end, "site_entries"),
        "effective_actions": _counter_delta(health_start, health_end, "effective_actions"),
    }
    effective_actions_delta = int(health_delta["effective_actions"])
    meta_errors = check_frozen_snapshot_meta(frozen)
    invariant_errors = check_candidate_invariants(candidate_totals)
    exposure_errors = check_observer_exposure(candidate_totals, health_delta)
    mutation_errors: list[str] = []
    if effective_actions_delta != 0:
        mutation_errors.append(f"effective_actions_delta={effective_actions_delta}")
    all_errors = meta_errors + invariant_errors + exposure_errors + mutation_errors
    support = hypothesis_support(supported_mask, safety_mask)
    candidate_swaps = max(1, int(candidate_totals.get("candidate_swaps", 0)))
    hypotheses: dict[str, Any] = {}
    mapping = {
        "same_parent_buffer_signature": ("same_parent_buffer_signature", "same_parent_pairs"),
        "cross_parent_buffer_signature": ("cross_parent_buffer_signature", "cross_parent_pairs"),
        "cross_parent_buffer_elision": ("cross_parent_buffer_elision_eligible", "cross_parent_pairs"),
        "same_subrecord_pointer_cross_parent": ("same_subrecord_pointer_cross_parent", "cross_parent_pairs"),
        "same_texture_input_signature": ("same_texture_input_signature", "adjacent_draw_pairs_total"),
    }
    for hyp_id, (counter, denom_name) in mapping.items():
        hits = int(candidate_totals.get(counter, 0))
        evaluated = int(candidate_totals.get(denom_name, 0))
        hps = hits / candidate_swaps
        hypotheses[hyp_id] = classify_hypothesis(
            supported=support[hyp_id]["supported"],
            safety_qualified=support[hyp_id]["safety_qualified"],
            evaluated=evaluated,
            hits=hits,
            hits_per_swap=hps if hits else 0.0,
            material_threshold_calls_per_swap=material_threshold_calls_per_swap,
        )
    return {
        "candidate_interpretation": "absolute_arm_to_freeze",
        "meta_errors": meta_errors,
        "invariant_errors": invariant_errors,
        "exposure_errors": exposure_errors,
        "mutation_errors": mutation_errors,
        "effective_actions_delta": effective_actions_delta,
        "harness_ok": len(all_errors) == 0,
        "measurement_paused_swaps": measurement_paused_swaps,
        "candidate_swaps": int(candidate_totals.get("candidate_swaps", 0)),
        "measurement_wall_seconds": measurement_wall_seconds,
        "advertised_supported_hypothesis_mask": supported_mask,
        "advertised_safety_qualified_hypothesis_mask": safety_mask,
        "hypotheses": hypotheses,
        "candidate_totals": candidate_totals,
        "health_delta": health_delta,
    }


def v3_reference_health_gate(phases: list[dict]) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    all_ok = True
    for phase in phases:
        if phase.get("role") != "reference":
            continue
        cv = phase.get("control_validation") or {}
        ok = (
            cv.get("status") == "passed"
            and int(cv.get("effective_actions_delta", 0)) == 0
            and int(cv.get("candidate_site_entries_delta", 0)) > 0
            and int(cv.get("renderbuckets_invocations_delta", 0)) > 0
        )
        if not ok:
            all_ok = False
        reports.append({"name": phase.get("name"), "phase_ok": ok, "control_validation": cv})
    return {"status": "passed" if all_ok else "failed", "phases": reports}


def multi_hypothesis_smoke_gate(phases: list[dict], *, scene_gate: dict[str, Any]) -> dict[str, Any]:
    """v3 health, B1 control+harness, and absolute scene gates."""
    health = v3_reference_health_gate(phases)
    b1 = next((p for p in phases if p.get("name") == "b1"), None)
    mh = (b1 or {}).get("multi_hypothesis") or {}
    cv = (b1 or {}).get("control_validation") or {}
    b1_control_ok = cv.get("status") == "passed"
    b1_harness_ok = bool(mh.get("harness_ok"))
    b1_phase_ok = bool(mh.get("phase_ok"))
    scene_ok = scene_gate.get("status") == "passed"
    stage2 = any(row.get("stage2_recommended") for row in (mh.get("hypotheses") or {}).values())
    passed = health["status"] == "passed" and scene_ok and b1_control_ok and b1_harness_ok and b1_phase_ok
    return {
        "status": "passed" if passed else "failed",
        "reference_health": health,
        "observer_scene_gate": scene_gate,
        "b1_control_ok": b1_control_ok,
        "b1_multi_hypothesis": mh,
        "stage2_recommended": stage2,
    }
