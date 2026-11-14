"""Semantic frame alignment for the dual-process harness (PR 3).

Alignment is decided from rendering evidence, never from output-image
similarity:

* phase/scene identity when the observation provides it;
* the unique caller-composition signature (same algorithm on both backends);
* draw count and draw ordering;
* the logical/physical extent relation;
* a bounded ordinal prior from the post-readiness anchor.

Outputs one of ``aligned``, ``ambiguous``, ``not_aligned`` or ``missing``.
Image comparison is only permitted for ``aligned`` pairs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ALIGNED = "aligned"
AMBIGUOUS = "ambiguous"
NOT_ALIGNED = "not_aligned"
MISSING = "missing"
STATUSES = (ALIGNED, AMBIGUOUS, NOT_ALIGNED, MISSING)

SCHEMA = "gfx-frame-alignment/v1"


def _int(value, default: int = 0) -> int:
    return value if type(value) is int else default


def _signature(record: dict) -> str:
    value = record.get("caller_signature")
    if not isinstance(value, str):
        return ""
    value = value.lower()
    # The zero signature is the initial FNV state: it means "no caller
    # composition", not "the same empty composition as the other backend".
    if value in ("", "0x0", "0x" + "0" * 16, "0" * 16):
        return ""
    return value


def _scene(record: dict) -> str:
    for key in ("scene", "stage", "phase", "scene_id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _extent(record: dict) -> tuple[int, int] | None:
    width = _int(record.get("width"))
    height = _int(record.get("height"))
    if width > 0 and height > 0:
        return width, height
    return None


def extent_relation(reference: dict, candidate: dict) -> dict:
    ref = _extent(reference)
    cand = _extent(candidate)
    relation = {
        "reference": list(ref) if ref else None,
        "candidate": list(cand) if cand else None,
        "scale": None,
        "normalized": False,
        "reason": "",
    }
    if ref is None or cand is None:
        relation["reason"] = "extent is missing"
        return relation
    (ref_w, ref_h), (cand_w, cand_h) = ref, cand
    if (ref_w, ref_h) == (cand_w, cand_h):
        relation["scale"] = 1
        relation["normalized"] = True
        return relation
    if cand_w % ref_w == 0 and cand_h % ref_h == 0 and cand_w // ref_w == cand_h // ref_h:
        scale = cand_w // ref_w
        if scale in (2, 3, 4):
            relation["scale"] = scale
            relation["normalized"] = True
            return relation
    relation["reason"] = "extent is not an integer logical/physical pair"
    return relation


def match_score(reference: dict, candidate: dict) -> tuple[int, list[str]]:
    """Evidence score for pairing two observations.  Higher is stronger."""
    score = 0
    evidence: list[str] = []
    ref_scene, cand_scene = _scene(reference), _scene(candidate)
    if ref_scene and cand_scene:
        if ref_scene != cand_scene:
            return -100, ["scene_mismatch"]
        score += 6
        evidence.append("scene")
    ref_sig, cand_sig = _signature(reference), _signature(candidate)
    if ref_sig and cand_sig:
        if ref_sig == cand_sig:
            score += 4
            evidence.append("caller_signature")
        else:
            score -= 2
            evidence.append("caller_signature_mismatch")
    ref_draws = _int(reference.get("draws"), -1)
    cand_draws = _int(candidate.get("draws"), -1)
    if ref_draws >= 0 and cand_draws >= 0:
        if ref_draws == cand_draws:
            score += 2
            evidence.append("draw_count")
        else:
            score -= 1
    relation = extent_relation(reference, candidate)
    if relation["normalized"]:
        score += 1
        evidence.append(f"extent_x{relation['scale']}")
    else:
        score -= 3
        evidence.append("extent_mismatch")
    return score, evidence


def align_sequences(reference: list[dict], candidate: list[dict], *,
                    max_offset: int = 16, gap_penalty: int = 3) -> dict:
    """Monotonic sequence alignment with a bounded ordinal prior.

    Returns pairs in order.  A pair is ``aligned`` only when the semantic score
    is strong (signature or scene evidence plus a normalized extent).  Ties and
    weak matches stay ``ambiguous`` so a pixel difference is not attributed to
    the renderer on the basis of a guessed correspondence.
    """
    ref = [row for row in reference if isinstance(row, dict)]
    cand = [row for row in candidate if isinstance(row, dict)]
    if not ref or not cand:
        return {
            "schema": SCHEMA,
            "status": MISSING,
            "pairs": [],
            "unmatched_reference": [row.get("frame_index") for row in ref],
            "unmatched_candidate": [row.get("frame_index") for row in cand],
            "reason": "one side has no observations",
        }
    ref0 = _int(ref[0].get("frame_index"))
    cand0 = _int(cand[0].get("frame_index"))
    rows = len(ref) + 1
    columns = len(cand) + 1
    score = [[0] * columns for _ in range(rows)]
    move = [[0] * columns for _ in range(rows)]  # 0 diag, 1 ref gap, 2 cand gap
    for i in range(1, rows):
        score[i][0] = score[i - 1][0] - gap_penalty
        move[i][0] = 1
    for j in range(1, columns):
        score[0][j] = score[0][j - 1] - gap_penalty
        move[0][j] = 2
    for i in range(1, rows):
        reference_row = ref[i - 1]
        ref_offset = _int(reference_row.get("frame_index")) - ref0
        for j in range(1, columns):
            candidate_row = cand[j - 1]
            cand_offset = _int(candidate_row.get("frame_index")) - cand0
            prior = abs(ref_offset - cand_offset) <= max_offset
            value, evidence = match_score(reference_row, candidate_row)
            if prior:
                value += 1
            else:
                value -= 1
            diagonal = score[i - 1][j - 1] + value
            up = score[i - 1][j] - gap_penalty
            left = score[i][j - 1] - gap_penalty
            best = max(diagonal, up, left)
            score[i][j] = best
            if best == diagonal:
                move[i][j] = 0
            elif best == up:
                move[i][j] = 1
            else:
                move[i][j] = 2
    pairs: list[dict] = []
    i, j = rows - 1, columns - 1
    while i > 0 and j > 0:
        direction = move[i][j]
        if direction == 0:
            reference_row = ref[i - 1]
            candidate_row = cand[j - 1]
            value, evidence = match_score(reference_row, candidate_row)
            relation = extent_relation(reference_row, candidate_row)
            if "scene_mismatch" in evidence:
                status = NOT_ALIGNED
            elif not relation["normalized"]:
                status = NOT_ALIGNED
            elif value >= 6 and (
                "caller_signature" in evidence or "scene" in evidence
            ):
                status = ALIGNED
            elif value >= 3:
                status = AMBIGUOUS
            else:
                status = AMBIGUOUS
            pairs.append({
                "reference_index": reference_row.get("frame_index"),
                "candidate_index": candidate_row.get("frame_index"),
                "status": status,
                "score": value,
                "evidence": evidence,
                "extent": relation,
            })
            i -= 1
            j -= 1
        elif direction == 1:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    aligned = sum(1 for pair in pairs if pair["status"] == ALIGNED)
    ambiguous = sum(1 for pair in pairs if pair["status"] == AMBIGUOUS)
    not_aligned = sum(1 for pair in pairs if pair["status"] == NOT_ALIGNED)
    paired_ref = {pair["reference_index"] for pair in pairs}
    paired_cand = {pair["candidate_index"] for pair in pairs}
    return {
        "schema": SCHEMA,
        "status": ALIGNED if aligned and not ambiguous and not not_aligned
        else (AMBIGUOUS if aligned or ambiguous else NOT_ALIGNED),
        "pairs": pairs,
        "aligned": aligned,
        "ambiguous": ambiguous,
        "not_aligned": not_aligned,
        "unmatched_reference": [
            row.get("frame_index") for row in ref
            if row.get("frame_index") not in paired_ref
        ],
        "unmatched_candidate": [
            row.get("frame_index") for row in cand
            if row.get("frame_index") not in paired_cand
        ],
        "policy": {
            "max_offset": max_offset,
            "gap_penalty": gap_penalty,
            "keys": ["scene", "caller_signature", "draw_count", "extent", "ordinal_prior"],
        },
    }


def load_observations(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with Path(path).open("r", encoding="utf-8") as source:
            for line in source:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return rows
    return rows
