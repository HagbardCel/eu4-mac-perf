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
channel error *and* a complete qualification: verified replay identity,
complete full-frame execution, clean checkpoint reconciliation and complete
captured-state coverage.  An accepted partial improvement is still valuable,
but it is not equivalence.
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


def _replay_evidence(capture_dir: Path, replay_dir: Path,
                     policy: Path) -> dict:
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
    request_path = replay_dir / "checkpoint-request.json"
    published = _json(request_path) if request_path.is_file() else None
    request_source = "published" if published is not None else "derived"
    request = published or gfx_frame_graph.checkpoint_request(graph)
    capture = gfx_independent_replay.capture_identity(seal, trace, replay)
    checkpoints, validation = gfx_independent_replay.reconcile_checkpoints(
        graph, request, replay, capture_dir, replay_dir, capture)
    if request_source == "derived":
        # A derived plan cannot be bound to the replay that was executed.
        validation = dict(validation)
        validation["status"] = "incomplete"
        validation.setdefault("problems", []).append({
            "kind": "request_not_bound",
            "detail": "the replay did not publish its checkpoint request",
        })
    states = gfx_independent_replay.state_coverage(capture_dir, trace=trace)
    receipt = _json(replay_dir / "producer-receipt.json")
    producer = receipt.get("producer") if isinstance(
        receipt.get("producer"), dict) else {}
    receipt_capture = receipt.get("capture") if isinstance(
        receipt.get("capture"), dict) else {}
    if receipt:
        for key in ("session_id", "capture_frame_id", "event_stream_sha256"):
            if str(receipt_capture.get(key)) != str(seal.get(key)):
                raise AcceptanceError(
                    "replay_capture_mismatch",
                    f"producer receipt capture.{key} does not match the "
                    "sealed case capture")
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
        "state_coverage": states,
        "producer": producer,
        "receipt_present": bool(receipt),
    }


def _qualification_axes(side: dict) -> dict:
    identity_ok = side["identity"].get("status") == "verified"
    execution_ok = not side["execution_problems"]
    checkpoints_ok = side["checkpoint_validation"].get("status") == "ok"
    state_ok = side["state_coverage"].get("status") == "complete"
    return {
        "replay_identity": "verified" if identity_ok else
                           str(side["identity"].get("status")),
        "full_frame_execution": "complete" if execution_ok else "incomplete",
        "checkpoint_reconciliation": str(
            side["checkpoint_validation"].get("status")),
        "state_coverage": str(side["state_coverage"].get("status")),
        "complete": (identity_ok and execution_ok and checkpoints_ok
                     and state_ok),
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
    """Load and validate the configured exact-match regression inventory."""
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
    return {"schema": contract.get("schema"),
            "required_regressions": cleaned,
            "allow_additional": allow_additional}, problems


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
        guard.complete(
            reservation, outcome="ok",
            detail=str(result.get("result")),
            evidence={"candidate_revision": str(candidate_revision),
                      "result": result.get("result")})
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
        before = _replay_evidence(case_dir / "capture", before_dir,
                                  policy_path)
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

    try:
        after = _replay_evidence(case_dir / "capture", after_dir, policy_path)
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

    # The configured qualification contract defines the required exact-match
    # inventory.  The supplied evidence must map one-to-one onto it: an omitted,
    # substituted, duplicate or unrelated case is a rejection, and an empty
    # submission cannot bypass a non-empty requirement.
    required = list(contract.get("required_regressions") or [])
    allow_additional = bool(contract.get("allow_additional"))
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
            replay_side = _replay_evidence(path / "capture",
                                           path / "replay", policy_path)
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

    axes = {"before": _qualification_axes(before) if before else None,
            "after": _qualification_axes(after) if after else None}
    complete_qualification = bool(
        axes["after"] and axes["after"].get("complete"))
    if axes["after"] and not axes["after"].get("complete"):
        limitations.append(
            "the candidate replay does not have complete qualification; a "
            "zero-pixel result is not equivalence")

    severities = {item["severity"] for item in problems}
    if "invalid" in severities:
        result = "invalid_evidence"
    elif problems:
        result = "rejected"
    else:
        result = "accepted"

    pixel_perfect = bool(
        result == "accepted" and complete_qualification
        and recomputed_after is not None
        and recomputed_after["mismatched_pixels"] == 0
        and recomputed_after["max_channel_error"] == 0)
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
            "checkpoints": before["checkpoints"],
            "state_coverage": before["state_coverage"],
        }),
        "after": (None if after is None else {
            "statistics": recomputed_after,
            "capture": after["capture"],
            "producer": after["producer"],
            "checkpoint_validation": after["checkpoint_validation"],
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
        "complete_qualification": complete_qualification,
        "problems": problems,
        "problems_count": len(problems),
        "limitations": limitations,
        "note": (
            "a valid correction_proposed claim is not an accepted correction "
            "until this report says accepted; pixel equivalence additionally "
            "requires complete qualification and zero differing pixels"),
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
