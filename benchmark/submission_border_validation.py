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
) -> dict[str, Any]:
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


def border_gate0_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    checked = int(snapshot.get("border_runtime_context_checked", 0))
    supported = int(snapshot.get("border_runtime_context_multidraw_supported", 0))
    return {
        "context_checked": checked > 0,
        "multidraw_supported": supported > 0,
        "border_runtime_context_checked": checked,
        "border_runtime_context_multidraw_supported": supported,
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
