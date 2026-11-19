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


def evaluate(*, case_dir: Path | str, baseline_path: Path | str,
             candidate_revision: str, after_replay: Path | str,
             claimed_before, claimed_after,
             baseline_revision: str | None = None,
             regressions=(), policy: Path | str | None = None,
             expected_revision: str | None = None) -> dict:
    """Independently accept or reject one proposed correction."""
    case_dir = Path(case_dir).expanduser().resolve()
    baseline_path = Path(baseline_path).expanduser().resolve()
    after_dir = Path(after_replay).expanduser().resolve()
    policy_path = (Path(policy).expanduser().resolve() if policy is not None
                   else DEFAULT_POLICY)
    problems: list[dict] = []
    limitations: list[str] = []

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

    # Every previously exact-matching regression must still be exact under the
    # same candidate producer, recomputed from its own artifacts.
    regression_rows: list[dict] = []
    candidate_producer = (after or {}).get("producer") or {}
    for position, entry in enumerate(regressions or ()):
        row = {"case": None, "ok": False, "statistics": None,
               "producer_ok": False}
        path = Path(entry).expanduser().resolve() if not isinstance(
            entry, dict) else Path(str(entry.get("case") or "")).expanduser().resolve()
        row["case"] = str(path)
        if not _within_root(path):
            problem("regression_outside_workspace",
                    f"regression {path} is outside the repository root",
                    severity="invalid")
        try:
            verified = gfx_replay_case.verify(path)
            if verified.get("ok") is not True:
                problem("regression_unverified",
                        f"regression {path} no longer verifies")
            replay_side = _replay_evidence(path / "capture",
                                           path / "replay", policy_path)
            statistics = _statistics(replay_side["pixels"])
            row["statistics"] = statistics
            row["producer_ok"] = (
                replay_side["producer"].get("executable_sha256")
                == candidate_producer.get("executable_sha256")
                and replay_side["producer"].get("build_fingerprint")
                == candidate_producer.get("build_fingerprint"))
            if not row["producer_ok"]:
                problem("regression_producer_mismatch",
                        f"regression {path} was not produced by the candidate "
                        "build")
            if statistics is None or statistics["mismatched_pixels"] != 0:
                problem("regression_failed",
                        f"regression {path} is not an exact match "
                        f"({None if statistics is None else statistics['mismatched_pixels']} "
                        "mismatched pixels)")
            else:
                row["ok"] = True
        except Exception as exc:  # noqa: BLE001 - isolate each regression
            problem("regression_unverified",
                    f"regression {path}: {type(exc).__name__}: {exc}")
        regression_rows.append(row)

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

    if not regression_rows:
        limitations.append("no exact-match regression cases were listed")
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
