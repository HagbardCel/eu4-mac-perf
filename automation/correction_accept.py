#!/usr/bin/env python3
"""Independent acceptance of a proposed renderer correction.

A ``correction_proposed`` pilot conclusion is a *claim*.  This module turns the
claim into an evidence decision: it takes the verified sealed case, the
recorded baseline, the candidate revision and the candidate replay output, and
recomputes the before/after pixel and checkpoint statistics from the validated
artifacts with the existing comparison and reconciliation machinery.  It never
trusts agent-supplied statistics and never reuses the baseline's recorded
result when the renderer executable or dependency closure changed.

Fail-closed rules:

* the case must pass the portable verifier and carry a sealed capture identity;
* the stored baseline must still describe the same case bytes and the same
  baseline replay artifacts;
* before and after must use the identical GL reference and event stream, and
  the candidate replay must name the same sealed capture;
* the candidate producer receipt must match the current replay build and the
  candidate revision must differ from the baseline revision;
* claimed statistics must equal the independently recomputed values exactly;
* every listed previously exact-matching regression must still recompute to
  zero mismatched pixels under the same candidate producer.

``pixel_perfect`` is only reported for zero mismatched pixels with zero maximum
channel error, complete qualification (verified replay identity, complete
full-frame execution, clean checkpoint reconciliation and complete captured
state coverage), exact authoritative checkpoint coverage, and a producer
receipt that binds the required checkpoint bytes.  ``authenticated_equivalence``
additionally requires every configured exact-match regression.

``observed_checkpoint_equality`` is narrower: it says the recomputed
authoritative bytes match, even if the producer receipt does not bind them.
``accepted`` only says the evidence is valid and the final image improved.  An
accepted partial improvement remains valuable, but it is not equivalence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from automation import (
    case_baseline,
    gfx_frame_graph,
    gfx_independent_replay,
    gfx_replay_case,
    m6_compare,
    m6_frame,
)

SCHEMA = "gfx-correction-acceptance/v1"
RESULTS = ("accepted", "rejected", "invalid_evidence")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "analysis" / "m6" / "comparison-policy.json"
DEFAULT_QUALIFICATION_CONTRACT = (
    ROOT / "analysis" / "m6" / "correction-qualification.json")
CONTRACT_SCHEMA = "gfx-correction-qualification/v1"
CANONICAL_READBACK = "metal-frame.bgra"

# Replay classifications that denote a completed comparison.  Anything else
# (incomplete_*, invalid_provenance, gpu_execution_failure, fault_experiment)
# is not a usable before/after observation.
COMPLETED_CLASSIFICATIONS = frozenset({
    "qualified_match",
    "qualified_difference",
    "difference_with_partial_state_coverage",
    "unsupported_state",
    # Legacy completed-difference wording still present in historical reports.
    "confirmed_rendering_difference",
    "partial_rendering_difference",
})


class AcceptanceError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _capture(seal: dict) -> dict:
    return {key: seal.get(key) for key in
            ("session_id", "capture_frame_id", "event_stream_sha256")}


def _valid_capture(value) -> bool:
    if not isinstance(value, dict):
        return False
    return (bool(value.get("session_id"))
            and value.get("capture_frame_id") is not None
            and bool(value.get("event_stream_sha256")))


def _statistics(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    mismatched = value.get("mismatched_pixels")
    maximum = value.get("max_channel_error")
    if (not isinstance(mismatched, int) or isinstance(mismatched, bool)
            or mismatched < 0):
        return None
    if (not isinstance(maximum, int) or isinstance(maximum, bool)
            or maximum < 0):
        return None
    return {
        "mismatched_pixels": mismatched,
        "max_channel_error": maximum,
        "pixels": value.get("pixels"),
        "mismatched_fraction": value.get("mismatched_fraction"),
        "alpha_different_pixels": value.get("alpha_different_pixels"),
    }


def _within_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def _returned_checkpoint_names(replay: dict) -> set[str]:
    """Validate returned checkpoint declarations before reconciliation.

    A producer artifact must not crash the independent checker or hide an
    unbound observation.  Every entry is an object with one unique safe
    basename.
    """
    returned = replay.get("checkpoints") or []
    if not isinstance(returned, list):
        raise AcceptanceError("replay_receipt_mismatch",
                              "replay checkpoints are not a list")
    names: set[str] = set()
    for item in returned:
        if not isinstance(item, dict):
            raise AcceptanceError("replay_receipt_mismatch",
                                  "replay has a malformed checkpoint entry")
        declared = str(item.get("file") or "")
        if (not declared or Path(declared).name != declared
                or declared in names):
            raise AcceptanceError(
                "replay_receipt_mismatch",
                f"replay checkpoint declares an invalid or duplicate file "
                f"name {declared!r}")
        names.add(declared)
    return names


def _replay_evidence(capture_dir: Path, replay_dir: Path,
                     policy: Path, *,
                     approved_recipes: set[str] | None = None) -> dict:
    """Recompute one side's execution, identity, pixels and checkpoints.

    Everything here is either validated from the artifacts or recomputed with
    the shared engines; no claimed result is copied in.
    """
    if not capture_dir.is_dir():
        raise AcceptanceError("capture_missing", str(capture_dir))
    if not replay_dir.is_dir():
        raise AcceptanceError("replay_missing", str(replay_dir))
    try:
        trace = m6_frame.load_trace(capture_dir, verify_blobs=True)
    except m6_frame.FrameError as exc:
        raise AcceptanceError("capture_invalid", str(exc)) from exc
    try:
        seal = m6_frame.load_seal(capture_dir, trace=trace)
    except m6_frame.FrameError as exc:
        raise AcceptanceError("seal_invalid", str(exc)) from exc
    replay = _json(replay_dir / "replay.json")
    if not replay:
        raise AcceptanceError("replay_unreadable",
                              str(replay_dir / "replay.json"))
    frame_block = (replay.get("frame")
                   if isinstance(replay.get("frame"), dict) else {})
    raw_name = str(frame_block.get("raw_file") or "")
    if raw_name != CANONICAL_READBACK:
        # The shared comparator reads frame.raw_file; the producer receipt
        # authenticates metal-frame.bgra.  Accepting an alternate path would
        # compare a file the receipt never bound.
        raise AcceptanceError(
            "unbound_readback",
            f"the replay declares readback {raw_name!r}; the producer receipt "
            f"authenticates {CANONICAL_READBACK!r}")
    shadow_path = replay_dir / "shadow-report.json"
    shadow = _json(shadow_path) if shadow_path.is_file() else None
    identity = gfx_independent_replay.identity_axis(
        seal, trace, replay, shadow, require_seal=True)
    execution_problems = gfx_independent_replay.full_frame_completion_problems(
        replay)
    policy_value = _json(policy)
    if not policy_value:
        raise AcceptanceError("policy_missing", str(policy))
    try:
        comparison = m6_compare.compare(capture_dir, replay_dir, policy_value)
    except (m6_compare.CompareError, OSError) as exc:
        raise AcceptanceError("pixels_unverified", str(exc)) from exc
    graph = gfx_frame_graph.build(capture_dir, trace=trace)
    # The published request is only a plan.  Reuse the independent replay's
    # graph-agreement and binding checks instead of reading the request file
    # directly, so an acceptance caller cannot skip them.
    request, request_source, request_problems, request_binding = (
        gfx_independent_replay.checkpoint_request_evidence(
            graph, replay_dir, shadow=shadow, seal=seal))
    invalid_requests = [item for item in request_problems
                        if item.get("severity") == "invalid"]
    if request_source == "published" and invalid_requests:
        # Refuse a malformed/disagreeing plan before reconciliation can touch
        # its entries; otherwise a bad request crashes instead of producing a
        # fail-closed report.
        raise AcceptanceError(
            "request_invalid",
            "; ".join(f"{item.get('kind')}: {item.get('detail')}"
                      for item in invalid_requests[:8]))
    returned_checkpoint_names = _returned_checkpoint_names(replay)
    capture = gfx_independent_replay.capture_identity(seal, trace, replay)
    checkpoints, validation = gfx_independent_replay.reconcile_checkpoints(
        graph, request, replay, capture_dir, replay_dir, capture)
    validation = dict(validation)
    if request_source == "derived":
        # A derived plan cannot be bound to the replay that was executed.
        validation["status"] = "incomplete"
        validation.setdefault("problems", []).append({
            "kind": "request_not_bound",
            "detail": "the replay did not publish its checkpoint request",
        })
    elif request_problems:
        # An unbound or legacy request is visible, not silently upgraded to a
        # complete qualification.
        validation["status"] = "incomplete"
        validation.setdefault("problems", []).extend(request_problems)
    states = gfx_independent_replay.state_coverage(capture_dir, trace=trace)
    receipt = _json(replay_dir / "producer-receipt.json")
    producer = receipt.get("producer") if isinstance(
        receipt.get("producer"), dict) else {}
    receipt_capture = receipt.get("capture") if isinstance(
        receipt.get("capture"), dict) else {}
    checkpoint_binding = "absent"
    recipe = None
    recipe_approved = False
    if receipt:
        for key in ("session_id", "capture_frame_id", "event_stream_sha256"):
            if str(receipt_capture.get(key)) != str(seal.get(key)):
                raise AcceptanceError(
                    "replay_capture_mismatch",
                    f"producer receipt capture.{key} does not match the "
                    "sealed case capture")
        raw_recipe = receipt.get("recipe")
        if raw_recipe is not None and not isinstance(raw_recipe, dict):
            raise AcceptanceError(
                "invalid_recipe",
                "producer receipt recipe is present but is not an object")
        recipe = raw_recipe if isinstance(raw_recipe, dict) else None
        recipe_approved = False
        if recipe is not None:
            if not isinstance(recipe.get("consumed_env"), dict):
                raise AcceptanceError(
                    "invalid_recipe",
                    "producer receipt consumed_env is not an object")
            if not isinstance(recipe.get("effective_env"), dict):
                raise AcceptanceError(
                    "invalid_recipe",
                    "producer receipt effective_env is not an object")
            if recipe.get("faults") is not None and not isinstance(
                    recipe.get("faults"), dict):
                raise AcceptanceError(
                    "invalid_recipe",
                    "producer receipt faults are not an object")
            if recipe.get("schema") != gfx_independent_replay.RECIPE_SCHEMA:
                raise AcceptanceError(
                    "invalid_recipe",
                    "producer receipt recipe schema is not the current "
                    "replay recipe")
            expected_effective = (
                gfx_independent_replay.effective_replay_environment(
                    recipe.get("consumed_env")))
            if recipe.get("effective_env") != expected_effective:
                raise AcceptanceError(
                    "invalid_recipe",
                    "producer receipt effective environment disagrees with "
                    "the consumed environment it records")
            if recipe.get("sha256") != gfx_independent_replay.recipe_digest(
                    recipe):
                raise AcceptanceError(
                    "invalid_recipe",
                    "producer receipt recipe digest does not match its "
                    "recorded inputs")
            expected_diagnostic = bool(
                recipe.get("faults")) or bool(recipe.get("effective_env"))
            if bool(recipe.get("diagnostic")) is not expected_diagnostic:
                raise AcceptanceError(
                    "invalid_recipe",
                    "producer receipt diagnostic flag disagrees with its "
                    "recorded effective inputs")
            if recipe.get("diagnostic") is True:
                digest = str(recipe.get("sha256") or "")
                if not digest or digest not in (approved_recipes or set()):
                    raise AcceptanceError(
                        "diagnostic_recipe",
                        "producer receipt records a diagnostic replay recipe; "
                        "a diagnostic variant is never correction evidence "
                        "unless its exact recipe is explicitly approved")
                recipe_approved = True
        result = receipt.get("result") if isinstance(
            receipt.get("result"), dict) else {}
        if result.get("replay_json_sha256") != _sha256(
                replay_dir / "replay.json"):
            raise AcceptanceError("replay_receipt_mismatch",
                                  "producer receipt does not bind replay.json")
        if result.get("metal_frame_sha256") != _sha256(
                replay_dir / "metal-frame.bgra"):
            raise AcceptanceError("replay_receipt_mismatch",
                                  "producer receipt does not bind the Metal "
                                  "readback")
        bound = result.get("checkpoint_files")
        if bound is None:
            # Receipts written before intermediate binding remain inspectable;
            # they cannot establish checkpoint exactness independently.
            checkpoint_binding = "absent"
        else:
            if not isinstance(bound, list):
                raise AcceptanceError(
                    "replay_receipt_mismatch",
                    "producer receipt checkpoint binding is not a list")
            bound_map: dict[str, str] = {}
            for row in bound:
                if not isinstance(row, dict):
                    raise AcceptanceError(
                        "replay_receipt_mismatch",
                        "producer receipt has a malformed checkpoint entry")
                name = str(row.get("file") or "")
                if (not name or Path(name).name != name
                        or name in bound_map):
                    raise AcceptanceError(
                        "replay_receipt_mismatch",
                        f"producer receipt has an invalid checkpoint file "
                        f"name {name!r}")
                bound_map[name] = str(row.get("sha256") or "")
            replay_names = returned_checkpoint_names
            if set(bound_map) != replay_names:
                missing = sorted(replay_names - set(bound_map))
                extra = sorted(set(bound_map) - replay_names)
                raise AcceptanceError(
                    "replay_receipt_mismatch",
                    "producer receipt does not bind exactly the returned "
                    f"checkpoints (missing={missing!r}, extra={extra!r})")
            for name, expected in bound_map.items():
                if _sha256(replay_dir / name) != expected:
                    raise AcceptanceError(
                        "replay_receipt_mismatch",
                        f"producer receipt does not bind checkpoint {name}")
            checkpoint_binding = "bound"
    return {
        "capture_dir": str(capture_dir),
        "replay_dir": str(replay_dir),
        "capture": capture,
        "seal": seal,
        "trace": trace,
        "replay": replay,
        "identity": identity,
        "execution_problems": execution_problems,
        "pixels": comparison.get("statistics"),
        "pixels_status": comparison.get("status"),
        "checkpoints": checkpoints,
        "checkpoint_validation": validation,
        "checkpoint_request": {
            "source": request_source,
            "binding": request_binding,
            "problems": request_problems,
            "required_entries": len(request.get("checkpoints") or []),
        },
        "graph_status": graph.get("status"),
        "state_coverage": states,
        "producer": producer,
        "recipe": recipe,
        "recipe_approved": recipe_approved,
        "receipt_present": bool(receipt),
        "checkpoint_binding": checkpoint_binding,
    }


def _checkpoint_authority(side: dict, *, allow_final_only: bool) -> dict:
    """Separate observed checkpoint equality from qualification coverage.

    Three questions are deliberately independent:

    * ``pixels_match`` -- did every authoritative observation recompute to
      zero differing pixels?
    * ``coverage`` -- were the required authoritative observations present and
      verified?  A diagnostic-only plan cannot establish equivalence, and an
      empty plan is exact only under an explicit final-only policy.
    * ``producer_binding`` -- does the producer receipt bind the intermediate
      bytes (reported by the caller, not inferred here)?
    """
    requested = [item for item in side["checkpoints"] if item.get("requested")]
    authoritative = [item for item in requested if item.get("authoritative")]
    diagnostic = [item for item in requested if not item.get("authoritative")]
    counts = {"match": 0, "divergent": 0, "divergent_diagnostic": 0,
              "invalid": 0}
    for item in authoritative:
        status = str(item.get("status") or "invalid")
        counts[status] = counts.get(status, 0) + 1
    for item in diagnostic:
        if str(item.get("status")) == "divergent_diagnostic":
            counts["divergent_diagnostic"] += 1
    first = next((item for item in authoritative
                  if item.get("status") != "match"), None)
    graph_status = str(side.get("graph_status") or "unknown")
    if counts["invalid"]:
        coverage = "invalid"
    elif counts["divergent"]:
        coverage = "divergent"
    elif graph_status != "complete":
        # No equivalence claim - final-only or checkpointed - is qualified on a
        # capture graph that itself reports incomplete coverage.
        coverage = "graph_incomplete"
    elif authoritative:
        coverage = "verified"
    elif requested:
        coverage = "diagnostic_only"
    else:
        required = int((side.get("checkpoint_request") or {}).get(
            "required_entries") or 0)
        if required:
            coverage = "missing_authoritative"
        else:
            coverage = "final_only" if allow_final_only else \
                "final_only_unqualified"
    if coverage == "final_only":
        pixels_match = None
    else:
        pixels_match = bool(authoritative) and not (
            counts["divergent"] or counts["invalid"])
    return {
        "requested": len(requested),
        "authoritative": len(authoritative),
        "diagnostic": len(diagnostic),
        "matched": counts["match"],
        "divergent": counts["divergent"],
        "invalid": counts["invalid"],
        "divergent_diagnostic": counts["divergent_diagnostic"],
        "pixels_match": pixels_match,
        "graph_status": graph_status,
        "coverage": coverage,
        "exact": coverage in ("verified", "final_only"),
        "final_only": coverage == "final_only",
        "first_difference": None if first is None else {
            "event": first.get("event"),
            "target": first.get("target"),
            "resource": first.get("resource"),
            "status": first.get("status"),
            "reason": first.get("reason"),
        },
    }


def _qualification_axes(side: dict, *, allow_final_only: bool) -> dict:
    identity_ok = side["identity"].get("status") == "verified"
    execution_ok = not side["execution_problems"]
    checkpoints_ok = side["checkpoint_validation"].get("status") == "ok"
    state_ok = side["state_coverage"].get("status") == "complete"
    checkpoints = _checkpoint_authority(
        side, allow_final_only=allow_final_only)
    binding = str(side.get("checkpoint_binding") or "absent")
    binding_required = checkpoints["authoritative"] > 0
    if checkpoints["coverage"] == "final_only":
        # There are no intermediate bytes to bind; the explicit final-only
        # policy is the qualification, not a producer receipt claim.
        producer_bound = True
    else:
        producer_bound = binding == "bound" and binding_required
    return {
        "replay_identity": "verified" if identity_ok else
                           str(side["identity"].get("status")),
        "full_frame_execution": "complete" if execution_ok else "incomplete",
        "checkpoint_reconciliation": str(
            side["checkpoint_validation"].get("status")),
        "authoritative_checkpoints": checkpoints,
        "checkpoint_pixels_match": checkpoints["pixels_match"],
        "checkpoint_coverage": checkpoints["coverage"],
        "authoritative_checkpoint_exact": checkpoints["exact"],
        "checkpoint_binding_required": binding_required,
        "checkpoint_producer_binding": binding,
        "checkpoint_producer_bound": producer_bound,
        "state_coverage": str(side["state_coverage"].get("status")),
        "complete": (identity_ok and execution_ok and checkpoints_ok
                     and state_ok and checkpoints["exact"]),
    }


def _claim(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    mismatched = value.get("mismatched_pixels")
    maximum = value.get("max_channel_error")
    if (not isinstance(mismatched, int) or isinstance(mismatched, bool)
            or mismatched < 0):
        return None
    if (not isinstance(maximum, int) or isinstance(maximum, bool)
            or maximum < 0):
        return None
    return {"mismatched_pixels": mismatched, "max_channel_error": maximum}


def _compare_claim(claimed: dict, recomputed: dict, problems: list[dict]) -> None:
    for key in ("mismatched_pixels", "max_channel_error"):
        if claimed.get(key) != recomputed.get(key):
            problems.append({
                "code": "claim_disagrees",
                "severity": "invalid",
                "detail": f"claimed {key}={claimed.get(key)!r} but the "
                          f"artifacts recompute {recomputed.get(key)!r}",
            })


def _improved(before: dict, after: dict) -> bool:
    if after["mismatched_pixels"] < before["mismatched_pixels"]:
        return True
    return (after["mismatched_pixels"] == before["mismatched_pixels"]
            and after["max_channel_error"] < before["max_channel_error"])


def _checkpoint_regressions(before: dict, after: dict) -> list[dict]:
    """Return authoritative checkpoints that matched before but not after.

    A final pixel improvement cannot hide damage at a boundary the baseline
    itself proved exact.  Checkpoints are paired by event and target inside one
    sealed capture; they are never compared across captures.
    """
    def rows(side: dict) -> dict:
        return {(item.get("event"), str(item.get("target"))): item
                for item in side["checkpoints"]
                if item.get("requested") and item.get("authoritative")}

    before_rows = rows(before)
    after_rows = rows(after)
    broken: list[dict] = []
    for key, row in before_rows.items():
        if row.get("status") != "match":
            continue
        candidate = after_rows.get(key)
        if candidate is None:
            broken.append({"event": key[0], "target": key[1],
                           "resource": row.get("resource"),
                           "status": "missing"})
        elif candidate.get("status") != "match":
            broken.append({"event": key[0], "target": key[1],
                           "resource": row.get("resource"),
                           "status": candidate.get("status")})
    return broken


def _baseline_problems(case_dir: Path, baseline: dict, before_dir: Path,
                       problems: list[dict]) -> dict:
    identity = baseline.get("identity") if isinstance(
        baseline.get("identity"), dict) else {}
    validity = identity.get("validity") if isinstance(
        identity.get("validity"), dict) else {}
    if baseline.get("schema") != case_baseline.SCHEMA:
        problems.append({"code": "baseline_schema", "severity": "invalid",
                         "detail": f"baseline schema must be "
                                   f"{case_baseline.SCHEMA}"})
    if identity.get("schema") != case_baseline.IDENTITY_SCHEMA:
        problems.append({"code": "baseline_identity_schema",
                         "severity": "invalid",
                         "detail": "the baseline has no identity"})
    if validity.get("verified") is not True:
        problems.append({"code": "baseline_not_verified",
                         "severity": "invalid",
                         "detail": "the baseline was not recorded from a "
                                   "verified case"})
    if validity.get("payload_validation") != "ok":
        problems.append({"code": "baseline_payloads_unvalidated",
                         "severity": "invalid",
                         "detail": "the baseline does not record on-disk "
                                   "payload validation"})
    manifest = _json(case_dir / "case.json")
    seal = _json(case_dir / "capture" / "frame-seal.json")
    if identity.get("case_manifest_sha256") != _sha256(
            case_dir / "case.json"):
        problems.append({"code": "baseline_case_changed",
                         "severity": "invalid",
                         "detail": "case.json changed since the baseline was "
                                   "recorded"})
    if identity.get("seal_sha256") != _sha256(
            case_dir / "capture" / "frame-seal.json"):
        problems.append({"code": "baseline_case_changed",
                         "severity": "invalid",
                         "detail": "the frame seal changed since the baseline "
                                   "was recorded"})
    if identity.get("replay_result_sha256") != _sha256(
            before_dir / "replay.json"):
        problems.append({"code": "baseline_replay_changed",
                         "severity": "invalid",
                         "detail": "the baseline replay.json no longer matches "
                                   "the recorded baseline"})
    if identity.get("metal_frame_sha256") != _sha256(
            before_dir / "metal-frame.bgra"):
        problems.append({"code": "baseline_replay_changed",
                         "severity": "invalid",
                         "detail": "the baseline Metal readback no longer "
                                   "matches the recorded baseline"})
    recorded_capture = identity.get("capture") if isinstance(
        identity.get("capture"), dict) else {}
    if (_valid_capture(recorded_capture)
            and _capture(recorded_capture) != _capture(seal)):
        problems.append({"code": "baseline_capture_mismatch",
                         "severity": "invalid",
                         "detail": "the baseline was recorded for a different "
                                   "sealed capture"})
    return {
        "fingerprint": identity.get("fingerprint"),
        "producer": identity.get("producer") or {},
        "capture": recorded_capture,
        "case_manifest": manifest.get("case_id"),
        "validity": validity,
    }


def _load_contract(value) -> tuple[dict, list[str]]:
    """Load and validate the configured qualification contract."""
    problems: list[str] = []
    if value is None:
        value = DEFAULT_QUALIFICATION_CONTRACT
    if isinstance(value, dict):
        contract = value
    else:
        path = Path(value).expanduser()
        try:
            contract = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return {}, [f"qualification contract unreadable: {path}: {exc}"]
        if not isinstance(contract, dict):
            return {}, [f"qualification contract is not an object: {path}"]
    if contract.get("schema") != CONTRACT_SCHEMA:
        problems.append(
            f"qualification contract schema must be {CONTRACT_SCHEMA}")
    required = contract.get("required_regressions")
    if not isinstance(required, list):
        problems.append(
            "qualification contract required_regressions must be a list")
        required = []
    cleaned: list[str] = []
    for item in required:
        if not isinstance(item, str) or not item.strip():
            problems.append(
                "qualification contract has an invalid regression id")
            continue
        if item in cleaned:
            problems.append(
                f"qualification contract repeats regression {item!r}")
        cleaned.append(item)
    allow_additional = contract.get("allow_additional", False)
    if not isinstance(allow_additional, bool):
        problems.append(
            "qualification contract allow_additional must be a boolean")
        allow_additional = False
    # A final-only case has no authoritative intermediate to bind.  It may be
    # treated as exact only when the operator's contract explicitly says so;
    # an absent plan is not silently promoted to equivalence.
    allow_final_only = contract.get("allow_final_only", False)
    if not isinstance(allow_final_only, bool):
        problems.append(
            "qualification contract allow_final_only must be a boolean")
        allow_final_only = False
    allow_no_exact_control = contract.get("allow_no_exact_control", False)
    if not isinstance(allow_no_exact_control, bool):
        problems.append(
            "qualification contract allow_no_exact_control must be a boolean")
        allow_no_exact_control = False
    # A diagnostic replay recipe (a fault, an inherited override or any other
    # effective non-default input) is never correction evidence on its own.
    # An operator may explicitly approve one exact recipe digest; approval is
    # by content, not by a receipt declaring itself canonical.
    approved = contract.get("approved_canonical_recipes", [])
    if not isinstance(approved, list):
        problems.append(
            "qualification contract approved_canonical_recipes must be a list")
        approved = []
    approved_recipes: list[str] = []
    for item in approved:
        if (not isinstance(item, str) or len(item) != 64
                or any(char not in "0123456789abcdef" for char in item)):
            problems.append(
                "qualification contract has an invalid approved recipe digest")
            continue
        if item in approved_recipes:
            problems.append(
                f"qualification contract repeats approved recipe {item!r}")
        approved_recipes.append(item)
    return {"schema": contract.get("schema"),
            "required_regressions": cleaned,
            "allow_additional": allow_additional,
            "allow_final_only": allow_final_only,
            "allow_no_exact_control": allow_no_exact_control,
            "approved_canonical_recipes": approved_recipes}, problems


def evaluate(*, case_dir: Path | str, baseline_path: Path | str,
             candidate_revision: str, after_replay: Path | str,
             claimed_before, claimed_after,
             baseline_revision: str | None = None,
             regressions=(), policy: Path | str | None = None,
             expected_revision: str | None = None,
             qualification_contract=None,
             claimed_regressions=()) -> dict:
    """Independently accept or reject one proposed correction.

    Under an active campaign, one evaluation atomically reserves the
    ``corrections`` allowance before any evidence is read.  The reservation is
    completed with the actual outcome; an exhausted allowance refuses further
    submissions through every caller, including the DSH validation path.
    """
    from automation import campaign_guard

    guard = campaign_guard.from_env()
    reservation = None
    if guard is not None:
        try:
            reservation = guard.reserve(
                {"corrections": 1},
                evidence={"candidate_revision": str(candidate_revision),
                          "case": str(case_dir),
                          "after_replay": str(after_replay)})
        except campaign_guard.CampaignRefusal as exc:
            raise AcceptanceError(
                "campaign_refused", f"{exc.kind}: {exc.detail}") from exc
    try:
        result = _evaluate(
            case_dir=case_dir, baseline_path=baseline_path,
            candidate_revision=candidate_revision,
            after_replay=after_replay, claimed_before=claimed_before,
            claimed_after=claimed_after, baseline_revision=baseline_revision,
            regressions=regressions, policy=policy,
            expected_revision=expected_revision,
            qualification_contract=qualification_contract,
            claimed_regressions=claimed_regressions)
    except BaseException as exc:  # noqa: BLE001 - record then propagate
        if guard is not None and reservation is not None:
            guard.complete(
                reservation, outcome="failed",
                detail=f"{type(exc).__name__}: {exc}",
                evidence={"candidate_revision": str(candidate_revision)})
        raise
    if guard is not None and reservation is not None:
        verdict = str(result.get("result") or "unknown")
        problems = [str(item.get("code"))
                    for item in (result.get("problems") or [])
                    if isinstance(item, dict)]
        accepted = verdict == "accepted"
        # Only an independently accepted correction is a successful attempt;
        # a rejection or invalid evidence is a failed attempt that keeps its
        # charge.  The audit record names the verdict and problem codes.
        guard.complete(
            reservation,
            outcome="ok" if accepted else "failed",
            detail=verdict,
            evidence={"candidate_revision": str(candidate_revision),
                      "result": verdict,
                      "accepted": accepted,
                      "problem_codes": problems,
                      "problem_count": len(problems)})
    return result


def _evaluate(*, case_dir: Path | str, baseline_path: Path | str,
              candidate_revision: str, after_replay: Path | str,
              claimed_before, claimed_after,
              baseline_revision: str | None = None,
              regressions=(), policy: Path | str | None = None,
              expected_revision: str | None = None,
              qualification_contract=None,
              claimed_regressions=()) -> dict:
    """Evaluate the correction evidence; the caller owns the correction budget."""
    case_dir = Path(case_dir).expanduser().resolve()
    baseline_path = Path(baseline_path).expanduser().resolve()
    after_dir = Path(after_replay).expanduser().resolve()
    policy_path = (Path(policy).expanduser().resolve() if policy is not None
                   else DEFAULT_POLICY)
    problems: list[dict] = []
    limitations: list[str] = []
    contract, contract_problems = _load_contract(qualification_contract)
    for detail in contract_problems:
        problems.append({"code": "qualification_contract",
                         "detail": detail, "severity": "invalid"})
    approved_recipes = set(contract.get("approved_canonical_recipes") or [])

    def problem(code: str, detail: str, *, severity: str = "rejected") -> None:
        problems.append({"code": code, "detail": detail,
                         "severity": severity})

    for label, path in (("case", case_dir), ("baseline", baseline_path),
                        ("after replay", after_dir)):
        if not _within_root(path):
            problem(f"{label.replace(' ', '_')}_outside_workspace",
                    f"{label} {path} is outside the repository root; "
                    "acceptance only reads workspace evidence", severity="invalid")

    if not case_dir.is_dir():
        raise AcceptanceError("case_missing", str(case_dir))
    try:
        verification = gfx_replay_case.verify(case_dir)
    except Exception as exc:  # noqa: BLE001 - every verifier failure is closed
        raise AcceptanceError("case_verification",
                              f"{type(exc).__name__}: {exc}") from exc
    if verification.get("ok") is not True:
        problem("case_not_verified",
                "; ".join(str(item) for item in
                          (verification.get("problems") or ["unknown"])[:8]),
                severity="invalid")
    seal = _json(case_dir / "capture" / "frame-seal.json")
    if not _valid_capture(_capture(seal)):
        problem("case_capture_identity",
                "the case has no sealed session/frame/event-stream identity",
                severity="invalid")

    baseline = _json(baseline_path)
    before_dir = case_dir / "replay"
    baseline_summary = _baseline_problems(case_dir, baseline, before_dir,
                                          problems)

    # The baseline's recorded result is never reused as the before statistic.
    try:
        before = _replay_evidence(
            case_dir / "capture", before_dir, policy_path,
            approved_recipes=approved_recipes)
    except AcceptanceError as exc:
        problem("before_unverified", str(exc), severity="invalid")
        before = None
    if before is not None:
        if _capture(before["capture"] or {}) != _capture(seal):
            problem("before_foreign_capture",
                    "the baseline replay does not name this sealed capture",
                    severity="invalid")
        if before["identity"].get("status") not in ("verified",
                                                    "unverified_session"):
            problem("before_identity",
                    f"baseline replay identity is "
                    f"{before['identity'].get('status')!r}", severity="invalid")
        if before["execution_problems"]:
            problem("before_incomplete",
                    "; ".join(before["execution_problems"]), severity="invalid")
        before_checkpoints = before["checkpoint_validation"].get("status")
        if before_checkpoints == "invalid":
            problem("before_checkpoints",
                    "baseline checkpoint reconciliation is invalid",
                    severity="invalid")
        elif before_checkpoints != "ok":
            limitations.append(
                "the baseline checkpoint reconciliation is "
                f"{before_checkpoints!r}; the before checkpoint statistics are "
                "reported as incomplete evidence")
        if before["receipt_present"] and before["recipe"] is None:
            limitations.append(
                "the baseline producer receipt has no replay recipe; ambient "
                "diagnostic overrides cannot be excluded from the before "
                "statistic")

    try:
        after = _replay_evidence(
            case_dir / "capture", after_dir, policy_path,
            approved_recipes=approved_recipes)
    except AcceptanceError as exc:
        problem("after_unverified", str(exc), severity="invalid")
        after = None
    if after is not None:
        if _capture(after["capture"] or {}) != _capture(seal):
            problem("after_foreign_capture",
                    "the candidate replay does not name this sealed capture",
                    severity="invalid")
        if after["identity"].get("status") not in ("verified",
                                                   "unverified_session"):
            problem("after_identity",
                    f"candidate replay identity is "
                    f"{after['identity'].get('status')!r}; the replay does not "
                    "belong to this sealed frame", severity="invalid")
        if after["execution_problems"]:
            problem("after_incomplete",
                    "; ".join(after["execution_problems"]), severity="invalid")
        if after["checkpoint_validation"].get("status") != "ok":
            problem("after_checkpoints",
                    "candidate checkpoint reconciliation is "
                    f"{after['checkpoint_validation'].get('status')!r}",
                    severity="invalid")
        if not after["receipt_present"]:
            problem("candidate_receipt_missing",
                    "the candidate replay has no producer receipt",
                    severity="invalid")
        else:
            if after["recipe"] is None:
                limitations.append(
                    "the candidate producer receipt has no replay recipe; "
                    "ambient diagnostic overrides cannot be excluded from the "
                    "candidate evidence")
            if after.get("recipe_approved"):
                limitations.append(
                    "the candidate replay uses an explicitly approved "
                    "non-canonical recipe; the approval is recorded, not "
                    "inferred")
            if after.get("checkpoint_binding") == "absent":
                limitations.append(
                    "the candidate producer receipt does not bind its "
                    "intermediate checkpoint files; checkpoint exactness is "
                    "recomputed from the sealed reference but not bound to the "
                    "producer")
            try:
                current = case_baseline.current_producer()
            except case_baseline.BaselineError as exc:
                problem("candidate_build_unavailable", str(exc),
                        severity="invalid")
                current = {}
            producer = after["producer"]
            for field in ("executable_sha256", "build_fingerprint"):
                if not current.get(field):
                    continue
                if producer.get(field) != current.get(field):
                    problem("candidate_build_stale",
                            f"candidate producer {field} does not match the "
                            f"current replay build ({producer.get(field)!r} != "
                            f"{current.get(field)!r})", severity="invalid")

    revision = str(candidate_revision or "").strip()
    if not revision:
        problem("candidate_revision_missing",
                "the candidate code revision is required", severity="invalid")
    if expected_revision is not None and revision != expected_revision:
        problem("candidate_revision_mismatch",
                f"candidate revision {revision!r} is not the expected "
                f"{expected_revision!r}", severity="invalid")
    if baseline_revision is not None and revision == str(baseline_revision):
        problem("candidate_unchanged",
                "the candidate revision equals the baseline revision; a "
                "correction requires a changed candidate", severity="invalid")
    if baseline_revision is None:
        limitations.append(
            "no baseline revision was supplied; a changed candidate revision "
            "cannot be proven from the artifacts alone")
    if after is not None:
        producer_revision = str(
            after["producer"].get("source_revision") or "").strip()
        if not producer_revision:
            problem("candidate_revision_unbound",
                    "the candidate producer receipt records no build revision; "
                    "the revision string is not evidence of executable origin",
                    severity="invalid")
        elif revision and producer_revision != revision:
            problem("candidate_revision_mismatch",
                    f"the claimed candidate revision {revision!r} is not the "
                    f"tested build revision {producer_revision!r}",
                    severity="invalid")

    recomputed_before = _statistics(before["pixels"]) if before else None
    recomputed_after = _statistics(after["pixels"]) if after else None
    if before is not None and recomputed_before is None:
        problem("before_statistics", "the baseline pixels could not be "
                "recomputed", severity="invalid")
    if after is not None and recomputed_after is None:
        problem("after_statistics", "the candidate pixels could not be "
                "recomputed", severity="invalid")

    claimed = {"before": _claim(claimed_before), "after": _claim(claimed_after)}
    for side in ("before", "after"):
        if claimed[side] is None:
            problem(f"claimed_{side}_missing",
                    f"the conclusion does not report {side} "
                    "mismatched_pixels and max_channel_error",
                    severity="invalid")
    if (claimed["before"] is not None and recomputed_before is not None):
        _compare_claim(claimed["before"], recomputed_before, problems)
    if claimed["after"] is not None and recomputed_after is not None:
        _compare_claim(claimed["after"], recomputed_after, problems)

    improved = False
    if recomputed_before is not None and recomputed_after is not None:
        improved = _improved(recomputed_before, recomputed_after)
        if not improved:
            problem("not_improved",
                    "the recomputed candidate does not improve any pixel "
                    "statistic over the baseline")
    if before is not None and after is not None:
        broken = _checkpoint_regressions(before, after)
        if broken:
            problem(
                "authoritative_checkpoint_regression",
                "the candidate no longer matches previously exact "
                "authoritative checkpoints: "
                + ", ".join(
                    f"event {row['event']} {row['resource']} "
                    f"({row['status']})" for row in broken[:8]))

    # The configured qualification contract defines the required exact-match
    # inventory.  The supplied evidence must map one-to-one onto it: an omitted,
    # substituted, duplicate or unrelated case is a rejection, and an empty
    # submission cannot bypass a non-empty requirement.
    required = list(contract.get("required_regressions") or [])
    allow_additional = bool(contract.get("allow_additional"))
    allow_final_only = bool(contract.get("allow_final_only"))
    allow_no_exact_control = bool(contract.get("allow_no_exact_control"))
    claimed_ids: list[str] = []
    for entry in claimed_regressions or ():
        value = entry
        if isinstance(entry, dict):
            value = entry.get("case_id") or entry.get("id") or entry.get("case")
        text = str(value or "").strip()
        if not text:
            problem("regression_claim_invalid",
                    "a claimed exact-match regression has no case identifier")
            continue
        if text in claimed_ids:
            problem("regression_claim_duplicate",
                    f"the conclusion claims regression {text!r} more than once")
            continue
        claimed_ids.append(text)
    if required and claimed_ids:
        if set(claimed_ids) != set(required):
            problem("regression_claim_mismatch",
                    "the conclusion's claimed regressions do not match the "
                    "qualification contract: claimed "
                    f"{sorted(claimed_ids)!r}, required {sorted(required)!r}",
                    severity="invalid")
    # The requirement is the contract when it names cases, otherwise the
    # candidate's own claim.  An empty submission never bypasses either.
    expected = required or claimed_ids
    # Snapshot primary evidence validity before regression execution adds its
    # own invalidity.  A broken regression rejects the correction but does not
    # change whether the candidate's own pixels/checkpoints are exact.
    primary_evidence_valid = not any(
        item.get("severity") == "invalid" for item in problems)
    regression_rows: list[dict] = []
    submitted_ids: list[str] = []
    candidate_producer = (after or {}).get("producer") or {}
    for position, entry in enumerate(regressions or ()):
        row = {"case": None, "case_id": None, "ok": False,
               "statistics": None, "producer_ok": False}
        if isinstance(entry, dict):
            case_value = entry.get("case") or entry.get("path")
            declared_id = entry.get("id") or entry.get("case_id")
        else:
            case_value, declared_id = entry, None
        path = Path(str(case_value or "")).expanduser()
        row["case"] = str(path)
        if not case_value:
            problem("regression_invalid",
                    f"regression entry {position} names no case",
                    severity="invalid")
            regression_rows.append(row)
            continue
        path = path.resolve()
        if not _within_root(path):
            problem("regression_outside_workspace",
                    f"regression {path} is outside the repository root",
                    severity="invalid")
            regression_rows.append(row)
            continue
        manifest = _json(path / "case.json")
        case_id = str(manifest.get("case_id") or declared_id or "").strip()
        if not case_id:
            problem("regression_invalid",
                    f"regression {path} has no case_id", severity="invalid")
            regression_rows.append(row)
            continue
        if case_id in submitted_ids:
            problem("regression_duplicate",
                    f"regression case {case_id!r} was submitted more than once",
                    severity="invalid")
            regression_rows.append(row)
            continue
        submitted_ids.append(case_id)
        row["case_id"] = case_id
        before_count = len(problems)
        try:
            verified = gfx_replay_case.verify(path)
            if verified.get("ok") is not True:
                problem("regression_unverified",
                        f"regression {case_id} no longer verifies",
                        severity="invalid")
            # Full-frame execution is established before any pixel result: a
            # failed replay with stale zero-mismatch pixels is not a pass.
            replay_document = _json(path / "replay" / "replay.json")
            execution_problems = (
                gfx_independent_replay.full_frame_completion_problems(
                    replay_document))
            if execution_problems:
                problem("regression_incomplete",
                        f"regression {case_id} is not a complete full-frame "
                        "execution: " + "; ".join(execution_problems),
                        severity="invalid")
                row["ok"] = False
                regression_rows.append(row)
                continue
            replay_side = _replay_evidence(
                path / "capture", path / "replay", policy_path,
                approved_recipes=approved_recipes)
            # Execution and provenance are established before pixels: a failed
            # replay with stale zero-mismatch pixels is not a passing
            # regression.
            if replay_side["execution_problems"]:
                problem("regression_incomplete",
                        f"regression {case_id} is not a complete full-frame "
                        "execution: "
                        + "; ".join(replay_side["execution_problems"]),
                        severity="invalid")
            if replay_side["identity"].get("status") not in (
                    "verified", "unverified_session"):
                problem("regression_identity",
                        f"regression {case_id} identity is "
                        f"{replay_side['identity'].get('status')!r}",
                        severity="invalid")
            # A regression is independently sealed: compare its replay against
            # its own capture identity, never the primary correction case's.
            regression_seal = _json(path / "capture" / "frame-seal.json")
            if (not _valid_capture(_capture(regression_seal))
                    or _capture(replay_side["capture"] or {})
                    != _capture(regression_seal)):
                problem("regression_foreign_capture",
                        f"regression {case_id} does not name its own verified "
                        "sealed capture", severity="invalid")
            if replay_side["checkpoint_validation"].get("status") != "ok":
                problem("regression_checkpoints",
                        f"regression {case_id} checkpoint reconciliation is "
                        f"{replay_side['checkpoint_validation'].get('status')!r}",
                        severity="invalid")
            if not replay_side["receipt_present"]:
                problem("regression_receipt_missing",
                        f"regression {case_id} has no producer receipt",
                        severity="invalid")
            row["producer_ok"] = (
                replay_side["producer"].get("executable_sha256")
                == candidate_producer.get("executable_sha256")
                and replay_side["producer"].get("build_fingerprint")
                == candidate_producer.get("build_fingerprint")
                and replay_side["producer"].get("source_revision")
                == candidate_producer.get("source_revision"))
            if not row["producer_ok"]:
                problem("regression_producer_mismatch",
                        f"regression {case_id} was not produced by the "
                        "candidate build", severity="invalid")
            statistics = _statistics(replay_side["pixels"])
            row["statistics"] = statistics
            checkpoint_axes = _qualification_axes(
                replay_side, allow_final_only=allow_final_only)
            row["checkpoints"] = checkpoint_axes["authoritative_checkpoints"]
            if not checkpoint_axes["authoritative_checkpoint_exact"]:
                problem("regression_checkpoints",
                        f"regression {case_id} changed an authoritative "
                        "checkpoint while its final image matched",
                        severity="invalid")
            if (checkpoint_axes["checkpoint_binding_required"]
                    and not checkpoint_axes["checkpoint_producer_bound"]):
                problem("regression_checkpoint_binding",
                        f"regression {case_id} matches pixels but its producer "
                        "receipt does not bind the required checkpoints",
                        severity="invalid")
            if statistics is None or statistics["mismatched_pixels"] != 0:
                problem("regression_failed",
                        f"regression {case_id} is not an exact match "
                        f"({None if statistics is None else statistics['mismatched_pixels']}"
                        " mismatched pixels)")
        except Exception as exc:  # noqa: BLE001 - isolate each regression
            problem("regression_unverified",
                    f"regression {case_id}: {type(exc).__name__}: {exc}",
                    severity="invalid")
        row["ok"] = len(problems) == before_count
        regression_rows.append(row)

    missing = [case_id for case_id in expected
               if case_id not in submitted_ids]
    unexpected = [case_id for case_id in submitted_ids
                  if case_id not in expected]
    if expected and not submitted_ids:
        problem("regression_missing",
                "required exact-match regressions "
                f"({', '.join(expected)}) were not supplied",
                severity="invalid")
    elif missing:
        problem("regression_missing",
                "required exact-match regressions are missing: "
                + ", ".join(missing), severity="invalid")
    if unexpected and not allow_additional:
        problem("regression_unexpected",
                "regressions not required by the contract or claimed by the "
                "conclusion: " + ", ".join(unexpected), severity="invalid")
    if not expected:
        limitations.append(
            "neither the qualification contract nor the conclusion requires "
            "an exact-match regression")

    axes = {"before": (_qualification_axes(
                 before, allow_final_only=allow_final_only)
                 if before else None),
            "after": (_qualification_axes(
                after, allow_final_only=allow_final_only)
                if after else None)}
    complete_qualification = bool(
        axes["after"] and axes["after"].get("complete"))
    authoritative_checkpoint_exact = bool(
        axes["after"] and axes["after"].get("authoritative_checkpoint_exact"))
    observed_checkpoint_equality = (
        axes["after"].get("checkpoint_pixels_match")
        if axes["after"] else None)
    producer_bound = bool(
        axes["after"] and axes["after"].get("checkpoint_producer_bound"))
    if axes["after"] and not axes["after"].get("complete"):
        coverage = axes["after"].get("checkpoint_coverage")
        if coverage == "graph_incomplete":
            limitations.append(
                "the capture graph is not complete; no checkpoint coverage or "
                "equivalence is qualified")
        elif coverage == "final_only_unqualified":
            graph_status = str(
                (axes["after"].get("authoritative_checkpoints") or {}).get(
                    "graph_status") or "unknown")
            if graph_status != "complete":
                limitations.append(
                    "the case has no authoritative checkpoints and its capture "
                    "graph is not complete; final-only equivalence requires a "
                    "complete capture graph")
            else:
                limitations.append(
                    "the case has no authoritative checkpoints; exactness "
                    "requires an explicit allow_final_only qualification "
                    "policy")
        elif coverage == "diagnostic_only":
            limitations.append(
                "all requested checkpoints are diagnostic-only; they cannot "
                "establish authoritative equivalence")
        else:
            limitations.append(
                "the candidate replay does not have complete qualification; a "
                "zero-pixel result is not equivalence")
    if axes["after"] and not authoritative_checkpoint_exact:
        limitations.append(
            "the candidate changed or was unable to verify an authoritative "
            "checkpoint; final equality cannot establish equivalence")
    if axes["after"] and not producer_bound:
        limitations.append(
            "the required checkpoint bytes are not bound to the producer "
            "receipt; observed equality is not authenticated equivalence")

    severities = {item["severity"] for item in problems}
    if "invalid" in severities:
        result = "invalid_evidence"
    elif problems:
        result = "rejected"
    else:
        result = "accepted"

    if expected:
        exact_control_policy = "required_inventory"
        regression_requirements_met = bool(
            not missing and (allow_additional or not unexpected)
            and set(expected).issubset(
                {row["case_id"] for row in regression_rows if row["ok"]}))
    elif allow_no_exact_control:
        exact_control_policy = "explicit_exception"
        regression_requirements_met = True
    else:
        exact_control_policy = "none"
        regression_requirements_met = False
        limitations.append(
            "no exact-match control inventory was required; authenticated "
            "equivalence is not certified (use allow_no_exact_control only as "
            "an explicit exception)")
    pixel_perfect = bool(
        primary_evidence_valid and complete_qualification
        and authoritative_checkpoint_exact and producer_bound
        and recomputed_after is not None
        and recomputed_after["mismatched_pixels"] == 0
        and recomputed_after["max_channel_error"] == 0)
    authenticated_equivalence = bool(
        pixel_perfect and regression_requirements_met
        and result == "accepted")
    if result == "accepted" and not improved:
        # Defensive: improvement is required for acceptance.
        result = "rejected"

    if after is not None and before is not None and (
            after["producer"] == before["producer"]):
        limitations.append(
            "the candidate and baseline replay binaries report the same "
            "producer identity; a native renderer rebuild is not demonstrated "
            "by the artifacts")

    return {
        "schema": SCHEMA,
        "ok": result == "accepted",
        "result": result,
        "pixel_perfect": pixel_perfect,
        "observed_checkpoint_equality": observed_checkpoint_equality,
        "producer_bound_checkpoint_evidence": producer_bound,
        "primary_evidence_valid": primary_evidence_valid,
        "authenticated_equivalence": authenticated_equivalence,
        "regression_requirements_met": regression_requirements_met,
        "exact_control_policy": exact_control_policy,
        "no_exact_control_exception": allow_no_exact_control,
        "verdict_semantics": {
            "accepted": (
                "valid evidence and a recomputed improvement; not necessarily "
                "exact and not necessarily producer-bound"),
            "observed_checkpoint_equality": (
                "every authoritative checkpoint recomputed to zero differing "
                "pixels; independent of the producer receipt"),
            "pixel_perfect": (
                "primary candidate evidence is exact: valid evidence, exact "
                "final image, complete qualification, exact checkpoint "
                "coverage and producer-bound required observations"),
            "authenticated_equivalence": (
                "pixel_perfect, an accepted result, and either every required "
                "exact-match regression verified or an explicit "
                "allow_no_exact_control exception; a broken regression can "
                "reject the correction while the primary image remains "
                "pixel_perfect"),
        },
        "improved": improved,
        "case": {
            "dir": str(case_dir),
            "case_id": _json(case_dir / "case.json").get("case_id"),
            "capture": _capture(seal),
            "verification": {
                "ok": verification.get("ok"),
                "independently_reproducible": verification.get(
                    "independently_reproducible"),
                "producer_identity": (verification.get(
                    "producer_identity") or {}).get("status"),
            },
        },
        "candidate": {
            "revision": revision or None,
            "expected_revision": expected_revision,
            "baseline_revision": baseline_revision,
            "producer": after["producer"] if after else None,
        },
        "baseline": baseline_summary,
        "before": (None if before is None else {
            "statistics": recomputed_before,
            "capture": before["capture"],
            "producer": before["producer"],
            "checkpoint_validation": before["checkpoint_validation"],
            "checkpoint_request": before["checkpoint_request"],
            "checkpoint_authority": (axes["before"] or {}).get(
                "authoritative_checkpoints"),
            "checkpoints": before["checkpoints"],
            "state_coverage": before["state_coverage"],
        }),
        "after": (None if after is None else {
            "statistics": recomputed_after,
            "capture": after["capture"],
            "producer": after["producer"],
            "checkpoint_validation": after["checkpoint_validation"],
            "checkpoint_request": after["checkpoint_request"],
            "checkpoint_authority": (axes["after"] or {}).get(
                "authoritative_checkpoints"),
            "checkpoints": after["checkpoints"],
            "state_coverage": after["state_coverage"],
        }),
        "claimed": claimed,
        "regressions": regression_rows,
        "regression_inventory": {
            "contract_required": required,
            "claimed": claimed_ids,
            "required": expected,
            "verified": [row["case_id"] for row in regression_rows
                         if row["ok"]],
            "submitted": submitted_ids,
            "missing": missing,
            "unexpected": unexpected,
            "allow_additional": allow_additional,
        },
        "qualification": axes,
        "authoritative_checkpoint_exact": authoritative_checkpoint_exact,
        "checkpoint_coverage": ((axes["after"] or {}).get(
            "checkpoint_coverage")),
        "checkpoint_producer_binding": ((axes["after"] or {}).get(
            "checkpoint_producer_binding")),
        "complete_qualification": complete_qualification,
        "problems": problems,
        "problems_count": len(problems),
        "limitations": limitations,
        "note": (
            "a valid correction_proposed claim is not an accepted correction "
            "until this report says accepted; pixel_perfect requires complete "
            "qualification, exact authoritative checkpoints and producer "
            "binding; authenticated_equivalence additionally requires every "
            "configured exact-match regression"),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--after-replay", required=True, type=Path)
    parser.add_argument("--candidate-revision", required=True)
    parser.add_argument("--baseline-revision", default=None)
    parser.add_argument("--expected-revision", default=None)
    parser.add_argument("--regression", action="append", default=[], type=Path)
    parser.add_argument("--claimed-regression", action="append", default=[])
    parser.add_argument("--qualification-contract", type=Path, default=None)
    parser.add_argument("--policy", type=Path, default=None)
    parser.add_argument("--before-mismatched-pixels", type=int, required=True)
    parser.add_argument("--before-max-channel-error", type=int, required=True)
    parser.add_argument("--after-mismatched-pixels", type=int, required=True)
    parser.add_argument("--after-max-channel-error", type=int, required=True)
    parser.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = evaluate(
            case_dir=args.case,
            baseline_path=args.baseline,
            candidate_revision=args.candidate_revision,
            after_replay=args.after_replay,
            claimed_before={
                "mismatched_pixels": args.before_mismatched_pixels,
                "max_channel_error": args.before_max_channel_error,
            },
            claimed_after={
                "mismatched_pixels": args.after_mismatched_pixels,
                "max_channel_error": args.after_max_channel_error,
            },
            baseline_revision=args.baseline_revision,
            regressions=args.regression,
            claimed_regressions=args.claimed_regression,
            qualification_contract=args.qualification_contract,
            policy=args.policy,
            expected_revision=args.expected_revision,
        )
    except AcceptanceError as exc:
        result = {"schema": SCHEMA, "ok": False, "result": "invalid_evidence",
                  "problems": [{"code": exc.kind, "detail": exc.detail,
                                "severity": "invalid"}],
                  "problems_count": 1}
        status = 1
    else:
        status = 0 if result["result"] == "accepted" else 2
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
