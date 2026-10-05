"""Mechanical static-feasibility verdict from per-criterion status."""

from __future__ import annotations

from typing import Literal

CriterionStatus = Literal["PASS", "PENDING", "FAIL"]
StaticFeasibility = Literal["PENDING_STATIC_RE", "GO_CONDITIONAL_RUNTIME_GATES", "NO_GO"]

CRITERION_KEYS = (
    "1_classifier_inputs",
    "2_control_flow_prefix_realizability",
    "3_entry_state",
    "4_fail_closed_paths",
    "5_helper_side_effect_equivalence",
    "6_cpu_continuation",
    "7_engine_gl_equivalence",
    "8_continuation_mechanics",
    "9_detour_relocation",
    "10_uncertainty_falls_through",
)


def derive_static_feasibility(static_criteria: dict[str, CriterionStatus]) -> StaticFeasibility:
    values = [static_criteria[k] for k in CRITERION_KEYS]
    if any(v == "FAIL" for v in values):
        return "NO_GO"
    if all(v == "PASS" for v in values):
        return "GO_CONDITIONAL_RUNTIME_GATES"
    return "PENDING_STATIC_RE"


def derive_static_item_lists(
    static_criteria: dict[str, CriterionStatus],
) -> tuple[list[str], list[str]]:
    open_items = [k for k in CRITERION_KEYS if static_criteria.get(k) == "PENDING"]
    failed_items = [k for k in CRITERION_KEYS if static_criteria.get(k) == "FAIL"]
    return open_items, failed_items


def validate_evidence_payload(payload: dict) -> None:
    """Raise AssertionError if verdict disagrees with static_criteria."""
    criteria = payload["static_criteria"]
    expected = derive_static_feasibility(criteria)
    actual = payload["static_feasibility"]
    if actual != expected:
        raise AssertionError(f"static_feasibility {actual!r} != derived {expected!r}")
    open_items, failed_items = derive_static_item_lists(criteria)
    if payload.get("open_static_items") != open_items:
        raise AssertionError("open_static_items does not match PENDING criteria")
    if payload.get("failed_static_items") != failed_items:
        raise AssertionError("failed_static_items does not match FAIL criteria")
