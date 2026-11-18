"""Validate one autonomous-diagnosis pilot conclusion, fail-closed.

M7-P1 runs a real DSH model session against an immutable sealed case.  The
session must end with a machine-readable conclusion, and that conclusion is
evidence only if its claims are bounded by what the case can actually prove.

Three checks are deliberately strict:

* a pilot must have *executed* at least one of the supported native EU4 tools
  (the model request's advertised tool list is not proof);
* a claimed causal interval must be supported by one authoritative matching
  lower checkpoint and one authoritative divergent upper checkpoint on the same
  resource, in the same sealed capture, with no invented or contradictory
  observations;
* the run summary must bind to the verified case receipt and its capture
  identity, and the EU4 launch count must be present and exactly zero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCHEMA = "gfx-pilot-conclusion/v1"
VALIDATION_SCHEMA = "gfx-pilot-validation/v1"
GATE_SCHEMA = "gfx-pilot-native-gate/v1"
CASE_RECEIPT_SCHEMA = "gfx-pilot-case-verification-receipt/v1"
OUTCOMES = ("correction_proposed", "requires_additional_evidence")
HYPOTHESIS_STATUS = ("supported", "refuted", "insufficient")
FRAME_SCOPES = ("same_sealed_frame", "new_capture")
INTERVAL_STATUS = ("match", "divergent")
MAX_HYPOTHESES = 3


def _native_tool_names() -> frozenset[str]:
    """The seven native EU4 tools, from their single existing definition."""
    from automation.agent_tools import TOOLS

    return frozenset(TOOLS)


def _capture_key(value) -> tuple[str, str, str] | None:
    """Identity tuple for one capture; unknown identity is not a wildcard."""
    if not isinstance(value, dict):
        return None
    parts = (value.get("session_id"), value.get("capture_frame_id"),
             value.get("event_stream_sha256"))
    if any(part is None or str(part) == "" for part in parts):
        return None
    return tuple(str(part) for part in parts)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _resource(value) -> bool:
    if not isinstance(value, str):
        return False
    return value == "fb:0" or (
        value.startswith("tex:") and value[4:].isdigit())


def _target_for(resource: str) -> str:
    return "0" if resource == "fb:0" else resource.split(":", 1)[1]


def _statistics(value) -> bool:
    return (isinstance(value, dict)
            and _integer(value.get("mismatched_pixels"))
            and _integer(value.get("max_channel_error")))


def _valid_basis(interval: dict, problems: list[dict], *,
                 sealed_frame_key: tuple[str, str, str] | None,
                 expected_resource: str | None = None) -> None:
    """Validate a claimed causal interval against the diagnostic contract.

    The diagnostic contract represents an interval as the last matching
    observation of one resource and the first divergent observation of that
    same resource.  A texture match never bounds a framebuffer divergence, and
    observations from two captures never bound each other.  An unbounded
    interval is represented by a null ``start_event`` and only the divergent
    upper observation; no lower checkpoint is invented.
    """

    def bad(code: str, detail: str) -> None:
        problems.append({"code": code, "detail": detail})

    start = interval.get("start_event")
    end = interval.get("end_event")
    lower_bound = interval.get("lower_bound")
    bounded = start is not None

    if not _integer(end):
        bad("interval_invalid", "end_event must be a non-negative integer")
    if bounded and not _integer(start):
        bad("interval_invalid", "start_event must be null or an integer")
    if (bounded and _integer(start) and _integer(end) and start >= end):
        bad("interval_invalid", "start_event must precede end_event")
    if interval.get("same_capture") is not True:
        bad("interval_not_same_capture",
            "same_capture must be true for a claimed interval")

    basis = interval.get("basis")
    if not isinstance(basis, list) or not basis:
        bad("interval_without_basis",
            "a causal interval needs checkpoint basis")
        return

    entries: list[dict] = []
    for position, item in enumerate(basis):
        if not isinstance(item, dict):
            bad("interval_basis_invalid",
                f"basis entry {position} is not an object")
            continue
        if not _integer(item.get("event")):
            bad("interval_basis_invalid",
                f"basis entry {position} has no non-negative event")
        if not _resource(item.get("resource")):
            bad("interval_basis_invalid",
                f"basis entry {position} has no fb:0/tex:N resource")
            continue
        resource = str(item["resource"])
        if "target" not in item or str(item.get("target")) != _target_for(
                resource):
            bad("interval_basis_target",
                f"basis entry {position} target does not match {resource}")
        status = item.get("status")
        if status not in INTERVAL_STATUS:
            bad("interval_basis_status",
                f"basis entry {position} status must be match or divergent")
        if item.get("authoritative") is not True:
            bad("interval_basis_not_authoritative",
                f"basis entry {position} is not authoritative")
        if _capture_key(item.get("capture")) is None:
            bad("interval_basis_unbound",
                f"basis entry {position} has no capture identity")
        entries.append(item)

    keys = {key for key in (_capture_key(item.get("capture"))
                            for item in entries)
            if key is not None}
    if len(keys) > 1:
        bad("cross_capture_interval",
            "basis checkpoints come from different captures; an interval may "
            "only use observations from one sealed frame")
    if sealed_frame_key is not None and keys and keys - {sealed_frame_key}:
        bad("cross_capture_interval",
            "causal interval basis does not match the conclusion's sealed "
            "frame")

    resources = {str(item.get("resource")) for item in entries
                 if _resource(item.get("resource"))}
    if len(resources) > 1:
        bad("mixed_resource_interval",
            "lower and upper observations must concern the same resource")
    if expected_resource and resources and resources != {expected_resource}:
        bad("interval_resource_mismatch",
            "the interval resource does not match starting_diagnosis "
            "first_divergence.resource")

    events = [item.get("event") for item in entries]
    if len(set(events)) != len(events):
        bad("interval_duplicate",
            "the interval basis repeats an event observation")

    if not bounded:
        if lower_bound not in (None, "unbounded"):
            bad("interval_invalid",
                "an unbounded interval must declare lower_bound=unbounded")
        if lower_bound == "same_resource_match":
            bad("unresolved_interval_claimed",
                "an unresolved interval must not claim a matching lower bound")
        upper = [item for item in entries if item.get("event") == end]
        if len(entries) != 1 or len(upper) != 1:
            bad("interval_upper_endpoint",
                "an unbounded interval needs exactly the divergent upper "
                "observation and no invented lower checkpoint")
        elif upper[0].get("status") != "divergent":
            bad("interval_upper_status",
                "the upper observation must be divergent")
        return

    if lower_bound not in (None, "same_resource_match"):
        bad("interval_invalid",
            "a bounded interval must declare lower_bound=same_resource_match")
    if len(entries) != 2:
        bad("interval_basis_cardinality",
            "a bounded interval needs exactly two endpoint observations")
    lower = [item for item in entries if item.get("event") == start]
    upper = [item for item in entries if item.get("event") == end]
    if len(lower) != 1:
        bad("interval_lower_endpoint",
            "no unique observation at start_event")
    if len(upper) != 1:
        bad("interval_upper_endpoint",
            "no unique observation at end_event")
    if len(lower) == 1 and len(upper) == 1:
        if lower[0].get("status") != "match":
            bad("interval_lower_status",
                "the lower observation must be an authoritative match")
        if upper[0].get("status") != "divergent":
            bad("interval_upper_status",
                "the upper observation must be an authoritative divergence")
        lower_generation = lower[0].get("generation")
        upper_generation = upper[0].get("generation")
        if (lower_generation is None) != (upper_generation is None):
            bad("interval_generation_incomplete",
                "resource generation is present on only one endpoint")
        elif (lower_generation is not None
              and lower_generation != upper_generation):
            bad("interval_generation_mismatch",
                "endpoint observations belong to different resource "
                "generations")


def validate_conclusion(value, *, authoritative: dict | None = None,
                        case_digests: dict | None = None) -> dict:
    problems: list[dict] = []

    def problem(code: str, detail: str) -> None:
        problems.append({"code": code, "detail": detail})

    if not isinstance(value, dict):
        return {"schema": VALIDATION_SCHEMA, "ok": False,
                "problems": [{"code": "not_an_object",
                              "detail": "conclusion must be a JSON object"}],
                "checks": {}}
    if value.get("schema") != SCHEMA:
        problem("schema", f"schema must be {SCHEMA}")

    case = value.get("case") if isinstance(value.get("case"), dict) else {}
    if not _text(case.get("case_id")):
        problem("case", "case.case_id is required")
    verification_ok = (case.get("verification_ok") is True
                       or case.get("case_verification") == "ok")
    if not verification_ok:
        problem("case_verification",
                "the case must have verified before any conclusion")
    sealed_frame = case.get("sealed_frame")
    case_frame_key = _capture_key(sealed_frame)
    if case_frame_key is None:
        problem("case_sealed_frame",
                "case.sealed_frame must name session_id, capture_frame_id and "
                "event_stream_sha256")
    if case_digests is not None:
        if case.get("case_manifest_sha256") != case_digests.get(
                "case_manifest_sha256"):
            problem("case_manifest_mismatch",
                    "case.case_manifest_sha256 does not match the verified "
                    "case manifest")
        if case.get("seal_sha256") != case_digests.get("seal_sha256"):
            problem("case_seal_mismatch",
                    "case.seal_sha256 does not match the verified frame seal")

    diagnosis = (value.get("starting_diagnosis")
                 if isinstance(value.get("starting_diagnosis"), dict) else {})
    if not _text(diagnosis.get("classification")):
        problem("starting_diagnosis",
                "starting_diagnosis.classification is required")
    first = (diagnosis.get("first_divergence")
             if isinstance(diagnosis.get("first_divergence"), dict) else {})
    if not _integer(first.get("event")):
        problem("starting_diagnosis",
                "starting_diagnosis.first_divergence.event is required")
    if not _text(first.get("resource")):
        problem("starting_diagnosis",
                "starting_diagnosis.first_divergence.resource is required")
    if "root_cause" not in diagnosis:
        problem("starting_diagnosis",
                "starting_diagnosis.root_cause is required (use "
                "'not_established' when unknown)")

    hypotheses = value.get("hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses:
        problem("hypotheses", "at least one hypothesis is required")
        hypotheses = []
    if len(hypotheses) > MAX_HYPOTHESES:
        problem("hypothesis_budget",
                f"at most {MAX_HYPOTHESES} hypotheses are allowed by the pilot")
    statuses = []
    for position, item in enumerate(hypotheses):
        if not isinstance(item, dict):
            problem("hypothesis_invalid", f"hypothesis {position} is not an object")
            continue
        if not _text(item.get("statement")):
            problem("hypothesis_invalid",
                    f"hypothesis {position} has no statement")
        status = item.get("status")
        if status not in HYPOTHESIS_STATUS:
            problem("hypothesis_status",
                    f"hypothesis {position} status must be one of "
                    f"{', '.join(HYPOTHESIS_STATUS)}")
        else:
            statuses.append(status)
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            problem("hypothesis_evidence",
                    f"hypothesis {position} needs concrete evidence")
        test = item.get("test")
        if not isinstance(test, dict) or not _text(test.get("method")):
            problem("hypothesis_test",
                    f"hypothesis {position} needs the discriminating test used")

    outcome = value.get("outcome")
    if outcome not in OUTCOMES:
        problem("outcome", f"outcome must be one of {', '.join(OUTCOMES)}")

    uncertainty = value.get("remaining_uncertainty")
    if not isinstance(uncertainty, list) or not uncertainty:
        problem("remaining_uncertainty",
                "remaining_uncertainty must be an explicit non-empty list")
    if not _text(value.get("next_experiment")):
        problem("next_experiment", "next_experiment is required")

    interval = value.get("causal_interval")
    if interval is not None:
        if not isinstance(interval, dict):
            problem("causal_interval", "causal_interval must be an object")
        else:
            expected_resource = (first.get("resource")
                                 if _resource(first.get("resource")) else None)
            _valid_basis(interval, problems,
                         sealed_frame_key=case_frame_key,
                         expected_resource=expected_resource)
            _verify_interval_evidence(interval, authoritative, problems,
                                      case_frame_key)
            if outcome == "requires_additional_evidence":
                if interval.get("start_event") is not None or (
                        interval.get("lower_bound") == "same_resource_match"):
                    problem("unresolved_interval_claimed",
                            "an unresolved pilot must not claim a resolved "
                            "lower bound")

    if outcome == "correction_proposed":
        if "supported" not in statuses:
            problem("correction_without_supported_hypothesis",
                    "a proposed correction needs a supported hypothesis")
        correction = (value.get("correction")
                      if isinstance(value.get("correction"), dict) else {})
        files = correction.get("files")
        if not isinstance(files, list) or not files or not all(
                _text(item) for item in files):
            problem("correction_files", "correction.files must list edited files")
        elif any(str(item).startswith(("/", "~")) or ".." in str(item)
                 for item in files):
            problem("correction_files",
                    "correction.files must be repo-relative, not absolute")
        if not _text(correction.get("description")):
            problem("correction_description",
                    "correction.description is required")
        validation = (correction.get("validation")
                      if isinstance(correction.get("validation"), dict) else {})
        if validation.get("same_sealed_case") is not True:
            problem("correction_not_same_case",
                    "a correction must be replayed on the identical sealed case")
        before = validation.get("before")
        after = validation.get("after")
        if not _statistics(before) or not _statistics(after):
            problem("correction_statistics",
                    "before/after mismatched_pixels and max_channel_error are "
                    "required")
        else:
            improved = (after["mismatched_pixels"] < before["mismatched_pixels"]
                        or (after["mismatched_pixels"]
                            == before["mismatched_pixels"]
                            and after["max_channel_error"]
                            < before["max_channel_error"]))
            if not improved:
                problem("correction_not_improving",
                        "a correction must improve at least one pixel statistic")
        if not _text(validation.get("replay_artifact")):
            problem("correction_replay_artifact",
                    "the replay artifact for the after-state is required")
        regressions = validation.get("exact_match_regressions")
        if not isinstance(regressions, list) or not regressions:
            problem("correction_regressions",
                    "exact-match regressions must be listed and preserved")
        else:
            for entry in regressions:
                if not isinstance(entry, dict) or not _text(entry.get("case")) \
                        or entry.get("mismatched_pixels") != 0:
                    problem("correction_regressions",
                            "every exact-match regression must report zero "
                            "mismatched pixels")
    elif "correction" in value:
        problem("correction_without_outcome",
                "correction is only allowed with outcome=correction_proposed")

    if outcome == "requires_additional_evidence":
        request = (value.get("evidence_request")
                   if isinstance(value.get("evidence_request"), dict) else {})
        if not _text(request.get("reason")):
            problem("evidence_request",
                    "evidence_request.reason must say why existing evidence is "
                    "insufficient")
        scope = request.get("frame_scope")
        if scope not in FRAME_SCOPES:
            problem("evidence_request_scope",
                    f"evidence_request.frame_scope must be one of "
                    f"{', '.join(FRAME_SCOPES)}")
        checkpoints = request.get("checkpoints")
        if not isinstance(checkpoints, list) or not checkpoints:
            problem("evidence_request_checkpoints",
                    "evidence_request must name at least one checkpoint")
            checkpoints = []
        for entry in checkpoints:
            if not isinstance(entry, dict):
                problem("evidence_request_checkpoints",
                        "checkpoint request entry is not an object")
                continue
            if not _integer(entry.get("event")):
                problem("evidence_request_checkpoints",
                        "checkpoint request needs a non-negative event")
            if not _resource(entry.get("resource")):
                problem("evidence_request_checkpoints",
                        "checkpoint request needs an fb:0/tex:N resource")
            if not _text(entry.get("why")):
                problem("evidence_request_checkpoints",
                        "checkpoint request needs a reason")
        if scope == "new_capture" and not _text(
                request.get("cross_frame_caveat")):
            problem("evidence_request_cross_frame",
                    "a new capture must state that it cannot bound the "
                    "existing frame without proven equivalent starting state")

    return {
        "schema": VALIDATION_SCHEMA,
        "ok": not problems,
        "problems": problems,
        "problems_count": len(problems),
        "checks": {
            "case_id": case.get("case_id"),
            "sealed_frame": sealed_frame if case_frame_key else None,
            "outcome": outcome,
            "hypotheses": len(hypotheses),
            "has_interval": interval is not None,
            "correction": "correction" in value,
            "evidence_request": "evidence_request" in value,
        },
    }


def read_run_events(path: Path | str) -> list[dict]:
    """Read the launcher's NDJSON run-event stream."""
    events = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def executed_tools(events: list[dict]) -> dict:
    """Pair actual tool calls with their results.

    A call is *executed* only when a matching result reports completion; an
    advertised tool list, a call without a result, or a failed call is not
    evidence of integration.
    """
    calls = []
    results: dict[str, str] = {}
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        kind = event.get("type")
        if kind == "tool_call":
            call_id = event.get("callId") or data.get("callId")
            name = event.get("tool") or data.get("tool")
            if call_id and name:
                calls.append({"call_id": str(call_id), "name": str(name)})
        elif kind == "tool_result":
            call_id = (event.get("callId") or data.get("toolCallId")
                       or data.get("callId"))
            if call_id:
                results[str(call_id)] = str(
                    event.get("status") or data.get("status") or "")
    executed = []
    failed = []
    unfinished = []
    for call in calls:
        status = results.get(call["call_id"])
        if status == "completed":
            executed.append(call["name"])
        elif status:
            failed.append({"name": call["name"], "status": status})
        else:
            unfinished.append(call["name"])
    return {"calls": calls, "executed": executed, "failed": failed,
            "unfinished": unfinished}


def gate_events(events: list[dict], required=()) -> dict:
    """Fail closed unless a native EU4 tool actually executed."""
    native = _native_tool_names()
    account = executed_tools(events)
    native_executed = [name for name in account["executed"] if name in native]
    failed_native = [item for item in account["failed"]
                     if item["name"] in native]
    required = [name for name in required if name]
    problems: list[dict] = []
    unknown = [name for name in required if name not in native]
    if unknown:
        problems.append({"code": "unknown_required_tool",
                         "detail": "not a supported native EU4 tool: "
                                   + ", ".join(sorted(set(unknown)))})
    missing = [name for name in required if name not in native_executed]
    if missing:
        problems.append({"code": "missing_required_tool",
                         "detail": "required native tool did not execute: "
                                   + ", ".join(sorted(set(missing)))})
    if not native_executed:
        problems.append({"code": "no_native_tool",
                         "detail": "no supported native EU4 tool executed; "
                                   "bash, image reads and goal tools do not "
                                   "count"})
    if failed_native:
        problems.append({"code": "failed_native_tool",
                         "detail": "native tool call(s) did not complete: "
                                   + ", ".join(sorted({item["name"]
                                                       for item in failed_native}))})
    return {
        "schema": GATE_SCHEMA,
        "ok": not problems,
        "problems": problems,
        "problems_count": len(problems),
        "supported_native_tools": sorted(native),
        "native_executed": native_executed,
        "required": required,
        "failed": failed_native,
        "unfinished": account["unfinished"],
    }


def case_receipt(case_dir: Path | str) -> dict | None:
    """Read the case receipt and the sealed capture identity it contains."""
    case_dir = Path(case_dir)
    try:
        case_bytes = (case_dir / "case.json").read_bytes()
        seal_bytes = (case_dir / "capture" / "frame-seal.json").read_bytes()
        case = json.loads(case_bytes)
        seal = json.loads(seal_bytes)
    except (OSError, ValueError):
        return None
    inventory = case.get("files") or []
    return {
        "schema": "gfx-pilot-case-receipt/v1",
        "case_id": case.get("case_id"),
        "case_manifest_sha256": hashlib.sha256(case_bytes).hexdigest(),
        "seal_sha256": hashlib.sha256(seal_bytes).hexdigest(),
        "payload_inventory_sha256": hashlib.sha256(
            json.dumps(inventory, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")).hexdigest(),
        "files": len(inventory),
        "capture": {
            "session_id": seal.get("session_id"),
            "capture_frame_id": seal.get("capture_frame_id"),
            "event_stream_sha256": seal.get("event_stream_sha256"),
        },
        "present_index": seal.get("present_index"),
        "scene_index": seal.get("scene_index"),
    }


def _verification_complete(verification: dict) -> bool:
    return bool(
        verification
        and verification.get("ok") is True
        and not verification.get("problems")
        and verification.get("independently_reproducible") is True
        and isinstance(verification.get("files"), int)
        and verification["files"] > 0
        and verification.get("graph_status") == "complete")


def _verify_case(case_dir: Path) -> tuple[dict, list[dict]]:
    """Run the existing portable-case verifier; never trust a claim."""
    try:
        from automation import gfx_replay_case
    except Exception as exc:  # noqa: BLE001 - fail closed, report the import
        return {}, [{"code": "case_verify_unavailable",
                     "detail": f"{type(exc).__name__}: {exc}"}]
    try:
        verification = gfx_replay_case.verify(case_dir)
    except Exception as exc:  # noqa: BLE001 - CaseError and I/O both fail closed
        return {}, [{"code": "case_verification_failed",
                     "detail": f"{type(exc).__name__}: {exc}"}]
    return verification, []


def _verification_from_receipt(receipt_path: Path, digests: dict,
                               ) -> tuple[dict, list[dict]]:
    """Consume an independently produced receipt bound to these digests."""
    problems: list[dict] = []
    try:
        value = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [{"code": "case_receipt_unreadable",
                     "detail": f"{type(exc).__name__}: {exc}"}]
    if value.get("schema") != CASE_RECEIPT_SCHEMA:
        problems.append({"code": "case_receipt_schema",
                         "detail": f"receipt schema must be "
                                   f"{CASE_RECEIPT_SCHEMA}"})
    for key in ("case_manifest_sha256", "seal_sha256",
                "payload_inventory_sha256"):
        if value.get(key) != digests.get(key):
            problems.append({"code": "case_receipt_mismatch",
                             "detail": f"receipt {key} does not match the "
                                       f"current case"})
    verification = (value.get("verification")
                    if isinstance(value.get("verification"), dict) else {})
    if verification.get("case_id") != digests.get("case_id"):
        problems.append({"code": "case_receipt_mismatch",
                         "detail": "receipt case_id does not match the "
                                   "current case"})
    if verification.get("files") != digests.get("files"):
        problems.append({"code": "case_receipt_mismatch",
                         "detail": "receipt file count does not match the "
                                   "current case inventory"})
    if not _verification_complete(verification):
        problems.append({"code": "case_receipt_incomplete",
                         "detail": "receipt does not carry complete, "
                                   "independently reproducible case evidence"})
    return verification, problems


def _authoritative_evidence(case_dir: Path, digests: dict,
                            ) -> tuple[dict | None, list[dict]]:
    """Load the case's diagnostic report, bound to its sealed capture.

    The report and its checkpoints are existing evidence: this function does
    not re-run a comparator or build a dependency graph.  It only refuses a
    report whose identity is not verified or whose frame does not match the
    seal.
    """
    problems: list[dict] = []
    case_dir = Path(case_dir)
    try:
        manifest = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
        seal = json.loads((case_dir / "capture" / "frame-seal.json").read_text(
            encoding="utf-8"))
        report_relative = str(manifest.get("diagnostic_report")
                              or "diagnostic-report.json")
        report_path, problem = _case_relative_path(case_dir, report_relative)
        if problem is not None:
            problems.append({"code": "case_diagnostic_unavailable",
                             "detail": problem})
            return None, problems
        report_bytes = report_path.read_bytes()
        report = json.loads(report_bytes)
    except (OSError, ValueError) as exc:
        problems.append({"code": "case_diagnostic_unavailable",
                         "detail": f"{type(exc).__name__}: {exc}"})
        return None, problems
    if report.get("schema") != "gfx-frame-diagnosis/v1":
        problems.append({"code": "case_diagnostic_schema",
                         "detail": "diagnostic report has an unexpected schema"})
        return None, problems
    identity = report.get("identity") if isinstance(report.get("identity"),
                                                    dict) else {}
    if identity.get("status") != "verified":
        problems.append({"code": "case_diagnostic_unverified",
                         "detail": "the case diagnostic report is not an "
                                   "in-process verified result"})
        return None, problems
    checks = {item.get("check"): item
              for item in identity.get("checks") or []
              if isinstance(item, dict) and item.get("check")}
    failed = [name for name, item in checks.items()
              if item.get("ok") is not True]
    if failed:
        problems.append({"code": "case_diagnostic_identity_mismatch",
                         "detail": "failed identity check(s): "
                                   + ", ".join(sorted(failed))})
    session_check = checks.get("session_id") or {}
    if (str(session_check.get("expected")) != str(seal.get("session_id"))
            or str(session_check.get("actual")) != str(seal.get("session_id"))):
        problems.append({"code": "case_diagnostic_identity_mismatch",
                         "detail": "diagnostic session_id does not match the "
                                   "frame seal"})
    frame = report.get("frame") if isinstance(report.get("frame"), dict) else {}
    comparisons = {
        "capture_frame_id": (frame.get("capture_frame_id"),
                             seal.get("capture_frame_id")),
        "present_index": (frame.get("present_index"), seal.get("present_index")),
        "scene_index": (frame.get("scene_index"), seal.get("scene_index")),
        "manifest_sha256": (frame.get("manifest_sha256"),
                            seal.get("manifest_sha256")),
        "reference_sha256": (frame.get("reference_sha256"),
                             seal.get("reference_sha256")),
    }
    mismatched = [name for name, (actual, expected) in comparisons.items()
                  if actual != expected]
    if mismatched:
        problems.append({"code": "case_diagnostic_identity_mismatch",
                         "detail": "diagnostic report does not match the "
                                   "frame seal: " + ", ".join(sorted(mismatched))})
        return None, problems
    if problems:
        return None, problems
    checkpoints = []
    for item in report.get("checkpoints") or []:
        if not isinstance(item, dict):
            continue
        checkpoints.append({
            key: item.get(key) for key in
            ("event", "resource", "target", "status", "authoritative",
             "generation")
        })
    return {
        "schema": "gfx-pilot-authoritative-checkpoints/v1",
        "capture": {
            "session_id": seal.get("session_id"),
            "capture_frame_id": seal.get("capture_frame_id"),
            "event_stream_sha256": seal.get("event_stream_sha256"),
        },
        "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "checkpoints": checkpoints,
    }, problems


def _case_relative_path(case_dir: Path, relative: str) -> tuple[Path, str | None]:
    """Resolve a manifest-relative path without escaping the case."""
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        return case_dir, f"case-relative path is not allowed: {relative}"
    return case_dir / path, None


def _verify_interval_evidence(interval: dict, evidence: dict | None,
                              problems: list[dict],
                              sealed_frame_key) -> None:
    """Require claimed endpoints to exist in the authoritative case evidence.

    The structural contract in ``_valid_basis()`` is not enough: a conclusion
    could otherwise invent a same-capture matching checkpoint.  This check
    looks the claimed observations up in the case's existing diagnostic
    report.  A bounded interval with no authoritative evidence is reported
    unverified, never accepted.
    """
    bounded = interval.get("start_event") is not None
    if evidence is None:
        if bounded:
            problems.append({
                "code": "interval_unverified",
                "detail": "no authoritative case diagnostic evidence is "
                          "available for the claimed bounded interval"})
        return
    if _capture_key(evidence.get("capture")) != sealed_frame_key:
        problems.append({
            "code": "interval_unverified",
            "detail": "authoritative checkpoint evidence belongs to a "
                      "different capture"})
        return
    checkpoints = evidence.get("checkpoints") or []

    def claim(event):
        return next((item for item in interval.get("basis") or []
                     if isinstance(item, dict)
                     and item.get("event") == event), None)

    def check(entry, role: str, expected_status: str) -> None:
        if not isinstance(entry, dict):
            return  # _valid_basis already reports the structural problem
        rows = [item for item in checkpoints
                if isinstance(item, dict)
                and item.get("event") == entry.get("event")
                and str(item.get("resource")) == str(entry.get("resource"))
                and str(item.get("target")) == str(entry.get("target"))]
        if not rows:
            problems.append({
                "code": "interval_unverified",
                "detail": f"the {role} observation at event "
                          f"{entry.get('event')} is not in the authoritative "
                          f"case evidence"})
            return
        matches = [item for item in rows
                   if item.get("authoritative") is True
                   and item.get("status") == expected_status]
        if len(matches) != 1:
            problems.append({
                "code": "interval_unverified",
                "detail": f"the authoritative {role} observation at event "
                          f"{entry.get('event')} is not exactly one "
                          f"{expected_status} checkpoint"})

    if bounded:
        check(claim(interval.get("start_event")), "lower", "match")
    check(claim(interval.get("end_event")), "upper", "divergent")


def summarize_run(run_dir: Path | str, conclusion_path: Path | str,
                  case_dir: Path | str, *,
                  case_receipt_path: Path | str | None = None) -> dict:
    """Combine one pilot run's session, efficiency and conclusion evidence.

    Acceptance is fail-closed: the case is verified with the existing portable
    verifier (or a digest-bound receipt), the conclusion's case/capture
    identity and manifest/seal digests are checked against it, claimed causal
    endpoints are looked up in the case's diagnostic report, the run-event
    stream must record an executed native EU4 tool, and the launch count must
    be a present integer exactly zero.
    """
    run_dir = Path(run_dir)
    problems: list[dict] = []

    def read(path: Path, label: str):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append({"code": "missing_" + label,
                             "detail": f"{path}: {exc}"})
            return {}

    session = read(run_dir / "session.json", "session")
    efficiency = read(run_dir / "efficiency.json", "efficiency")
    conclusion = read(Path(conclusion_path), "conclusion")

    events_path = run_dir / "events.jsonl"
    if events_path.is_file():
        gate = gate_events(read_run_events(events_path))
    else:
        gate = {
            "schema": GATE_SCHEMA, "ok": False,
            "problems": [{"code": "missing_event_records",
                          "detail": "no events.jsonl; advertised or summarized "
                                    "tool names are not invocation evidence"}],
            "problems_count": 1, "native_executed": [],
        }
    problems.extend(gate.get("problems") or [])

    launches = (efficiency.get("eu4_launches") or {}).get("count") \
        if isinstance(efficiency.get("eu4_launches"), dict) else None
    if isinstance(launches, bool) or not isinstance(launches, int):
        problems.append({"code": "launch_count_invalid",
                         "detail": "eu4_launches.count is missing or not an "
                                   "integer; the offline budget cannot be "
                                   "verified"})
    elif launches != 0:
        problems.append({"code": "eu4_launch",
                         "detail": f"pilot launched EU4 {launches} time(s); "
                                   "the offline budget is zero"})
    if not efficiency:
        problems.append({"code": "no_efficiency",
                         "detail": "the session export was not analyzed"})

    digests = case_receipt(case_dir) if case_dir is not None else None
    verification: dict = {}
    evidence: dict | None = None
    if digests is None:
        problems.append({"code": "missing_case",
                         "detail": f"no readable case.json/frame-seal.json in "
                                   f"{case_dir}; the conclusion's own "
                                   f"verification flag is not evidence"})
    else:
        if case_receipt_path is not None:
            verification, verification_problems = _verification_from_receipt(
                Path(case_receipt_path), digests)
        else:
            verification, verification_problems = _verify_case(Path(case_dir))
        problems.extend(verification_problems)
        if not _verification_complete(verification):
            problems.append({
                "code": "case_not_verified",
                "detail": "the case is not valid, complete and independently "
                          "reproducible"})
        conclusion_case = (conclusion.get("case")
                           if isinstance(conclusion.get("case"), dict) else {})
        if digests.get("case_id") != conclusion_case.get("case_id"):
            problems.append({"code": "case_receipt_mismatch",
                             "detail": "the conclusion case_id does not match "
                                       "the verified case"})
        if _capture_key(digests.get("capture")) != _capture_key(
                conclusion_case.get("sealed_frame")):
            problems.append({"code": "case_capture_mismatch",
                             "detail": "the conclusion sealed_frame does not "
                                       "match the case seal"})
        evidence, evidence_problems = _authoritative_evidence(
            Path(case_dir), digests)
        problems.extend(evidence_problems)

    if conclusion:
        validation = validate_conclusion(
            conclusion, authoritative=evidence, case_digests=digests)
        problems.extend(validation.get("problems") or [])
    else:
        validation = {"schema": VALIDATION_SCHEMA, "ok": False,
                      "problems": [{"code": "missing_conclusion"}],
                      "checks": {}}

    calls = efficiency.get("calls") or {}
    tokens = efficiency.get("tokens") or {}
    window = efficiency.get("window") or {}
    conclusion_outcome = conclusion.get("outcome") if conclusion else None
    evidence_request = conclusion.get("evidence_request") if conclusion else None
    tools = [item for item in session.get("tool_calls") or [] if item]
    return {
        "schema": "gfx-pilot-evidence/v1",
        "ok": not problems,
        "problems": problems,
        "problems_count": len(problems),
        "native_gate": gate,
        "case_verification": verification,
        "case_receipt": digests,
        "authoritative_evidence": (None if evidence is None else {
            "schema": evidence.get("schema"),
            "report_sha256": evidence.get("report_sha256"),
            "capture": evidence.get("capture"),
            "checkpoints": len(evidence.get("checkpoints") or []),
        }),
        "session": {
            "id": session.get("session_id"),
            "cwd": (efficiency.get("session") or {}).get("cwd"),
            "turns": window.get("turns"),
            "elapsed_seconds": window.get("elapsed_seconds"),
            "model_calls": window.get("model_calls"),
            "tool_calls": tools,
            "tool_count": len(tools),
            "native_tool_calls": gate.get("native_executed") or [],
            "native_tool_count": len(gate.get("native_executed") or []),
        },
        "efficiency": {
            "calls_total": calls.get("total"),
            "calls_by_tool": calls.get("by_tool"),
            "failed_calls": calls.get("failed"),
            "repeated_calls": efficiency.get("repeated_calls"),
            "output_chars": (efficiency.get("output") or {}).get("total_chars"),
            "oversized_count": (efficiency.get("output") or {}).get(
                "oversized_count"),
            "tool_wait_seconds": (efficiency.get("wait") or {}).get(
                "tool_wait_seconds"),
            "test_invocations": (efficiency.get("tests") or {}).get(
                "test_invocations"),
            "eu4_launches": launches,
            "tokens": {
                "input": tokens.get("input"),
                "cache_read": tokens.get("cache_read"),
                "output": tokens.get("output"),
                "total": tokens.get("total"),
            },
        },
        "conclusion": {
            "validation": validation,
            "outcome": conclusion_outcome,
            "hypotheses": [
                {"id": item.get("id"), "status": item.get("status")}
                for item in conclusion.get("hypotheses") or []
                if isinstance(item, dict)
            ],
            "evidence_request": evidence_request,
            "has_correction": "correction" in conclusion,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--conclusion", required=True, type=Path)
    validate.add_argument("--case", type=Path, default=None,
                          help="verified case directory; required for a "
                               "bounded causal interval")
    validate.add_argument("--case-receipt", type=Path, default=None,
                          help="digest-bound verification receipt")
    validate.add_argument("--out", type=Path, default=None)
    gate = sub.add_parser("gate")
    gate.add_argument("--events", required=True, type=Path,
                      help="the launcher's NDJSON run-event stream")
    gate.add_argument("--require-tool", action="append", default=[],
                      help="a supported native EU4 tool that must execute")
    gate.add_argument("--out", type=Path, default=None)
    summary = sub.add_parser("summarize")
    summary.add_argument("--run", required=True, type=Path)
    summary.add_argument("--conclusion", required=True, type=Path)
    summary.add_argument("--case", required=True, type=Path,
                         help="verified case directory; acceptance fails "
                              "without it")
    summary.add_argument("--case-receipt", type=Path, default=None,
                         help="digest-bound verification receipt, reused "
                              "instead of re-running the verifier")
    summary.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    status = 0
    if args.operation == "summarize":
        result = summarize_run(args.run, args.conclusion, args.case,
                               case_receipt_path=args.case_receipt)
        status = 0 if result.get("ok") else 1
    elif args.operation == "gate":
        try:
            events = read_run_events(args.events)
        except OSError as exc:
            result = {"schema": GATE_SCHEMA, "ok": False,
                      "problems": [{"code": "unreadable_events",
                                    "detail": str(exc)}]}
            status = 1
        else:
            result = gate_events(events, args.require_tool)
            status = 0 if result.get("ok") else 2
    else:
        try:
            value = json.loads(Path(args.conclusion).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            result = {"schema": VALIDATION_SCHEMA, "ok": False,
                      "problems": [{"code": "unreadable",
                                    "detail": f"{type(exc).__name__}: {exc}"}],
                      "checks": {}}
            status = 1
        else:
            digests = case_receipt(args.case) if args.case else None
            verification: dict = {}
            evidence: dict | None = None
            case_problems: list[dict] = []
            if args.case:
                if digests is None:
                    case_problems.append({
                        "code": "missing_case",
                        "detail": f"no readable case.json/frame-seal.json in "
                                  f"{args.case}"})
                else:
                    if args.case_receipt is not None:
                        verification, found = _verification_from_receipt(
                            args.case_receipt, digests)
                    else:
                        verification, found = _verify_case(args.case)
                    case_problems.extend(found)
                    if not _verification_complete(verification):
                        case_problems.append({
                            "code": "case_not_verified",
                            "detail": "the case is not valid, complete and "
                                      "independently reproducible"})
                    evidence, found = _authoritative_evidence(
                        args.case, digests)
                    case_problems.extend(found)
            result = validate_conclusion(
                value, authoritative=evidence, case_digests=digests)
            if case_problems:
                result["problems"] = case_problems + result["problems"]
                result["problems_count"] = len(result["problems"])
                result["ok"] = False
            result["case_verification"] = verification or None
            status = 0 if result.get("ok") else 1
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
