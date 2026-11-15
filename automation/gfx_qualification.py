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


MATCH_SCHEMA = "gfx-campaign-match/v1"
_COMPARED = ("pass", "fail", "unverified")


def _campaign_pairs(result: dict) -> list[dict]:
    """Pairs recorded for qualification, newest schema first."""
    frames = result.get("frames") or {}
    pairs = frames.get("pairs")
    if isinstance(pairs, list):
        return [pair for pair in pairs if isinstance(pair, dict)]
    last = frames.get("last_pair")
    return [last] if isinstance(last, dict) else []


def _pair_dumps(result: dict) -> dict:
    """Build the full digest series from every compared pair's own dump.

    ``result.dumps`` preserves the stop pair and its neighbours; the per-pair
    records preserve the compared frame's validated metadata for the whole
    campaign.  Combining both gives a consecutive series without re-reading
    payloads or inventing digests.  A pair is only admitted when its comparison
    ran (so ``_compare_pair`` already validated the dump payload).
    """
    merged = {
        role: {"captured": [], "missing": []}
        for role in ("reference", "candidate")
    }
    for role in merged:
        for item in ((result.get("dumps") or {}).get(role) or {}).get(
                "captured") or []:
            if isinstance(item, dict):
                merged[role]["captured"].append(item)
    seen: dict[str, set[tuple[int, str]]] = {
        role: set() for role in merged}
    for pair in _campaign_pairs(result):
        comparison = pair.get("comparison") or {}
        if comparison.get("status") not in _COMPARED:
            continue
        for role in merged:
            payload = (pair.get("dumps") or {}).get(role) or {}
            if payload.get("valid") is not True:
                # No collector validity, no contribution.  This prevents a
                # synthetic valid=True record from manufacturing staticity.
                continue
            found = payload.get("found") or {}
            if not isinstance(found, dict):
                continue
            for key, meta in found.items():
                if not isinstance(meta, dict):
                    continue
                index = meta.get("frame_index", key)
                if type(index) is str and index.isdigit():
                    index = int(index)
                if type(index) is not int:
                    continue
                digest = meta.get("sha256")
                marker = (index, str(digest))
                if marker in seen[role]:
                    continue
                seen[role].add(marker)
                merged[role]["captured"].append({
                    "frame_index": index,
                    "sha256": digest,
                    "valid": True,
                    "preserved": True,
                    "reasons": [],
                    "meta": meta,
                })
    return merged


def pair_match_evidence(result: dict, pair: dict, *, required: int,
                        session: str | None = None) -> dict:
    """Whether one passing pair carries trustworthy qualification evidence."""
    comparison = pair.get("comparison") or {}
    correspondence = _correspondence(pair, comparison)
    frames = _pair_frames(pair)
    problems: list[str] = []
    if comparison.get("status") != "pass":
        problems.append(
            f"comparison is {comparison.get('status') or 'absent'}, not pass")
    if not correspondence["agrees"]:
        problems.append(
            "pair correspondence is not authoritative "
            f"(pair {correspondence['pair_status'] or 'absent'}, "
            f"comparison {correspondence['comparison_status'] or 'absent'})")
    staticity = gfx_align.scene_staticity(
        _pair_dumps(result), required=required, frames=frames, session=session)
    if not staticity["verified"]:
        problems.append(
            "pair-local static scene is unverified: " + staticity["reason"])
    return {
        "supported": not problems,
        "problems": problems,
        "reference_frame": frames["reference"],
        "candidate_frame": frames["candidate"],
        "correspondence": correspondence,
        "staticity": staticity,
        "comparison_status": comparison.get("status"),
    }


def match_evidence(result: dict, *, scenario: dict | None = None,
                   required_frames: int | None = None) -> dict:
    """Decide whether every requested comparison is validly qualified.

    A ``match`` is never derived from a digest streak somewhere in the campaign
    or from the legacy ``result.stability.status``.  It requires complete
    requested coverage and, for each requested pair, an authoritative
    correspondence plus a pair-local static run containing that pair's frame.
    Missing per-pair evidence is an incomplete result, not a match.
    """
    scenario = scenario if scenario is not None else (result.get("scenario") or {})
    frames = result.get("frames") or {}
    comparison = frames.get("comparison") or {}
    required = int(required_frames if required_frames is not None
                   else frames.get("paired") or 1)
    required = max(1, required)
    session = _session_id(result)
    problems: list[str] = []

    policy = (scenario.get("comparison") or {}).get("policy")
    if policy == "diagnostic":
        problems.append("diagnostic policy reports differences without qualifying")
    if not session:
        problems.append("campaign session identity is absent")
    health = result.get("health") or {}
    if health.get("verdict") != HEALTHY:
        problems.append(
            f"GPU health is {health.get('verdict') or 'unknown'}, not healthy")
    if health.get("instrumentation_failures"):
        problems.append("instrumentation failure is present")
    if comparison.get("status") not in (None, "pass"):
        problems.append(
            f"campaign comparison status is {comparison.get('status')}")
    if int(comparison.get("failed") or 0):
        problems.append("the campaign contains failed comparisons")
    if int(comparison.get("unverified") or 0):
        problems.append("the campaign contains unverified comparisons")
    if int(comparison.get("not_compared") or 0):
        problems.append("the campaign contains pairs that were not compared")
    passed = int(comparison.get("passed") or 0)
    if passed < required:
        problems.append(
            f"requested {required} passing comparisons, got {passed}")
    paired = int(frames.get("paired") or 0)
    aligned = int(frames.get("aligned") or 0)
    if paired < required or aligned < required:
        problems.append(
            f"paired/aligned coverage is {paired}/{aligned}, required {required}")
    if int(frames.get("not_aligned") or 0):
        problems.append("the campaign contains not-aligned pairs")

    pairs = _campaign_pairs(result)
    if len(pairs) < required:
        problems.append(
            f"per-pair qualification evidence covers {len(pairs)} of "
            f"{required} requested comparisons")
    pair_results: list[dict] = []
    for pair in pairs[:required]:
        evidence = pair_match_evidence(
            result, pair, required=required_stable_frames(scenario),
            session=session)
        pair_results.append(evidence)
        if not evidence["supported"]:
            problems.append(
                f"pair {evidence['reference_frame']}/"
                f"{evidence['candidate_frame']}: "
                + "; ".join(evidence["problems"]))

    return {
        "schema": MATCH_SCHEMA,
        "supported": not problems,
        "problems": problems,
        "required_frames": required,
        "session": session,
        "coverage": {
            "passed": passed,
            "paired": paired,
            "aligned": aligned,
            "not_aligned": int(frames.get("not_aligned") or 0),
            "per_pair_records": len(pairs),
        },
        "pairs": pair_results,
    }
