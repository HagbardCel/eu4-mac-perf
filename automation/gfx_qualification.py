"""Qualification policy shared by the campaign runner and the offline case.

One predicate decides whether a measured pixel difference may be attributed to
a renderer.  Campaign classification (`gfx_dual_run.classify_campaign`) and
offline case classification (`gfx_case._classify`) both call it, so a preserved
case can never claim a stronger verdict than its campaign evidence supports.

The policy is deliberately scoped to the paused, pixel-static experiment: a
confirmed renderer difference needs valid pixel capture, authoritative pair
correspondence, pair-local full-resolution staticity and healthy GPU execution.
Animated gameplay may legitimately differ from frame to frame; it is retained
as an observation but does not receive renderer blame.
"""

from __future__ import annotations

from automation import gfx_align

SCHEMA = "gfx-renderer-difference/v1"
HEALTHY = "healthy"


def required_stable_frames(scenario: dict | None) -> int:
    """The campaign's configured static-scene requirement (never below two).

    ``settle.stable_frames`` is the single source of truth.  A missing value
    falls back to the minimum that can express "consecutive identical frames"
    and does not silently override a larger campaign requirement.
    """
    settle = (scenario or {}).get("settle") or {}
    value = settle.get("stable_frames", 1)
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 1
    return max(2, value)


def _session_id(result: dict) -> str:
    value = result.get("session_id")
    if isinstance(value, str) and value:
        return value
    session = result.get("session")
    if isinstance(session, dict):
        value = session.get("session_id")
        if isinstance(value, str) and value:
            return value
    return ""


def _first_failure(result: dict) -> dict | None:
    comparison = (result.get("frames") or {}).get("comparison") or {}
    pair = comparison.get("first_failure")
    return pair if isinstance(pair, dict) else None


def _pair_frames(pair: dict | None) -> dict:
    pair = pair if isinstance(pair, dict) else {}
    reference = pair.get("frame_index")
    candidate = pair.get("candidate_frame_index", reference)
    return {"reference": reference, "candidate": candidate}


def _correspondence(pair: dict | None, comparison: dict | None) -> dict:
    pair_status = (pair or {}).get("status")
    alignment = (comparison or {}).get("alignment")
    alignment_status = alignment.get("status") if isinstance(alignment, dict) else None
    agrees = (
        pair_status == gfx_align.ALIGNED
        and alignment_status == gfx_align.ALIGNED
    )
    return {
        "pair_status": pair_status or "",
        "comparison_status": alignment_status or "",
        "agrees": agrees,
    }


def renderer_difference_evidence(result: dict, *, scenario: dict | None = None,
                                 pair: dict | None = None,
                                 comparison: dict | None = None) -> dict:
    """Decide whether a measured pixel difference is attributable to a renderer.

    Returns a structured verdict with every reason that blocks attribution.
    Numerical difference statistics live in the comparison record and are never
    discarded here; only the attribution is withheld.
    """
    scenario = scenario if scenario is not None else (result.get("scenario") or {})
    if pair is None:
        pair = _first_failure(result)
    if comparison is None:
        comparison = ((pair or {}).get("comparison")
                      or (result.get("frames") or {}).get("comparison") or {})
    required = required_stable_frames(scenario)
    frames = _pair_frames(pair)
    session = _session_id(result)
    problems: list[str] = []

    health = result.get("health") or {}
    verdict = health.get("verdict")
    if verdict != HEALTHY:
        problems.append(
            f"GPU health is {verdict or 'unknown'}, not healthy")
    if health.get("instrumentation_failures"):
        problems.append("instrumentation failure is present")

    if not session:
        problems.append("campaign session identity is absent")

    correspondence = _correspondence(pair, comparison)
    if not correspondence["agrees"]:
        problems.append(
            "pair correspondence is not authoritative "
            f"(pair {correspondence['pair_status'] or 'absent'}, "
            f"comparison {correspondence['comparison_status'] or 'absent'})")

    if (comparison or {}).get("status") != "fail":
        problems.append("no measured pixel difference is recorded")

    staticity = gfx_align.scene_staticity(
        result.get("dumps"), required=required, frames=frames,
        session=session)
    if not staticity["verified"]:
        problems.append(
            "pair-local static scene is unverified: " + staticity["reason"])

    return {
        "schema": SCHEMA,
        "supported": not problems,
        "problems": problems,
        "required_stable_frames": required,
        "pair": {"reference_frame": frames["reference"],
                 "candidate_frame": frames["candidate"]},
        "session": session,
        "correspondence": correspondence,
        "staticity": staticity,
        "health": {
            "verdict": verdict or "",
            "instrumentation_failures": list(
                health.get("instrumentation_failures") or []),
        },
    }
