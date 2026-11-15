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
import re
from dataclasses import dataclass, field
from pathlib import Path

ALIGNED = "aligned"
AMBIGUOUS = "ambiguous"
NOT_ALIGNED = "not_aligned"
MISSING = "missing"
STATUSES = (ALIGNED, AMBIGUOUS, NOT_ALIGNED, MISSING)

SCHEMA = "gfx-frame-alignment/v1"


def _int(value, default: int = 0) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return default
    return default


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
    ref_update = _int(reference.get("update_identity"), 0)
    cand_update = _int(candidate.get("update_identity"), 0)
    if ref_update and cand_update:
        if ref_update == cand_update:
            score += 8
            evidence.append("game_update")
        else:
            return -100, ["game_update_mismatch"]
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
                    max_offset: int = 16, gap_penalty: int = 3,
                    draw_tolerance: float = 0.30) -> dict:
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
            # The DP score orders candidates; the authoritative pair decision
            # is the same predicate the pixel path uses.  A score threshold
            # must not promote a pair the pixel path would reject (or vice
            # versa) on identity/draw-workload grounds.
            correspondence = pair_correspondence(
                reference_row, candidate_row,
                draw_tolerance=draw_tolerance)
            pairs.append({
                "reference_index": reference_row.get("frame_index"),
                "candidate_index": candidate_row.get("frame_index"),
                "status": correspondence["status"],
                "reason": correspondence["reason"],
                "score": value,
                "evidence": evidence,
                "extent": correspondence["extent"],
                "correspondence": correspondence,
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
            "draw_tolerance": draw_tolerance,
            "keys": ["scene", "caller_signature", "draw_count", "extent", "ordinal_prior"],
            "final_status": "gfx_align.pair_correspondence",
        },
    }


STATICITY_SCHEMA = "gfx-scene-staticity/v1"
_DIGEST = re.compile(r"[0-9a-fA-F]{64}")


def _static_frame(item, *, expected_session: str | None) -> tuple[tuple[int, str] | None, list[str]]:
    """Validate one preserved frame record before it can prove staticity.

    A digest string on its own proves nothing: the record must carry a verified
    payload, consistent metadata and the campaign's own session.  Every problem
    is returned so a caller can explain why a role is not static.
    """
    problems: list[str] = []
    if not isinstance(item, dict):
        return None, ["frame record is not an object"]
    if item.get("valid") is not True:
        problems.append("frame dump is not marked valid")
    index = item.get("frame_index")
    if type(index) is not int or index <= 0:
        problems.append("frame index is absent or invalid")
    digest = item.get("sha256")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        problems.append("frame digest is absent or malformed")
        digest = ""
    else:
        digest = digest.lower()
    meta = item.get("meta")
    if not isinstance(meta, dict):
        problems.append("frame metadata is absent")
    else:
        if meta.get("schema") != "gfx-frame-dump/v1":
            problems.append("frame metadata schema is not a frame dump")
        if meta.get("frame_index") != index:
            problems.append("frame index disagrees with its metadata")
        meta_digest = meta.get("sha256")
        if not isinstance(meta_digest, str) or not _DIGEST.fullmatch(meta_digest):
            problems.append("metadata digest is absent or malformed")
        elif digest and meta_digest.lower() != digest:
            problems.append("frame digest disagrees with its metadata")
        width, height = meta.get("width"), meta.get("height")
        if (type(width) is not int or width <= 0
                or type(height) is not int or height <= 0):
            problems.append("frame extent is absent or invalid")
        if meta.get("capture_valid") is not True:
            problems.append("frame dump is not marked capture-valid")
        if meta.get("gpu_completed") is not True:
            problems.append("frame did not report GPU completion")
        for key in ("origin", "pixel_format", "raw_file"):
            value = meta.get(key)
            if not isinstance(value, str) or not value:
                problems.append(f"frame {key} is absent")
        raw_bytes = meta.get("raw_bytes")
        if type(raw_bytes) is not int:
            problems.append("payload size declaration is absent")
        elif (type(width) is int and width > 0
              and type(height) is int and height > 0
              and raw_bytes != width * height * 4):
            problems.append("declared payload size disagrees with the extent")
        if expected_session:
            session = meta.get("session")
            if not isinstance(session, str) or not session:
                problems.append("frame session is absent")
            elif session != expected_session:
                problems.append("frame belongs to a different session")
    if problems:
        return None, problems
    return (index, digest), []


def scene_staticity(dumps: dict | None, *, required: int, frames: dict,
                    session: str | None = None) -> dict:
    """Decide whether the *compared* frames come from a pixel-static scene.

    Alignment evidence (engine day, update identity, caller composition, draw
    count) identifies *which* logical frame was rendered, never whether the
    renderer output was stable.  This predicate therefore uses only verified
    full-resolution digests:

    * identity, scene and draw-count evidence is not accepted as staticity;
    * a dump must be valid, carry a matching payload declaration, extent,
      GPU-completion and the campaign session;
    * the identical-digest run containing the compared frame itself must reach
      ``required`` consecutive frames for each role.

    A gap, a conflicting duplicate index or an unrelated stable run elsewhere in
    the window does not qualify the pair.
    """
    try:
        required = int(required)
    except (TypeError, ValueError):
        required = 0
    required = max(2, required)
    roles: dict[str, dict] = {}
    verified_roles: list[str] = []
    for role in ("reference", "candidate"):
        payload = (dumps or {}).get(role)
        problems: list[str] = []
        captured = payload.get("captured") if isinstance(payload, dict) else None
        if captured is None:
            problems.append("no preserved frames for this role")
            captured = []
        by_index: dict[int, str] = {}
        conflicts: set[int] = set()
        for item in captured:
            parsed, item_problems = _static_frame(
                item, expected_session=session)
            if item_problems:
                problems.extend(item_problems)
                continue
            index, digest = parsed
            if index in by_index and by_index[index] != digest:
                conflicts.add(index)
                continue
            by_index.setdefault(index, digest)
        if conflicts:
            problems.append(
                "duplicate frame indices with conflicting digests: "
                + repr(sorted(conflicts)))
            for index in conflicts:
                by_index.pop(index, None)
        indices = sorted(by_index)
        runs: list[list[int]] = []
        for index in indices:
            if (runs and index == runs[-1][1] + 1
                    and by_index[index] == by_index[runs[-1][1]]):
                runs[-1][1] = index
            else:
                runs.append([index, index])
        max_streak = max((end - start + 1 for start, end in runs), default=0)
        pair_frame = frames.get(role) if isinstance(frames, dict) else None
        pair_local = 0
        if type(pair_frame) is not int or pair_frame <= 0:
            problems.append("compared frame index is not declared")
        elif pair_frame not in by_index:
            problems.append(
                f"compared frame {pair_frame} has no valid preserved frame")
        else:
            for start, end in runs:
                if start <= pair_frame <= end:
                    pair_local = end - start + 1
                    break
        if 0 < pair_local < required:
            problems.append(
                f"compared frame {pair_frame} is in a {pair_local}-frame stable "
                f"run; {required} consecutive frames are required")
        static = pair_local >= required and not problems
        roles[role] = {
            "frames": indices,
            "max_streak": max_streak,
            "pair_frame": pair_frame,
            "pair_local_streak": pair_local,
            "static": static,
            "problems": problems,
        }
        if static:
            verified_roles.append(role)
    verified = len(verified_roles) == len(roles)
    reason = "" if verified else "; ".join(
        f"{role}: {', '.join(item['problems']) or 'no stable run containing the compared frame'}"
        for role, item in roles.items())
    return {
        "schema": STATICITY_SCHEMA,
        "required_frames": required,
        "roles": roles,
        "verified": verified,
        "reason": reason,
    }


def pair_correspondence(reference: dict, candidate: dict, *,
                        draw_tolerance: float = 0.30) -> dict:
    """Single authoritative correspondence decision for one pair.

    Both the sequence aligner and the pixel comparison must use this decision.
    A shared engine update plus a comparable render workload remains valid
    evidence across backends with legitimately different draw batching; it is
    not a requirement for identical draw counts.  Everything weaker stays
    ambiguous so an observation is retained without renderer blame.
    """
    score, evidence = match_score(reference, candidate)
    relation = extent_relation(reference, candidate)
    reference_signature = _signature(reference)
    candidate_signature = _signature(candidate)
    same_draws = (
        type(reference.get("draws")) is int
        and reference.get("draws") == candidate.get("draws")
        and reference.get("draws") > 0
    )
    reference_identity = _int(reference.get("update_identity"), 0)
    candidate_identity = _int(candidate.get("update_identity"), 0)
    identity_equal = bool(
        reference_identity and reference_identity == candidate_identity)
    ref_draws = _int(reference.get("draws"), 0)
    cand_draws = _int(candidate.get("draws"), 0)
    draw_relation = (
        abs(ref_draws - cand_draws) / max(ref_draws, cand_draws)
        if ref_draws and cand_draws else 1.0
    )
    try:
        draw_tolerance = float(draw_tolerance)
    except (TypeError, ValueError):
        draw_tolerance = 0.30
    if "scene_mismatch" in evidence or "game_update_mismatch" in evidence:
        status = NOT_ALIGNED
        reason = "scene or engine update identity does not match"
    elif not relation["normalized"]:
        status = NOT_ALIGNED
        reason = "extent relation is not an integer logical/physical pair"
    elif (identity_equal and draw_relation <= draw_tolerance) or (
            same_draws and reference_signature
            and reference_signature == candidate_signature):
        status = ALIGNED
        reason = ""
    else:
        status = AMBIGUOUS
        if identity_equal and draw_relation > draw_tolerance:
            reason = (
                "same engine day but render workload differs "
                f"({draw_relation:.1%} > {draw_tolerance:.0%}); "
                "intra-day state is not established")
        else:
            reason = "alignment is ambiguous"
    return {
        "schema": "gfx-pair-correspondence/v1",
        "status": status,
        "reason": reason,
        "score": score,
        "evidence": evidence,
        "extent": relation,
        "draw_counts": [reference.get("draws"), candidate.get("draws")],
        "signatures": [reference_signature, candidate_signature],
        "identity_equal": identity_equal,
        "draw_relation": round(draw_relation, 6),
        "same_draws": same_draws,
        "draw_tolerance": draw_tolerance,
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
