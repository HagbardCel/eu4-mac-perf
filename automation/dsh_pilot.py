"""Validate one autonomous-diagnosis pilot conclusion, fail-closed.

M7-P1 runs a real DSH model session against an immutable sealed case.  The
session must end with a machine-readable conclusion, and that conclusion is
evidence only if its claims are bounded by what the case can actually prove.
This module validates the conclusion without running anything: it checks the
schema, the investigation budget, the correction evidence and, most
importantly, that no causal interval is assembled from checkpoints observed in
different captures.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "gfx-pilot-conclusion/v1"
VALIDATION_SCHEMA = "gfx-pilot-validation/v1"
OUTCOMES = ("correction_proposed", "requires_additional_evidence")
HYPOTHESIS_STATUS = ("supported", "refuted", "insufficient")
FRAME_SCOPES = ("same_sealed_frame", "new_capture")
MAX_HYPOTHESES = 3


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


def _statistics(value) -> bool:
    return (isinstance(value, dict)
            and _integer(value.get("mismatched_pixels"))
            and _integer(value.get("max_channel_error")))


def _valid_basis(interval: dict, problems: list[dict]) -> None:
    """A causal interval is only valid inside one capture."""
    basis = interval.get("basis")
    if not isinstance(basis, list) or not basis:
        problems.append({"code": "interval_without_basis",
                         "detail": "a causal interval needs checkpoint basis"})
        return
    keys = []
    for item in basis:
        if not isinstance(item, dict):
            problems.append({"code": "interval_basis_invalid",
                             "detail": "basis entry is not an object"})
            continue
        if not _integer(item.get("event")):
            problems.append({"code": "interval_basis_invalid",
                             "detail": "basis entry has no integer event"})
        if not _resource(item.get("resource")):
            problems.append({"code": "interval_basis_invalid",
                             "detail": "basis entry has no fb:0/tex:N resource"})
        key = _capture_key(item.get("capture"))
        if key is None:
            problems.append({
                "code": "interval_basis_unbound",
                "detail": "basis checkpoint has no capture identity"})
        else:
            keys.append(key)
    if keys and len(set(keys)) > 1:
        problems.append({
            "code": "cross_capture_interval",
            "detail": (
                "basis checkpoints come from different captures; a causal "
                "interval may only use observations from one sealed frame")})
    if interval.get("same_capture") is not True:
        problems.append({
            "code": "interval_not_same_capture",
            "detail": "same_capture must be true for a claimed interval"})
    start = interval.get("start_event")
    end = interval.get("end_event")
    if not _integer(end):
        problems.append({"code": "interval_invalid",
                         "detail": "end_event must be a non-negative integer"})
    if start is not None:
        if not _integer(start):
            problems.append({"code": "interval_invalid",
                            "detail": "start_event must be null or an integer"})
        elif _integer(end) and start >= end:
            problems.append({"code": "interval_invalid",
                            "detail": "start_event must precede end_event"})


def validate_conclusion(value) -> dict:
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
            _valid_basis(interval, problems)
            basis_keys = {
                key for key in (_capture_key(item.get("capture"))
                                for item in interval.get("basis") or []
                                if isinstance(item, dict))
                if key is not None
            }
            if case_frame_key is not None and basis_keys - {case_frame_key}:
                problem("cross_capture_interval",
                        "causal interval basis is not the conclusion's sealed "
                        "frame")
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


def summarize_run(run_dir: Path | str, conclusion_path: Path | str) -> dict:
    """Combine one pilot run's session, efficiency and conclusion evidence.

    The summary is a bounded projection; it never copies the session export or
    large artifacts.  A run is only ``ok`` when the conclusion validates and
    the session invoked at least one native EU4 tool without launching EU4.
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
    validation = validate_conclusion(conclusion) if conclusion else {
        "schema": VALIDATION_SCHEMA, "ok": False,
        "problems": [{"code": "missing_conclusion"}], "checks": {}}

    tools = [item for item in session.get("tool_calls") or [] if item]
    if not tools:
        problems.append({"code": "no_native_tool",
                         "detail": "the pilot turn invoked no native EU4 tool"})
    launches = ((efficiency.get("eu4_launches") or {}).get("count"))
    if launches is not None and launches != 0:
        problems.append({"code": "eu4_launch",
                         "detail": f"pilot launched EU4 {launches} time(s); "
                                   "the offline budget is zero"})
    if not efficiency:
        problems.append({"code": "no_efficiency",
                         "detail": "the session export was not analyzed"})
    if not validation.get("ok"):
        problems.extend(validation.get("problems") or [])

    calls = efficiency.get("calls") or {}
    tokens = efficiency.get("tokens") or {}
    window = efficiency.get("window") or {}
    conclusion_outcome = conclusion.get("outcome") if conclusion else None
    evidence_request = conclusion.get("evidence_request") if conclusion else None
    return {
        "schema": "gfx-pilot-evidence/v1",
        "ok": not problems,
        "problems": problems,
        "problems_count": len(problems),
        "session": {
            "id": session.get("session_id"),
            "cwd": (efficiency.get("session") or {}).get("cwd"),
            "turns": window.get("turns"),
            "elapsed_seconds": window.get("elapsed_seconds"),
            "model_calls": window.get("model_calls"),
            "tool_calls": tools,
            "tool_count": len(tools),
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
    validate.add_argument("--out", type=Path, default=None)
    summary = sub.add_parser("summarize")
    summary.add_argument("--run", required=True, type=Path)
    summary.add_argument("--conclusion", required=True, type=Path)
    summary.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.operation == "summarize":
        result = summarize_run(args.run, args.conclusion)
    else:
        try:
            value = json.loads(Path(args.conclusion).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            result = {"schema": VALIDATION_SCHEMA, "ok": False,
                      "problems": [{"code": "unreadable",
                                    "detail": f"{type(exc).__name__}: {exc}"}],
                      "checks": {}}
        else:
            result = validate_conclusion(value)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
