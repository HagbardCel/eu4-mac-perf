"""Stable JSON tool core for agents (CLI, DSH plugin, MCP, OpenCode).

Eight logical tools share one implementation:

``eu4_re_query``       fast indexed reverse-engineering knowledge
``eu4_research``       sealed renderer-snapshot instructions, decompilation and
                       paginated static cross-references
``eu4_session``        owned session inspection and safe termination
``eu4_capture``        frame/health inspection and targeted capture requests
``eu4_diff_run``       run a dual-process differential campaign
``eu4_diff_inspect``   inspect the first discrepancy of a campaign
``eu4_replay``         replay a stored case/frame, verify a Strategy B case,
                       diagnose already-produced Metal output, or execute a
                       bounded M7 ordered sequence in one replay session
``eu4_case_inspect``   summarize a v1 or v2 Strategy B case package

``eu4_diff_run`` remains the older two-process differential workflow.  M7
sequence execution is deliberately exposed through ``eu4_replay sequence``
instead, so an agent cannot confuse the two contracts.

Every invocation prints exactly one JSON object with a stable envelope.  Thin
agent adapters must call this module rather than reimplementing any backend
logic.

Envelope schema ``eu4-agent-tool/v2`` separates five questions that the canary
proved are not the same:

* ``outcome.execution`` -- did the tool adapter and core run at all?
* ``outcome.diagnostic`` -- did the requested diagnostic operation complete
  (``complete``), get truncated (``partial``), or not run
  (``incomplete``/``unavailable``/``refused``/``failed``)?
* ``outcome.verdict`` -- the rendering/comparison conclusion, if any.  A
  successful comparison that finds different pixels is a completed diagnostic
  with verdict ``different``, not a tool failure.
* ``outcome.evidence`` -- ``valid``/``invalid``/``unverified`` for the
  evidence the operation used, or ``null`` when it does not apply.
* ``outcome.qualified`` -- the separate qualification outcome, or ``null``.

``ok`` is true only when the operation ran and its diagnostic is complete or
explicitly bounded (``partial``); module import, interpreter and credential
policy are unchanged.  Every envelope also carries ``runtime`` with the actual
interpreter and NumPy availability, so a native/CLI interpreter mismatch is
visible in the response that failed.  v1 consumers can keep reading ``ok``,
``status`` and ``result``; ``ok`` is now false for an incomplete diagnostic that
v1 reported as a success.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import (gfx_align, gfx_case, gfx_diagnostics, gfx_dual_run,
                        gfx_event_evidence, gfx_frame_graph,
                        gfx_independent_replay, gfx_replay_case,
                        gfx_scenario)  # noqa: E402
from automation import (evidence_index, gfx_frame_compare, gfx_session, launch_lease,
                        launch_ledger, python_runtime)  # noqa: E402

SCHEMA = "eu4-agent-tool/v2"
RUNTIME_SCHEMA = "eu4-tool-runtime/v1"
TOOLS = (
    "eu4_re_query",
    "eu4_research",
    "eu4_session",
    "eu4_capture",
    "eu4_diff_run",
    "eu4_diff_inspect",
    "eu4_replay",
    "eu4_case_inspect",
)

# Status -> diagnostic state. A status not listed is treated as incomplete: a
# new tool must declare its completion explicitly rather than inherit success.
_DIAGNOSTIC_BY_STATUS = {
    "ok": "complete",
    "pass": "complete",
    "completed": "complete",
    "complete": "complete",
    "match": "complete",
    "stopped": "complete",
    "not_run": "not_applicable",
    "partial": "partial",
    "incomplete": "incomplete",
    "not_found": "unavailable",
    "unavailable": "unavailable",
    "refused": "refused",
    "invalid": "invalid",
    "failed": "failed",
    "error": "failed",
}
_COMPLETE_DIAGNOSTIC = ("complete", "partial", "not_applicable")


def _runtime_block() -> dict:
    """Report the actual interpreter environment of this process.

    The configured path is compared as an *environment*, not by dereferencing
    symlinks: a virtualenv entry point (``.venv/bin/python3``) is a different
    environment from the base interpreter it points at.  Two virtualenvs that
    share a base binary therefore report a mismatch, while a system interpreter
    reached through a symlink does not.
    """
    configured_raw = os.environ.get("EU4_DSH_PYTHON", "")
    executable = python_runtime.absolute(sys.executable)
    configured_executable = ""
    configured_environment = None
    mismatch = False
    if configured_raw:
        try:
            configured_executable = python_runtime.absolute(configured_raw)
            configured_environment = python_runtime.environment_root(
                configured_executable)
        except (OSError, ValueError):
            configured_executable = configured_raw
            mismatch = True
    current_environment = (sys.prefix
                           if sys.prefix != getattr(sys, "base_prefix", sys.prefix)
                           else None)
    if configured_executable and not mismatch:
        if configured_environment or current_environment:
            mismatch = configured_environment != current_environment
        else:
            same_binary = False
            try:
                same_binary = os.path.samefile(configured_executable, executable)
            except OSError:
                same_binary = False
            mismatch = configured_executable != executable and not same_binary
    available = gfx_frame_compare.np is not None
    return {
        "schema": RUNTIME_SCHEMA,
        "executable": executable,
        "version": list(sys.version_info[:3]),
        "environment": current_environment or sys.prefix,
        "virtualenv": current_environment is not None,
        "configured": configured_raw,
        "configured_executable": configured_executable or None,
        "configured_environment": configured_environment,
        "mismatch": mismatch,
        "numpy": {
            "required": True,
            "available": available,
            "version": getattr(gfx_frame_compare.np, "__version__", None),
        },
    }


def _nested_evidence_state(result) -> str | None:
    """Detect invalid/incomplete evidence carried inside a result payload.

    Some tools return a verification or diagnosis object whose own status is
    weaker than the top-level envelope.  A nested invalid verification can
    never be promoted to a qualified result merely because the outer call
    completed.
    """
    if not isinstance(result, dict):
        return None
    verification = result.get("verification")
    if isinstance(verification, dict):
        if verification.get("verifier_exception") is True:
            return "invalid"
        if verification.get("ok") is False:
            return "invalid"
    diagnosis = result.get("diagnosis")
    if isinstance(diagnosis, dict):
        classification = str(diagnosis.get("classification") or "")
        if classification in ("invalid_provenance", "incomplete_capture"):
            return "invalid"
        if classification == "incomplete_diagnostics":
            return "incomplete"
        axes = diagnosis.get("axes")
        if isinstance(axes, dict) and axes.get("capture_validity") == "invalid":
            return "invalid"
    return None


def _classify_failure(status: str, reason: str, diagnostic: str,
                      verdict: str | None,
                      result=None) -> dict:
    """Separate operational failures from a completed comparison.

    Classification is driven by the explicit status/diagnostic first.  Text is
    only a tie-breaker for named dependency or capability failures.  In
    particular, the word "Metal" is not evidence that a GPU is unavailable: a
    shader/replay correctness error is a tool failure, while invalid evidence
    always takes precedence over generic dependency wording.
    """
    lowered = (reason or "").lower()
    nested = _nested_evidence_state(result)
    if status in ("ok", "pass", "match") and diagnostic in (
            "complete", "partial", "not_applicable") and nested is None:
        if verdict == "different":
            return {"kind": None, "class": "comparison",
                    "detail": "completed comparison reported a difference"}
        if verdict == "unqualified":
            return {"kind": None, "class": "comparison",
                    "detail": "completed comparison remained unqualified"}
        return {"kind": None, "class": "diagnostic", "detail": ""}
    if nested == "invalid":
        return {"kind": "evidence_invalid", "class": "failure",
                "detail": "nested verification/diagnosis is invalid"}
    if "timed out" in lowered or "timeout" in lowered:
        return {"kind": "execution_timeout", "class": "failure",
                "detail": reason or "the operation timed out"}
    if any(token in lowered for token in (
            "credential", "unauthorized", "authentication", "api key")):
        return {"kind": "missing_credential", "class": "blocker",
                "detail": reason or "credentials are required"}
    if status == "invalid" or diagnostic == "invalid":
        return {"kind": "evidence_invalid", "class": "failure",
                "detail": reason or "evidence did not qualify"}
    if status == "not_found":
        return {"kind": "evidence_missing", "class": "blocker",
                "detail": reason or "the requested evidence is missing"}
    if status == "refused":
        return {"kind": "policy_refusal", "class": "blocker",
                "detail": reason or "the operation was refused"}
    if diagnostic == "unavailable":
        if any(token in lowered for token in (
                "no metal device", "metal device is unavailable",
                "mtlcreatesystemdefaultdevice", "unsupported gpu",
                "graphics unavailable", "no gpu", "no display",
                "display is unavailable", "betterdisplay", "rosetta")):
            return {"kind": "graphics_unavailable", "class": "blocker",
                    "detail": reason or "a graphics capability is unavailable"}
        if any(token in lowered for token in (
                "numpy", "import", "dependency", "missing module")):
            return {"kind": "dependency_unavailable", "class": "blocker",
                    "detail": reason or "a required dependency is unavailable"}
        return {"kind": "unavailable", "class": "blocker",
                "detail": reason or "the operation is unavailable"}
    if status == "incomplete" and diagnostic in ("incomplete", "unverified"):
        return {"kind": "evidence_missing", "class": "blocker",
                "detail": reason or "required evidence is unverified or missing"}
    if nested == "incomplete" or diagnostic in ("incomplete", "unverified"):
        return {"kind": "evidence_invalid", "class": "failure",
                "detail": reason or "evidence did not qualify"}
    if status == "partial" and verdict is None:
        return {"kind": "evidence_invalid", "class": "failure",
                "detail": reason or "evidence did not qualify"}
    return {"kind": "tool_failure", "class": "failure",
            "detail": reason or f"operation ended as {status}"}


def _envelope(tool: str, status: str, result=None, *, reason: str = "",
              exit_code: int | None = None, diagnostic: str | None = None,
              verdict: str | None = None, evidence: str | None = None,
              qualified: bool | None = None) -> dict:
    state = diagnostic or _DIAGNOSTIC_BY_STATUS.get(status, "incomplete")
    execution = "error" if status == "error" else "ok"
    nested = _nested_evidence_state(result)
    if nested == "invalid" and status in (
            "ok", "pass", "complete", "partial", "match"):
        status = "invalid"
        state = "invalid"
        execution = "ok"
        verdict = None
        evidence = "invalid"
        qualified = False
    elif nested == "incomplete" and state == "complete":
        state = "incomplete"
        evidence = "unverified"
        qualified = False
    complete = state == "complete"
    ok = execution == "ok" and state in _COMPLETE_DIAGNOSTIC
    failure = _classify_failure(status, reason, state, verdict, result)
    value = {
        "schema": SCHEMA,
        "tool": tool,
        "ok": ok,
        "status": status,
        "reason": reason,
        "result": result if result is not None else {},
        "outcome": {
            "execution": execution,
            "diagnostic": state,
            "diagnostic_complete": complete,
            "verdict": verdict,
            "evidence": evidence,
            "qualified": qualified,
            "failure_kind": failure["kind"],
            "failure_class": failure["class"],
        },
        "failure": failure,
        "runtime": _runtime_block(),
    }
    if exit_code is not None:
        value["exit_code"] = exit_code
    return value



def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: not an object")
    return value


def _run_module(module: str, args: list[str], *, timeout: float = 600.0) -> dict:
    """Run one existing CLI module and preserve its process outcome.

    A parseable JSON body is not evidence that the subprocess succeeded: the
    return code, missing output and malformed JSON are all reported separately
    so callers can distinguish execution failure from a valid diagnostic
    verdict (a differing-pixel replay is a valid result, not a tool error).
    """
    completed = subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=timeout,
    )
    text = completed.stdout.strip()
    parsed = None
    parse_error = ""
    if not text:
        parse_error = completed.stderr.strip() or "tool produced no output"
    else:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            parse_error = f"tool output is not JSON: {exc}"
    if parsed is not None and not isinstance(parsed, dict):
        parsed = {"result": parsed}
    return {
        "module": module,
        "args": list(args),
        "exit_code": completed.returncode,
        "stdout": text,
        "stderr": (completed.stderr or "")[-4000:],
        "value": parsed,
        "parse_error": parse_error,
    }


def _subprocess_report(run: dict) -> dict:
    return {
        "module": run.get("module"),
        "args": run.get("args"),
        "exit_code": run.get("exit_code"),
        "stderr": run.get("stderr"),
        "parse_error": run.get("parse_error") or "",
    }


# ---------------------------------------------------------------------------
# Shared inspection helpers


def _session_dirs(path: Path) -> list[Path]:
    """Resolve a session directory or every session under a campaign root."""
    path = Path(path)
    if (path / "result.json").is_file() or (path / "session.json").is_file():
        return [path]
    if path.is_dir():
        found = [
            child for child in sorted(path.glob("session-*"))
            if child.is_dir() and (child / "result.json").is_file()
        ]
        if found:
            return found
    return []


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _optional_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _scan_health(run: Path) -> dict:
    """Freshly scan a session, including both role directories."""
    health = gfx_diagnostics.GpuHealth()
    for directory in (run, run / "reference", run / "candidate"):
        if directory.is_dir():
            gfx_diagnostics.scan_run(directory, health=health)
    return health.summary()


def _compact_health(summary: dict | None) -> dict:
    summary = summary or {}
    first = summary.get("first_failure") or {}
    return {
        "verdict": summary.get("verdict"),
        "distinct_diagnostics": summary.get("distinct_diagnostics"),
        "total_diagnostics": summary.get("total_diagnostics"),
        "first_failure": (
            {"kind": first.get("kind"), "message": first.get("message")}
            if first else None
        ),
        "instrumentation_failures": len(summary.get("instrumentation_failures") or []),
        "pixel_correctness": summary.get("pixel_correctness"),
    }


_HEALTH_RANK = {"unknown": 0, "healthy": 1, "degraded": 2, "failed": 3}


def _health_view(run: Path, result: dict) -> dict:
    """Reconcile persisted session health with a fresh scan, with provenance.

    Neither source silently wins: each is reported with its path and verdict,
    the agreement is explicit, and an ``unknown`` verdict is never presented as
    established healthy execution.
    """
    persisted = result.get("health") if isinstance(result.get("health"), dict) else None
    scanned = _scan_health(run)
    persisted_verdict = (persisted or {}).get("verdict")
    scanned_verdict = scanned.get("verdict") or "unknown"
    if persisted is None:
        agreement = "scan_only"
        chosen, source = scanned, "scan"
    elif persisted_verdict == scanned_verdict:
        agreement = "agree"
        chosen, source = persisted, "persisted"
    elif persisted_verdict in (None, "unknown"):
        agreement = "scan_only"
        chosen, source = scanned, "scan"
    elif scanned_verdict in (None, "unknown"):
        agreement = "persisted_only"
        chosen, source = persisted, "persisted"
    else:
        agreement = "disagree"
        if _HEALTH_RANK.get(scanned_verdict, 0) >= _HEALTH_RANK.get(
                persisted_verdict, 0):
            chosen, source = scanned, "scan"
        else:
            chosen, source = persisted, "persisted"
    note = ""
    if agreement == "disagree":
        note = (f"persisted health is {persisted_verdict} but a fresh scan is "
                f"{scanned_verdict}; the more severe verdict is reported and "
                "the disagreement is preserved")
    elif agreement == "persisted_only":
        note = ("a fresh scan found no diagnostics; the persisted record is the "
                "only evidence and is reported with that provenance")
    elif agreement == "scan_only":
        note = ("no persisted record was usable; the fresh scan is reported with "
                "that provenance")
    if chosen.get("verdict") == "unknown":
        note = ("health is unknown, not healthy: no diagnostic event establishes "
                "successful execution")
    merged = dict(chosen)
    merged.update({
        "schema": "gfx-health-view/v1",
        "verdict": chosen.get("verdict") or "unknown",
        "agreement": agreement,
        "source": source,
        "sources": {
            "persisted": {
                "available": persisted is not None,
                "path": str(run / "result.json"),
                "summary": _compact_health(persisted),
            },
            "scanned": {
                "available": True,
                "path": str(run),
                "summary": _compact_health(scanned),
            },
        },
        "note": note,
    })
    return merged


def _max_frames(result: dict) -> int:
    value = (result.get("qualification") or {}).get("requested_frames")
    if type(value) is int and value > 0:
        return value
    scenario = result.get("scenario") or {}
    value = (scenario.get("observation") or {}).get("max_frames")
    return value if type(value) is int and value > 0 else 1


def _recompute_classification(result: dict) -> tuple[dict | None, str]:
    if not result:
        return None, "no result.json"
    try:
        value = gfx_dual_run.classify_campaign(
            result, result.get("scenario") or {}, max_frames=_max_frames(result))
        return value, ""
    except Exception as exc:  # noqa: BLE001 - a recorded schema may be older
        return None, f"{type(exc).__name__}: {exc}"


def _staticity(run: Path, result: dict) -> dict:
    """Pair-local staticity from the recorded campaign dumps.

    This uses the same predicate as campaign classification.  A recorded
    session whose dumps predate the collector's ``valid`` marker stays
    unverified rather than being inferred from its digests.
    """
    required = int(((result.get("scenario") or {}).get("settle") or {}).get(
        "stable_frames", 1) or 1)
    comparison = (result.get("frames") or {}).get("comparison") or {}
    pair = comparison.get("first_failure") or (
        result.get("frames") or {}).get("last_pair") or {}
    frames = {
        "reference": pair.get("frame_index"),
        "candidate": pair.get("candidate_frame_index", pair.get("frame_index")),
    }
    session = result.get("session_id") or (
        (result.get("session") or {}).get("session_id")
        if isinstance(result.get("session"), dict) else None)
    value = gfx_align.scene_staticity(
        result.get("dumps"), required=required, frames=frames,
        session=session)
    value["source"] = "result.dumps"
    return value


def _compact_pair(pair: dict | None) -> dict | None:
    if not isinstance(pair, dict):
        return None
    comparison = pair.get("comparison") or {}
    statistics = comparison.get("statistics") or {}
    return {
        "reference_frame": pair.get("frame_index"),
        "candidate_frame": pair.get("candidate_frame_index"),
        "alignment_status": (comparison.get("alignment") or {}).get("status"),
        "alignment_evidence": (comparison.get("alignment") or {}).get("evidence"),
        "reference": {
            "game_day": (pair.get("reference") or {}).get("game_day"),
            "update_identity": (pair.get("reference") or {}).get("update_identity"),
            "caller_signature": (pair.get("reference") or {}).get("caller_signature"),
            "draws": (pair.get("reference") or {}).get("draws"),
            "sha256": (pair.get("reference") or {}).get("sha256"),
        },
        "candidate": {
            "game_day": (pair.get("candidate") or {}).get("game_day"),
            "update_identity": (pair.get("candidate") or {}).get("update_identity"),
            "caller_signature": (pair.get("candidate") or {}).get("caller_signature"),
            "draws": (pair.get("candidate") or {}).get("draws"),
            "sha256": (pair.get("candidate") or {}).get("sha256"),
        },
        "comparison": {
            "status": comparison.get("status"),
            "reasons": (comparison.get("reasons") or [])[:6],
            "statistics": {
                key: statistics.get(key) for key in (
                    "pixels", "mismatched_pixels", "mismatched_fraction",
                    "max_channel_error", "mean_abs_error", "difference_bbox")
            },
        },
    }


def _missing_evidence(result: dict, pair: dict | None, staticity: dict,
                      health: dict) -> list[str]:
    missing: list[str] = []
    if pair is None:
        missing.append("a paired frame with preserved raw payloads")
        return missing
    dumps = pair.get("dumps") or {}
    for role in ("reference", "candidate"):
        if not ((dumps.get(role) or {}).get("found") or {}):
            missing.append(f"{role} raw frame payload for the compared index")
    if not staticity.get("verified"):
        missing.append(
            "consecutive identical full-resolution frame digests on both sides "
            "(staticity is unverified)")
    comparison = pair.get("comparison") or {}
    if comparison.get("status") in (gfx_frame_compare.UNVERIFIED, "not_compared", None):
        missing.append("an aligned pixel comparison with valid raw payloads")
    if health.get("verdict") == "unknown":
        missing.append("a diagnostic event that establishes GPU execution health")
    if not result.get("semantic"):
        missing.append(
            "per-draw semantic operation prefix for the failing frame "
            "(only needed once staticity and correspondence are established)")
    return missing


def _next_steps(classification: str, health: dict, staticity: dict,
                pair: dict | None) -> list[str]:
    """Category-specific next actions; never default to another full campaign."""
    steps: list[str] = []
    if classification == "process_failure":
        steps.append(
            "inspect process exit codes, terminal-status.json and the tail of "
            "each role's game.stderr/game.stdout before any pixel work")
    elif classification == "gpu_execution_failure":
        steps.append(
            "inspect health.first_failure and the gpu raw failure records; "
            "correct the rejected draw or failed command buffer first")
    elif classification in ("alignment_failure", "unaligned"):
        steps.append(
            "compare caller signatures, draw counts and render stage for the "
            "rejected pair; capture the selected event if the stage differs")
    elif classification == "incomplete":
        if not staticity.get("verified"):
            steps.append(
                "run eu4_diff_inspect --temporal on the preserved consecutive "
                "frames; do not start another campaign until staticity is "
                "observed")
        elif health.get("verdict") != "healthy":
            steps.append(
                "resolve the outstanding GPU-health diagnostic before "
                "interpreting the pixel numbers")
        else:
            steps.append(
                "compare the first differing region against the temporal "
                "difference set to separate scene variability from renderer "
                "behaviour")
    elif classification == "rendering_difference":
        steps.append(
            "localize the first differing region and capture a per-draw "
            "operation prefix for that pass")
    elif classification == "match":
        steps.append("none; the compared frame is qualified")
    else:
        steps.append(
            "read the recorded stop reason and the missing-evidence list "
            "before choosing another experiment")
    if health.get("verdict") == "unknown":
        steps.append(
            "treat GPU health as unknown, not healthy; re-scan the session or "
            "record a health summary before qualification")
    return steps


def _session_summary(directory: Path) -> dict:
    result = _optional_json(directory / "result.json")
    recomputed, error = _recompute_classification(result)
    recorded = result.get("classification")
    comparison = (result.get("frames") or {}).get("comparison") or {}
    qualification = result.get("qualification") or {}
    return {
        "session_id": result.get("session_id") or directory.name,
        "path": str(directory),
        "result_sha256": _sha256_file(directory / "result.json"),
        "recorded_classification": recorded,
        "classification": (recomputed or {}).get("status", recorded),
        "classification_change": (
            None if recomputed is None or not recorded else recorded != (
                recomputed or {}).get("status")
        ),
        "reclassification_error": error or None,
        "stop": (result.get("stop") or {}).get("reason"),
        "health": (result.get("health") or {}).get("verdict"),
        "comparison": {
            "status": comparison.get("status"),
            "compared": comparison.get("compared"),
            "failed": comparison.get("failed"),
        },
        "qualified": qualification.get("qualified"),
        "finished_utc": result.get("finished_utc"),
    }


# ---------------------------------------------------------------------------
# Tool implementations


def eu4_re_query(args: argparse.Namespace) -> dict:
    from reverse_engineering import re_query

    if args.re_op == "provenance":
        value = re_query.query_provenance(index=args.index)
    elif args.re_op == "struct":
        value = re_query.query_struct(args.target, limit=args.limit)
    elif args.re_op == "resolve":
        value = re_query.query_resolve(args.target, index=args.index, limit=args.limit)
    elif args.re_op in ("function", "decompile"):
        value = re_query.query_function(args.target, index=args.index,
                                        max_bytes=args.max_bytes)
        if args.re_op == "decompile":
            value["operation"] = "decompile"
    elif args.re_op in ("callers", "callees"):
        value = re_query.query_edges(args.target, direction=args.re_op, index=args.index)
    elif args.re_op == "xrefs":
        value = re_query.query_xrefs(args.target, index=args.index)
    elif args.re_op == "abi":
        value = re_query.query_abi(args.target, index=args.index)
    else:
        raise ValueError(f"unknown re operation: {args.re_op}")
    status = value.get("status", "unavailable")
    result = dict(value.get("result") or {})
    result["truncated"] = bool(value.get("truncated"))
    return _envelope("eu4_re_query", status, result,
                     reason=value.get("reason", ""))


def eu4_research(args: argparse.Namespace) -> dict:
    """Translate the typed request to the sealed-snapshot research CLI.

    The CLI owns corpus validation, statuses and bounds; this adapter only
    validates the arguments it forwards and maps the document into the shared
    envelope.
    """
    from reverse_engineering import research

    operation = args.research_op
    if operation != "status" and not args.target:
        return _envelope("eu4_research", "error",
                         reason=f"{operation} requires a target RVA or symbol")
    if not research.MIN_MAX_BYTES <= args.max_bytes <= research.HARD_MAX_BYTES:
        return _envelope(
            "eu4_research", "error",
            reason=(f"max-bytes must be from {research.MIN_MAX_BYTES} to "
                    f"{research.HARD_MAX_BYTES}"))
    if args.limit is not None and args.limit < 1:
        return _envelope("eu4_research", "error",
                         reason="limit must be a positive integer")
    offsets = (args.static_offset, args.decompile_offset, args.incoming_offset,
               args.outgoing_offset, args.callsite_offset)
    if any(offset < 0 for offset in offsets):
        return _envelope("eu4_research", "error",
                         reason="offsets must be zero or positive")

    argv = [operation]
    if operation != "status":
        argv.append(args.target)
    if operation == "resolve":
        if args.limit is not None:
            argv += ["--limit", str(args.limit)]
    elif operation == "function":
        argv += ["--view", args.view,
                 "--static-offset", str(args.static_offset),
                 "--decompile-offset", str(args.decompile_offset)]
        if args.limit is not None:
            argv += ["--limit", str(args.limit)]
    elif operation == "xrefs":
        argv += ["--incoming-offset", str(args.incoming_offset),
                 "--outgoing-offset", str(args.outgoing_offset),
                 "--callsite-offset", str(args.callsite_offset)]
    if args.baseline_revision:
        argv += ["--baseline-revision", args.baseline_revision]
    argv += ["--max-bytes", str(args.max_bytes)]

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = research.main(argv)
    text = buffer.getvalue().strip()
    if not text:
        return _envelope(
            "eu4_research", "error",
            reason=(f"sealed-snapshot research rejected the request "
                    f"(exit {code})"))
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        return _envelope("eu4_research", "failed",
                         reason=f"research output is not JSON: {exc}")
    if not isinstance(document, dict):
        return _envelope("eu4_research", "failed",
                         reason="research output is not an object")
    status = str(document.get("status") or "unavailable")
    return _envelope("eu4_research", status, document,
                     reason=str(document.get("reason") or ""))


def discover_evidence(*, roots, query=None, classification=None, since=None,
                      limit=20, offset=0, locator_limit=None,
                      locator_offset=0) -> dict:
    return evidence_index.discover(
        configured_roots=roots,
        query=query,
        classification=classification,
        since=since,
        limit=limit,
        offset=offset,
        locator_limit=locator_limit,
        locator_offset=locator_offset,
    )


def eu4_session(args: argparse.Namespace) -> dict:
    if args.session_op == "list":
        listing = gfx_session.list_sessions(
            include_history=bool(getattr(args, "include_history", False)),
            status=getattr(args, "status", None),
            limit=int(getattr(args, "limit", 20) or 20),
            offset=int(getattr(args, "offset", 0) or 0),
        )
        reason = ""
        if listing["counts"]["total"] and not listing["returned"]:
            reason = (f"no {listing['filter']} sessions; "
                      f"{listing['counts']['total']} record(s) exist "
                      f"(closed={listing['counts']['closed']}, "
                      f"stale={listing['counts']['stale']}); pass "
                      "--include-history to inspect them")
        return _envelope("eu4_session", "ok", listing, reason=reason,
                         diagnostic="complete")
    if args.session_op == "lease":
        executable = Path(args.executable).expanduser() if args.executable else None
        if executable is None:
            from reverse_engineering import paths
            executable = paths.executable_path()
        return _envelope("eu4_session", "ok",
                         launch_lease.inspect(executable))
    if args.session_op == "discover":
        discovery = discover_evidence(
            roots=args.evidence_root,
            query=args.query,
            classification=args.classification,
            since=args.since,
            limit=int(getattr(args, "limit", 20) or 20),
            offset=int(getattr(args, "offset", 0) or 0),
            locator_limit=getattr(args, "locator_limit", None),
            locator_offset=int(
                getattr(args, "locator_offset", 0) or 0),
        )
        return _envelope("eu4_session", "ok", discovery)
    run = Path(args.run).expanduser().resolve()
    if args.session_op == "inspect":
        return _envelope("eu4_session", "ok", gfx_session.Session.load(run))
    if args.session_op == "terminate":
        state = _read_json(run / "session.json")
        worktree = Path(str(state.get("worktree", ""))).expanduser().resolve()
        if not getattr(args, "force", False) and worktree != ROOT.resolve():
            return _envelope(
                "eu4_session", "refused", reason=(
                    "session belongs to another worktree; pass --force only if "
                    "you own it"))
        processes = state.get("processes") or []
        results = []
        for row in processes:
            identity = gfx_session.ProcessIdentity.from_dict(row)
            results.append(gfx_session.terminate_owned(
                identity, grace_seconds=args.grace).as_dict())
        return _envelope("eu4_session", "ok", {
            "session_id": state.get("session_id"),
            "terminations": results,
        })
    raise ValueError(f"unknown session operation: {args.session_op}")


FRAME_STREAM_LIMIT = 200000
_FRAME_COMPACT_FIELDS = (
    "frame_index", "backend", "pid", "game_day", "update_identity",
    "caller_signature", "draws", "distinct_callers", "width", "height",
    "origin", "pixel_format", "sha256", "preview_sha256", "capture_valid",
    "gpu_completed", "skipped_before", "ts_ms", "error",
)


def _compact_frame(row: dict, role: str) -> dict:
    value = {name: row.get(name) for name in _FRAME_COMPACT_FIELDS}
    value["role"] = role
    return value


def _observation_streams(run: Path, role: str | None) -> list[tuple[str, Path]]:
    roles = [role] if role else ["reference", "candidate"]
    streams: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for name in roles:
        path = run / name / "observe" / "frame-observe.jsonl"
        if not path.is_file():
            path = run / "observe" / "frame-observe.jsonl"
        if not path.is_file() or path in seen:
            continue
        seen.add(path)
        streams.append((name, path))
    return streams


def _read_frames(run: Path, *, role: str | None, requested: list[int],
                 around: int, limit: int, offset: int,
                 full: bool) -> dict:
    if limit < 1:
        raise ValueError("frame limit must be >= 1")
    if offset < 0:
        raise ValueError("frame offset must be >= 0")
    if around < 0:
        raise ValueError("frame neighbour radius must be >= 0")
    wanted: set[int] = set()
    for index in requested:
        wanted.update(range(max(0, index - around), index + around + 1))
    streams = _observation_streams(run, role)
    if not streams:
        return {
            "run_dir": str(run), "streams": [], "frames": [], "count": 0,
            "matched": 0, "requested_frames": requested, "around": around,
            "offset": offset, "limit": limit, "truncated": False,
            "next_offset": None, "missing_frames": sorted(set(requested)),
            "missing_by_role": {}, "available_by_role": {},
            "counts": {"observations_scanned": 0, "matched": 0, "returned": 0,
                       "omitted": 0},
            "scan_truncated": False,
            "reason": f"no frame-observation stream in {run}",
        }
    matched: list[tuple[str, int, dict]] = []
    available: dict[str, list[int]] = {}
    found: set[int] = set()
    found_by_role: dict[str, set[int]] = {name: set() for name, _ in streams}
    stream_reports = []
    scanned = 0
    scan_truncated = False
    for name, path in streams:
        indices: list[int] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as source:
                for line in source:
                    if scanned >= FRAME_STREAM_LIMIT:
                        scan_truncated = True
                        break
                    scanned += 1
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(value, dict):
                        continue
                    index = value.get("frame_index")
                    if type(index) is not int:
                        continue
                    indices.append(index)
                    found_by_role[name].add(index)
                    if wanted and index not in wanted:
                        continue
                    found.add(index)
                    matched.append((name, index, value))
        except OSError:
            continue
        available[name] = indices
        stream_reports.append({
            "role": name, "path": str(path), "observations": len(indices),
            "frame_range": [min(indices), max(indices)] if indices else None,
        })
    matched.sort(key=lambda item: (item[0], item[1]))
    page = matched[offset:offset + limit]
    rows = [
        value if full else _compact_frame(value, name)
        for name, _index, value in page
    ]
    missing = sorted(set(requested) - found) if requested else []
    missing_by_role = {
        name: sorted(set(requested) - indices)
        for name, indices in found_by_role.items() if requested
    }
    return {
        "run_dir": str(run),
        "streams": stream_reports,
        "requested_frames": requested,
        "around": around,
        "roles": [name for name, _ in streams],
        "frames": rows,
        "count": len(rows),
        "returned": len(rows),
        "matched": len(matched),
        "offset": offset,
        "limit": limit,
        "truncated": offset + len(rows) < len(matched),
        "next_offset": (offset + len(rows)) if offset + len(rows) < len(matched) else None,
        "omitted": max(0, len(matched) - offset - len(rows)),
        "missing_frames": missing,
        "missing_by_role": {name: value for name, value in missing_by_role.items() if value},
        "available_by_role": {name: len(indices) for name, indices in available.items()},
        "counts": {
            "observations_scanned": scanned,
            "matched": len(matched),
            "returned": len(rows),
            "omitted": max(0, len(matched) - offset - len(rows)),
        },
        "scan_truncated": scan_truncated,
        "raw": bool(full),
    }


def eu4_capture(args: argparse.Namespace) -> dict:
    if args.capture_op == "gl-control":
        from automation import gfx_gl_control

        frame_dirs = []
        if args.frame_dir:
            frame_dirs = [Path(args.frame_dir)]
        elif args.run:
            run = Path(args.run).expanduser().resolve()
            frames = sorted((run / "frames").glob("frame-*"))
            frame_dirs = [item for item in frames if item.is_dir()]
            if not frame_dirs and (run / "frame-events.jsonl").is_file():
                frame_dirs = [run]
        if not frame_dirs:
            return _envelope(
                "eu4_capture", "error",
                reason="gl-control requires --frame-dir or a run with frames")
        reports = []
        for frame in frame_dirs:
            try:
                reports.append(gfx_gl_control.control_frame(frame))
            except gfx_gl_control.ControlError as exc:
                return _envelope(
                    "eu4_capture", "invalid",
                    {"frames": [str(item) for item in frame_dirs],
                     "kind": exc.kind, "reports": reports},
                    diagnostic="complete", verdict="unverified",
                    evidence="invalid", qualified=False,
                    reason=str(exc))
        statuses = {item["status"] for item in reports}
        if "invalid" in statuses:
            status = "invalid"
        elif reports and all(item["status"] == "match" for item in reports):
            status = "match"
        elif "different" in statuses:
            status = "different"
        else:
            status = "incomplete"
        value = {
            "schema": "gfx-gl-control-run/v1",
            "frames": reports,
            "status": status,
            "qualified": status == "match",
            "scope": "captured_transfer",
            "reason_codes": sorted({
                code for item in reports
                for code in (item.get("reason_codes") or [])}),
        }
        if status == "match":
            return _envelope(
                "eu4_capture", "ok", value, diagnostic="complete",
                verdict="equal", evidence="valid", qualified=True,
                reason="same-backend GL control matched with zero differing "
                       "pixels")
        if status == "different":
            return _envelope(
                "eu4_capture", "invalid", value, diagnostic="complete",
                verdict="different", evidence="invalid", qualified=False,
                reason="same-backend GL control differs; the capture/decoder "
                       "path is not exact")
        if status == "invalid":
            return _envelope(
                "eu4_capture", "invalid", value, diagnostic="invalid",
                verdict=None, evidence="invalid", qualified=False,
                reason="same-backend GL control found corrupt evidence")
        return _envelope(
            "eu4_capture", "incomplete", value, diagnostic="incomplete",
            verdict="unverified", evidence="unverified", qualified=False,
            reason="same-backend GL control could not establish the copied "
                   "destination version")
    if args.capture_op == "gl-control-qualify":
        from automation import gfx_gl_control_qualification as control_qualification

        if args.live_image is None or args.width is None \
                or args.height is None:
            return _envelope(
                "eu4_capture", "error",
                reason="gl-control-qualify requires --live-image, --width and "
                       "--height")
        if args.comparison_only:
            if args.control_image is None:
                return _envelope(
                    "eu4_capture", "error",
                    reason="--comparison-only requires --control-image")
            try:
                value = control_qualification.compare(
                    args.live_image, args.control_image,
                    args.width, args.height)
            except control_qualification.QualificationError as exc:
                return _envelope(
                    "eu4_capture", "invalid",
                    {"kind": exc.kind, "detail": exc.detail},
                    diagnostic="invalid", verdict=None, evidence="invalid",
                    qualified=False, reason=str(exc))
            return _envelope(
                "eu4_capture", "ok", value, diagnostic="complete",
                verdict="equal" if value["exact"] else "different",
                evidence="valid", qualified=False,
                reason=("BGRA comparison only; this is not an authenticated "
                        "control"))
        if args.frame_dir is None or args.renderer is None \
                or args.control_out is None \
                or args.checkpoint_event is None \
                or args.draw_event is None:
            return _envelope(
                "eu4_capture", "refused",
                reason="qualification requires --frame-dir, --renderer, "
                       "--control-out, --checkpoint-event and --draw-event; "
                       "pixel equality alone is not a control")
        try:
            value = control_qualification.qualify_sealed(
                args.live_image, args.width, args.height,
                frame_dir=args.frame_dir,
                checkpoint_event=args.checkpoint_event,
                draw_event=args.draw_event,
                renderer=args.renderer,
                out_dir=args.control_out,
                resource=args.resource,
                timeout=args.timeout if args.timeout is not None else 300.0,
                caller_drawable=({"path": str(args.drawable)}
                                 if args.drawable else None),
                caller_prestate=({"path": str(args.prestate)}
                                 if args.prestate else None))
        except control_qualification.QualificationError as exc:
            return _envelope(
                "eu4_capture", "invalid",
                {"kind": exc.kind, "detail": exc.detail},
                diagnostic="invalid", verdict=None, evidence="invalid",
                qualified=False, reason=str(exc))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return _envelope(
                "eu4_capture", "invalid",
                {"kind": "invalid_drawable_or_prestate", "detail": str(exc)},
                diagnostic="invalid", verdict=None, evidence="invalid",
                qualified=False, reason=str(exc))
        if value["qualified"]:
            return _envelope(
                "eu4_capture", "ok", value, diagnostic="complete",
                verdict="equal", evidence="valid", qualified=True,
                reason="executed authenticated original-API control "
                       "reproduces live GL exactly")
        return _envelope(
            "eu4_capture", "ok", value, diagnostic="complete",
            verdict="unqualified", evidence="valid", qualified=False,
            reason=("; ".join(
                (value.get("qualification") or {}).get("reasons") or [])
                or "; ".join(value.get("missing_observation") or [])
                or "control is not authenticated and exact"))

    if args.capture_op == "graph":
        if not args.frame_dir:
            return _envelope("eu4_capture", "error",
                             reason="graph requires --frame-dir")
        graph = gfx_frame_graph.build(Path(args.frame_dir))
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(
                json.dumps(graph, indent=2) + "\n", encoding="utf-8")
        status = {"complete": "ok", "partial": "partial"}.get(
            graph["status"], "invalid")
        if bool(getattr(args, "full", False)):
            return _envelope("eu4_capture", status, graph,
                             reason="; ".join(graph.get("reasons") or []))
        request = gfx_frame_graph.checkpoint_request(graph)
        rows = gfx_frame_graph.provenance_matrix(
            graph, request.get("checkpoints") or [])
        value = {
            "schema": graph.get("schema"),
            "status": graph.get("status"),
            "reasons": (graph.get("reasons") or [])[:12],
            "counts": graph.get("counts") or {},
            "boundary": graph.get("boundary") or {},
            "checkpoint_count": len(rows),
            "checkpoints": rows[:32],
            "truncated": len(rows) > 32,
            "authority": "graph-derived observation/writer rows; writer "
                         "history is empty only when the graph records no "
                         "prior same-resource writer",
        }
        return _envelope("eu4_capture", status, value,
                         reason="; ".join(graph.get("reasons") or []))
    run = Path(args.run).expanduser().resolve() if args.run else None
    if args.capture_op == "health":
        if run is None:
            return _envelope("eu4_capture", "error",
                             reason="health requires --run")
        sessions = _session_dirs(run)
        if len(sessions) > 1:
            limit = int(getattr(args, "limit", 20) or 20)
            page = sessions[:limit]
            rows = []
            for directory in page:
                result = _optional_json(directory / "result.json")
                view = _health_view(directory, result)
                rows.append({
                    "session_id": result.get("session_id") or directory.name,
                    "path": str(directory),
                    "health": {
                        "verdict": view["verdict"],
                        "agreement": view["agreement"],
                        "source": view["source"],
                    },
                })
            return _envelope("eu4_capture", "ok", {
                "campaign_root": str(run),
                "sessions": rows,
                "counts": {"sessions": len(sessions), "returned": len(rows),
                           "omitted": max(0, len(sessions) - len(rows))},
                "truncated": len(rows) < len(sessions),
                "next_offset": len(rows) if len(rows) < len(sessions) else None,
                "next": "pass a session directory for full health detail",
            }, diagnostic="complete")
        directory = sessions[0] if sessions else run
        result = _optional_json(directory / "result.json")
        view = _health_view(directory, result)
        gate = gfx_diagnostics.health_gate(
            view, result_requires_pixels=bool(args.require_pixels))
        evidence = {
            "valid": "valid", "invalid": "invalid",
            "unverified": "unverified", "failed": "invalid",
        }.get(gate.get("status"), None)
        if view["verdict"] == "unknown":
            evidence = "unverified"
        return _envelope("eu4_capture", "ok", {
            "run_dir": str(directory),
            "health": view,
            "gate": gate,
            "sources": view.get("sources"),
            "agreement": view.get("agreement"),
        }, reason=gate.get("reason") or "", verdict=view["verdict"],
            evidence=evidence, diagnostic="complete")
    if args.capture_op == "frames":
        if run is None:
            return _envelope("eu4_capture", "error",
                             reason="frames requires --run")
        frames = _read_frames(
            run,
            role=getattr(args, "role", None),
            requested=sorted({int(value) for value in (args.frame or [])}),
            around=int(getattr(args, "around", 0) or 0),
            limit=int(getattr(args, "limit", 20) or 20),
            offset=int(getattr(args, "offset", 0) or 0),
            full=bool(getattr(args, "full", False)),
        )
        if not frames["streams"]:
            return _envelope("eu4_capture", "unavailable", frames,
                             reason=frames["reason"], diagnostic="unavailable")
        if frames["missing_frames"]:
            return _envelope(
                "eu4_capture", "incomplete", frames, diagnostic="incomplete",
                reason=(f"requested frame(s) {frames['missing_frames']} are "
                        f"absent from the available streams; lower --around or "
                        f"inspect --full observations"))
        if frames["scan_truncated"]:
            return _envelope(
                "eu4_capture", "partial", frames, diagnostic="partial",
                reason=(f"stream scan stopped at {FRAME_STREAM_LIMIT} "
                        "observation lines; raise the bound or use --frame"))
        return _envelope("eu4_capture", "ok", frames)
    if args.capture_op == "request":
        if run is None:
            return _envelope("eu4_capture", "error",
                             reason="request requires --run")
        if not args.role:
            return _envelope("eu4_capture", "error",
                             reason="request requires --role")
        role_dir = run / args.role
        observe = role_dir / "observe"
        if not observe.is_dir():
            return _envelope("eu4_capture", "unavailable",
                             reason=f"no observation directory: {observe}")
        frames = [int(value) for value in args.frame]
        try:
            request_id = gfx_dual_run._request_dumps(
                {"observe_dir": observe}, frames)
        except gfx_dual_run.RequestError as exc:
            return _envelope("eu4_capture", "refused", reason=str(exc))
        request = observe / "capture-request.json"
        return _envelope("eu4_capture", "ok", {
            "request": str(request),
            "frames": sorted(set(frames)),
            "request_id": request_id,
            "capacity": gfx_dual_run.NATIVE_REQUEST_CAPACITY,
        })
    if args.capture_op == "probe":
        argv = ["automation.live_gfx", "probe", "--backend", "metal",
                "--checkpoint", args.checkpoint, "--occurrence", str(args.occurrence),
                "--output", args.output, "--campaign-run-root", args.campaign_run_root]
        if args.caller_rva:
            argv += ["--caller-rva", args.caller_rva]
        if args.shader_cache:
            argv += ["--shader-cache", args.shader_cache]
        if args.timeout:
            argv += ["--timeout", str(args.timeout)]
        run = _run_module("automation.live_gfx", argv[1:],
                          timeout=(args.timeout or 300) + 120)
        value = run["value"]
        report = _subprocess_report(run)
        if not isinstance(value, dict):
            return _envelope(
                "eu4_capture", "failed",
                {"subprocess": report},
                diagnostic="failed",
                reason=(f"probe subprocess failed (exit {run['exit_code']}): "
                        f"{run['parse_error'] or run['stderr'] or 'no output'}"))
        result = dict(value)
        result["subprocess"] = report
        flags = value.get("status") if isinstance(value.get("status"), dict) else {}
        overall = str(value.get("overall") or "")
        complete = (
            run["exit_code"] == 0
            and overall in ("presented", "captured")
            and flags.get("pixels_captured") is True
        )
        if complete:
            return _envelope("eu4_capture", "ok", result,
                             diagnostic="complete", verdict=overall or None,
                             evidence="valid")
        if run["exit_code"] in (2, 3):
            return _envelope("eu4_capture", "incomplete", result,
                             diagnostic="incomplete", verdict=overall or None,
                             evidence="unverified",
                             reason=(f"probe reported {overall or 'incomplete'} "
                                     f"(exit {run['exit_code']}); no accepted "
                                     "pixel result"))
        return _envelope(
            "eu4_capture", "unavailable", result,
            diagnostic="unavailable", verdict=overall or None,
            evidence="unverified",
            reason=(f"probe subprocess exited {run['exit_code']} without a "
                    f"complete result: {run['stderr'] or overall or 'no detail'}"))
    raise ValueError(f"unknown capture operation: {args.capture_op}")


def eu4_diff_run(args: argparse.Namespace) -> dict:
    scenario = gfx_scenario.load(Path(args.scenario))
    result = gfx_dual_run.run_campaign(
        scenario,
        run_root=Path(args.run_root),
        ledger_path=Path(args.ledger),
        shader_cache=Path(args.shader_cache) if args.shader_cache else None,
        qualified_sources=(Path(args.qualified_sources) if args.qualified_sources
                           else ROOT / "analysis" / "gfx-shader" / "v0" / "qualified-sources.json"),
        mode=args.mode,
        max_frames=args.max_frames,
        profile=args.profile,
        staggered=bool(getattr(args, "staggered", False)),
        stagger_wait_seconds=float(getattr(args, "stagger_wait", 600.0)),
        background=bool(getattr(args, "background", False)),
        display_name=getattr(args, "display_name", "EU4 Test"),
    )
    qualification = result.get("qualification")
    if qualification is None:
        qualification = gfx_dual_run.classify_campaign(
            result, scenario,
            max_frames=int(args.max_frames or scenario["observation"]["max_frames"]),
        )
    tool_error = result.get("error") is not None
    status = "error" if tool_error else "ok"
    comparison = (result.get("frames") or {}).get("comparison") or {}
    return _envelope("eu4_diff_run", status, {
        "run_dir": result.get("run_dir"),
        "error": result.get("error"),
        "execution": result.get("result", "incomplete"),
        "classification": qualification["status"],
        "qualified": qualification["qualified"],
        "qualification": qualification,
        "stop": result.get("stop"),
        "frames": (result.get("frames") or {}).get("paired"),
        "comparison": comparison,
        "health": (result.get("health") or {}).get("verdict"),
        "cleanup_errors": result.get("cleanup_errors") or [],
    }, reason=str(result.get("error") or ""),
        diagnostic="failed" if tool_error else "complete",
        verdict=_verdict_from_status(comparison.get("status")),
        evidence=("invalid" if tool_error else "valid"),
        qualified=bool(qualification["qualified"]))


def _frame_meta_entries(run: Path, role: str) -> tuple[dict[int, dict], list[str]]:
    """Collect preserved full-resolution frame metadata from a session/case."""
    metas: dict[int, dict] = {}
    notes: list[str] = []

    def add(value: dict | None) -> None:
        if not isinstance(value, dict):
            return
        meta = value.get("meta") if isinstance(value.get("meta"), dict) else value
        if not isinstance(meta, dict):
            return
        index = meta.get("frame_index", value.get("frame_index"))
        if type(index) is not int:
            return
        merged = dict(meta)
        if value.get("sha256") and not merged.get("sha256"):
            merged["sha256"] = value["sha256"]
        if value.get("raw_file") and not merged.get("raw_file"):
            merged["raw_file"] = value["raw_file"]
        if value.get("preserved") is False:
            notes.append(f"{role} frame {index} was not preserved")
            return
        metas.setdefault(index, merged)

    result = _optional_json(run / "result.json")
    for container in (result.get("dumps"), (result.get("pair") or {}).get("dumps")):
        payload = ((container or {}).get(role) or {})
        for item in payload.get("captured") or []:
            add(item)
        for item in (payload.get("found") or {}).values():
            add(item)
    repro = _optional_json(run / "repro" / "repro.json")
    for key, value in (repro.get("frames") or {}).items():
        if key == role and isinstance(value, dict):
            add(value)
    for directory in (run / role / "observe" / "captured", run / role, run / "frames"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            value = _optional_json(path)
            if value.get("raw_file") or value.get("schema") == "gfx-frame-dump/v1":
                add(value)
            elif value.get("sha256") and value.get("width"):
                add(value)
    for path in sorted((run / "frames").glob(f"{role}-frame-*.bgra")):
        try:
            index = int(path.stem.rsplit("-", 1)[-1])
        except ValueError:
            continue
        # Do not read or hash every payload while merely enumerating
        # candidates; the selected window is verified during decoding.
        metas.setdefault(index, {
            "frame_index": index,
            "raw_file": str(path),
        })
    return metas, notes


def _frame_candidates(run: Path, role: str, meta: dict) -> list[Path]:
    paths: list[Path] = []
    raw = meta.get("raw_file")
    if isinstance(raw, str) and raw:
        paths.append(Path(raw))
    index = meta.get("frame_index")
    for directory in (run / role / "observe" / "captured", run / role):
        if type(index) is int:
            paths.append(directory / f"frame-{index}.bgra")
    if type(index) is int:
        paths.append(run / "frames" / f"{role}-frame-{index}.bgra")
    return paths


TEMPORAL_MAX_WINDOW = 4
TEMPORAL_MAX_COMPARISONS = 12
TEMPORAL_MAX_SELECTION_PROBES = 16
TEMPORAL_MAX_METADATA_CANDIDATES = 4096


def _decode_frame_meta(run: Path, role: str, index: int,
                       meta: dict) -> tuple[dict | None, str]:
    path = next((item for item in _frame_candidates(run, role, meta)
                 if item.is_file()), None)
    if path is None:
        return None, "payload file is absent"
    try:
        payload = path.read_bytes()
    except OSError as exc:
        return None, f"payload is unreadable: {exc}"
    declared = meta.get("sha256")
    if declared is not None:
        if not isinstance(declared, str) or len(declared) != 64:
            return None, "declared payload digest is malformed"
        if hashlib.sha256(payload).hexdigest() != declared.lower():
            return None, "payload digest does not match its declaration"
    try:
        width = int(meta.get("width") or 0)
        height = int(meta.get("height") or 0)
        if width <= 0 or height <= 0:
            return None, "frame extent is absent or invalid"
        image = gfx_frame_compare.decode_frame(
            payload, width, height,
            origin=str(meta.get("origin") or "top_left"),
            pixel_format=str(meta.get("pixel_format") or "BGRA8"),
        )
    except (OSError, ValueError, TypeError, gfx_frame_compare.CompareError) as exc:
        return None, f"payload cannot be decoded: {exc}"
    return {
        "frame_index": index,
        "image": image,
        "sha256": declared,
        "identity": {
            key: meta.get(key) for key in (
                "backend", "game_day", "update_identity", "caller_signature",
                "draws", "width", "height", "origin", "pixel_format")
            if meta.get(key) is not None
        },
        "path": str(path),
    }, ""


def _load_preserved_frames(
    run: Path, role: str, indices: list[int] | None = None,
) -> tuple[list[dict], list[int], list[dict]]:
    """Index metadata cheaply and decode only the requested frame indices."""
    metas, _notes = _frame_meta_entries(run, role)
    if indices is None:
        selected = sorted(metas)
    else:
        selected = sorted({int(index) for index in indices})
    frames: list[dict] = []
    missing: list[int] = []
    problems: list[dict] = []
    for index in selected:
        meta = metas.get(index)
        if meta is None:
            missing.append(index)
            problems.append({"role": role, "frame_index": index,
                             "reason": "no preserved metadata"})
            continue
        frame, problem = _decode_frame_meta(run, role, index, meta)
        if frame is None:
            missing.append(index)
            problems.append({"role": role, "frame_index": index,
                             "reason": problem})
            continue
        frames.append(frame)
    return frames, sorted(set(missing)), problems


def _payload_available(run: Path, role: str, index: int, meta: dict) -> bool:
    """Cheap availability probe: the selected payload exists and fits metadata.

    This deliberately does not decode or hash the payload; decoding the chosen
    window is the authoritative check.  It prevents an automatic selection from
    preferring a metadata record whose payload is absent.
    """
    path = next((item for item in _frame_candidates(run, role, meta)
                 if item.is_file()), None)
    if path is None:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= 0:
        return False
    raw_bytes = meta.get("raw_bytes")
    if type(raw_bytes) is int and raw_bytes > 0:
        return size == raw_bytes
    width = meta.get("width")
    height = meta.get("height")
    if type(width) is int and width > 0 and type(height) is int and height > 0:
        return size == width * height * 4
    return True


def _temporal_report(run: Path, pair: dict | None,
                     args: argparse.Namespace) -> dict:
    scenario = _optional_json(run / "result.json").get("scenario") or {}
    policy = dict(scenario.get("comparison") or {})
    policy.setdefault("policy", "diagnostic")
    regions = policy.get("regions") or {}
    ref_metas, _ = _frame_meta_entries(run, "reference")
    cand_metas, _ = _frame_meta_entries(run, "candidate")
    reference_available = sorted(ref_metas)
    candidate_available = sorted(cand_metas)
    window = int(getattr(args, "temporal_window", 1) or 1)
    window = max(1, window)
    if window > TEMPORAL_MAX_WINDOW:
        return {
            "schema": gfx_frame_compare.TEMPORAL_SCHEMA,
            "status": "error",
            "reason": (
                f"temporal window {window} exceeds the documented bound "
                f"({TEMPORAL_MAX_WINDOW} consecutive frames per role)"),
            "bounds": {
                "max_window": TEMPORAL_MAX_WINDOW,
                "max_comparisons": TEMPORAL_MAX_COMPARISONS,
                "requested_window": window,
            },
        }
    explicit_reference = getattr(args, "reference_frame", None) is not None
    explicit_candidate = getattr(args, "candidate_frame", None) is not None
    requested_reference = getattr(args, "reference_frame", None)
    requested_candidate = getattr(args, "candidate_frame", None)
    if requested_reference is None:
        requested_reference = (pair or {}).get("frame_index")
    if requested_candidate is None:
        requested_candidate = (pair or {}).get("candidate_frame_index")

    def window_indices(start) -> list[int]:
        return ([start + offset for offset in range(window + 1)]
                if type(start) is int else [])

    def select(role: str, metas: dict[int, dict], preferred,
               explicit: bool) -> dict:
        available = sorted(metas)
        if explicit:
            indices = window_indices(preferred)
            frames, missing, problems = _load_preserved_frames(
                run, role, indices)
            return {
                "start": preferred,
                "indices": indices,
                "frames": frames,
                "missing": missing,
                "problems": problems,
                "source": "explicit",
                "fallback": False,
                "probes": 0,
                "candidates_considered": 0,
                "search_exhausted": False,
                "search_reason": "",
                "probe_problems": [],
            }
        starts: list[int] = []
        if type(preferred) is int and preferred in metas:
            starts.append(preferred)
        starts.extend(index for index in available if index not in starts)
        probes = 0
        considered = 0
        search_exhausted = False
        search_reason = ""
        probe_problems: list[dict] = []
        # Metadata presence and cheap payload availability are checked for
        # every candidate; only a candidate worth decoding consumes a probe.
        # The two bounds are separate so many metadata-only or missing-payload
        # frames cannot exhaust the decode allowance.
        for start in starts:
            if considered >= TEMPORAL_MAX_METADATA_CANDIDATES:
                search_exhausted = True
                search_reason = "metadata candidate scan bound reached"
                break
            considered += 1
            indices = window_indices(start)
            if not all(index in metas for index in indices):
                continue
            if not all(_payload_available(run, role, index, metas[index])
                       for index in indices):
                continue
            if probes >= TEMPORAL_MAX_SELECTION_PROBES:
                search_exhausted = True
                search_reason = "decode-attempt bound reached"
                break
            probes += 1
            frames, missing, problems = _load_preserved_frames(
                run, role, indices)
            probe_problems.extend(problems)
            if not missing and not problems:
                fallback = type(preferred) is int and start != preferred
                return {
                    "start": start,
                    "indices": indices,
                    "frames": frames,
                    "missing": missing,
                    "problems": problems,
                    "source": ("recorded_pair" if start == preferred
                               else "fallback_window" if type(preferred) is int
                               else "first_usable"),
                    "fallback": fallback,
                    "probes": probes,
                    "candidates_considered": considered,
                    "search_exhausted": False,
                    "search_reason": "",
                    "probe_problems": probe_problems,
                }
        # No complete usable window.  Keep the recorded pair (or the first
        # available index) and report exactly why it is incomplete.
        start = (preferred if type(preferred) is int and preferred in metas
                 else available[0] if available else None)
        indices = window_indices(start)
        frames, missing, problems = _load_preserved_frames(run, role, indices)
        return {
            "start": start,
            "indices": indices,
            "frames": frames,
            "missing": missing,
            "problems": problems,
            "source": "first_available",
            "fallback": type(preferred) is int and start != preferred,
            "probes": probes,
            "candidates_considered": considered,
            "search_exhausted": search_exhausted,
            "search_reason": search_reason,
            "probe_problems": probe_problems,
        }

    reference_selection = select("reference", ref_metas, requested_reference,
                                explicit_reference)
    candidate_selection = select("candidate", cand_metas, requested_candidate,
                                 explicit_candidate)
    selected_reference = reference_selection["start"]
    selected_candidate = candidate_selection["start"]
    reference_indices = reference_selection["indices"]
    candidate_indices = candidate_selection["indices"]
    reference_frames = reference_selection["frames"]
    candidate_frames = candidate_selection["frames"]
    reference_missing = reference_selection["missing"]
    candidate_missing = candidate_selection["missing"]
    reference_problems = reference_selection["problems"]
    candidate_problems = candidate_selection["problems"]
    reference_pairs = ([(index, index + 1)
                        for index in range(selected_reference,
                                           selected_reference + window)]
                       if type(selected_reference) is int else [])
    candidate_pairs = ([(index, index + 1)
                        for index in range(selected_candidate,
                                           selected_candidate + window)]
                       if type(selected_candidate) is int else [])
    cross_pairs = ([(int(selected_reference), int(selected_candidate))]
                   if type(selected_reference) is int
                   and type(selected_candidate) is int else [])
    requested_comparisons = (len(reference_pairs) + len(candidate_pairs)
                             + len(cross_pairs))
    if requested_comparisons > TEMPORAL_MAX_COMPARISONS:
        return {
            "schema": gfx_frame_compare.TEMPORAL_SCHEMA,
            "status": "error",
            "reason": (
                f"{requested_comparisons} requested temporal comparisons "
                f"exceed the documented bound ({TEMPORAL_MAX_COMPARISONS})"),
            "bounds": {
                "max_window": TEMPORAL_MAX_WINDOW,
                "max_comparisons": TEMPORAL_MAX_COMPARISONS,
                "requested_comparisons": requested_comparisons,
            },
        }

    artifacts = None
    if getattr(args, "temporal_artifacts", False):
        directory = getattr(args, "temporal_artifact_dir", None)
        artifacts = {
            "dir": Path(directory) if directory else run / "temporal",
            "amplification": 4,
        }
    report = gfx_frame_compare.compare_temporal(
        reference_frames, candidate_frames,
        policy=policy, regions=regions,
        cross_pairs=cross_pairs,
        reference_consecutive=reference_pairs,
        candidate_consecutive=candidate_pairs,
        max_pairs=min(TEMPORAL_MAX_COMPARISONS,
                      max(1, requested_comparisons)),
        artifacts=artifacts,
    )
    report["selected"] = {
        "reference": selected_reference,
        "candidate": selected_candidate,
        "window": window,
        "explicit": {
            "reference": explicit_reference,
            "candidate": explicit_candidate,
        },
        "requested_pair": {
            "reference": requested_reference,
            "candidate": requested_candidate,
        },
        "source": {
            "reference": reference_selection["source"],
            "candidate": candidate_selection["source"],
        },
        "fallback": {
            "reference": reference_selection["fallback"],
            "candidate": candidate_selection["fallback"],
        },
        "probes": {
            "reference": reference_selection["probes"],
            "candidate": candidate_selection["probes"],
        },
        "candidates_considered": {
            "reference": reference_selection["candidates_considered"],
            "candidate": candidate_selection["candidates_considered"],
        },
        "search_exhausted": {
            "reference": reference_selection["search_exhausted"],
            "candidate": candidate_selection["search_exhausted"],
        },
        "search_reason": {
            "reference": reference_selection["search_reason"],
            "candidate": candidate_selection["search_reason"],
        },
        "indices": {
            "reference": reference_indices,
            "candidate": candidate_indices,
        },
    }
    report["fallback_used"] = bool(
        reference_selection["fallback"] or candidate_selection["fallback"])
    report["selection_search_exhausted"] = bool(
        reference_selection["search_exhausted"]
        or candidate_selection["search_exhausted"])
    report["preserved"] = {
        "reference": reference_available,
        "candidate": candidate_available,
    }
    report["missing"] = {
        "reference": reference_missing,
        "candidate": candidate_missing,
        "requested_reference": [index for index in reference_indices
                                if index not in ref_metas],
        "requested_candidate": [index for index in candidate_indices
                                if index not in cand_metas],
    }
    report["invalid"] = (
        reference_problems + candidate_problems
        + reference_selection.get("probe_problems", [])
        + candidate_selection.get("probe_problems", []))
    report["bounds"] = {
        "max_window": TEMPORAL_MAX_WINDOW,
        "max_comparisons": TEMPORAL_MAX_COMPARISONS,
        "requested_window": window,
        "requested_comparisons": requested_comparisons,
    }
    return report


def _verdict_from_status(status: str | None) -> str | None:
    return {
        gfx_frame_compare.PASS: "equal",
        gfx_frame_compare.FAIL: "different",
        gfx_frame_compare.UNVERIFIED: "unverified",
    }.get(str(status or ""), None)


def eu4_diff_inspect(args: argparse.Namespace) -> dict:
    run = Path(args.run).expanduser().resolve()
    sessions = _session_dirs(run)
    limit = int(getattr(args, "limit", 20) or 20)
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if int(getattr(args, "offset", 0) or 0) < 0:
        raise ValueError("offset must be >= 0")
    if not sessions:
        return _envelope(
            "eu4_diff_inspect", "not_found", {
                "run_dir": str(run),
                "exists": run.exists(),
            }, diagnostic="unavailable",
            reason=(f"no result.json at {run} and no session-* child with a "
                    "result.json; pass a session directory or a campaign root"))
    if len(sessions) > 1:
        offset = int(getattr(args, "offset", 0) or 0)
        page = sessions[offset:offset + limit]
        rows = [_session_summary(directory) for directory in page]
        return _envelope("eu4_diff_inspect", "ok", {
            "campaign_root": str(run),
            "sessions": rows,
            "counts": {
                "sessions": len(sessions), "returned": len(rows),
                "omitted": max(0, len(sessions) - offset - len(rows)),
            },
            "offset": offset,
            "limit": limit,
            "truncated": offset + len(rows) < len(sessions),
            "next_offset": (offset + len(rows)
                            if offset + len(rows) < len(sessions) else None),
            "next": ("select one session with --run <session-directory>; a "
                     "campaign root with several sessions is never "
                     "auto-selected"),
        }, diagnostic="complete")
    directory = sessions[0]
    result = _read_json(directory / "result.json")
    frames = result.get("frames") or {}
    comparison = frames.get("comparison") or {}
    pair = comparison.get("first_failure") or frames.get("last_pair")
    recomputed, recompute_error = _recompute_classification(result)
    recorded = result.get("classification")
    current = (recomputed or {}).get("status") if recomputed else recorded
    classification = current or recorded or "incomplete"
    health = _health_view(directory, result)
    staticity = _staticity(directory, result)
    temporal = None
    if getattr(args, "temporal", False):
        temporal = _temporal_report(directory, pair, args)
    missing = _missing_evidence(result, pair, staticity, health)
    steps = _next_steps(classification, health, staticity, pair)
    package = None
    if args.build_case:
        package = gfx_case.build_case(directory)
    pair_comparison = (pair or {}).get("comparison") or {}
    alignment = (pair_comparison.get("alignment")
                 or comparison.get("alignment")
                 or result.get("alignment") or {})
    full = bool(getattr(args, "full", False))
    raw_comparison = comparison
    comparison_summary = {
        "status": comparison.get("status"),
        "compared": comparison.get("compared"),
        "failed": comparison.get("failed"),
        "unverified": comparison.get("unverified"),
        "not_compared": comparison.get("not_compared"),
        "reasons": (pair_comparison.get("reasons") or [])[:8],
        "first_failure": _compact_pair(pair),
    }
    compact_pair = _compact_pair(pair)
    dumps = (pair or {}).get("dumps") or {}
    has_payloads = bool((dumps.get("reference") or {}).get("found")) and bool(
        (dumps.get("candidate") or {}).get("found"))
    if classification in ("gpu_execution_failure", "process_failure",
                          "infrastructure_failure"):
        evidence = "invalid"
    elif has_payloads:
        evidence = "valid"
    else:
        evidence = "unverified"
    diagnostic = "complete"
    reason = ""
    temporal_status = temporal.get("status") if temporal is not None else None
    if temporal_status == "error":
        diagnostic = "refused"
        reason = str(temporal.get("reason") or "temporal diagnostics refused")
    elif temporal_status in ("incomplete", "partial"):
        diagnostic = "incomplete"
        reason = str(temporal.get("reason") or
                     "temporal diagnostics are not complete")
    processes = {
        role: {
            "exit_code": (value or {}).get("exit_code"),
            "exit_code_before_stop": (value or {}).get("exit_code_before_stop"),
        }
        for role, value in (result.get("processes") or {}).items()
        if isinstance(value, dict)
    }
    payload = {
        "run_dir": str(directory),
        "campaign_root": str(run) if directory != run else None,
        "execution": {
            "result": result.get("result"),
            "stop": result.get("stop"),
            "duration_seconds": result.get("duration_seconds"),
            "processes": processes,
            "cleanup_errors": result.get("cleanup_errors") or [],
        },
        "selected_session": (result.get("session_id") or directory.name
                             if directory != run else None),
        "stop": result.get("stop"),
        "health": health,
        "classification": classification,
        "recorded_classification": recorded,
        "classification_change": (
            None if recorded is None or current is None
            else recorded != current),
        "reclassification": {
            "status": "current" if recomputed else "unavailable",
            "source": "gfx_dual_run.classify_campaign",
            "reasons": (recomputed or {}).get("reasons") or [],
            "error": recompute_error or None,
        },
        "qualification": result.get("qualification"),
        "correspondence": {
            "status": alignment.get("status"),
            "evidence": alignment.get("evidence"),
            "extent": alignment.get("extent"),
            "identity_equal": alignment.get("identity_equal"),
            "staticity": staticity,
        },
        "staticity": staticity,
        "comparison": raw_comparison if full else comparison_summary,
        "first_pair": pair if full else None,
        "first_discrepancy": compact_pair,
        "missing_evidence": missing,
        "next_steps": steps,
        "next": steps[0] if steps else "",
        "case": package,
        "temporal": temporal,
        "temporal_status": temporal_status,
    }
    envelope_status = ("ok" if diagnostic == "complete"
                       else "refused" if diagnostic == "refused"
                       else "incomplete")
    return _envelope(
        "eu4_diff_inspect", envelope_status,
        payload, reason=reason, diagnostic=diagnostic,
        verdict=_verdict_from_status(comparison.get("status")),
        evidence=evidence,
        qualified=(result.get("qualification") or {}).get("qualified"))


def _dependency_failure(tool: str, path: Path | None = None) -> dict | None:
    """Return an actionable unavailable envelope when NumPy is absent."""
    if gfx_frame_compare.np is not None:
        return None
    runtime = _runtime_block()
    return _envelope(
        tool, "unavailable", {
            "path": str(path) if path is not None else None,
            "execution": "not_run",
            "dependency": runtime["numpy"],
        }, diagnostic="unavailable", reason=(
            "numpy is required for frame comparison but is not importable by "
            f"{runtime['executable']} (EU4_DSH_PYTHON={runtime['configured']!r}). "
            "Install numpy for that interpreter or point EU4_DSH_PYTHON at an "
            "absolute interpreter that has it; run "
            "`python -m automation.python_runtime resolve` for a checked "
            "diagnosis. This is an unavailable diagnostic, not a pass or a "
            "rendering verdict."))


def _case_replay_outcome(result: dict) -> tuple[str, str, str | None, str | None]:
    """Map a case-repro status to (status, diagnostic, verdict, evidence)."""
    replay_status = str(result.get("status") or "unavailable")
    reason = str(result.get("reason") or "")
    verdict = _verdict_from_status(replay_status)
    if verdict is not None:
        return "ok", "complete", verdict, "valid"
    if "numpy" in reason.lower() or "unavailable" in replay_status:
        return "unavailable", "unavailable", None, None
    return "incomplete", "incomplete", None, "unverified"


def _case_verification_outcome(verification: dict) -> tuple[str, str, str]:
    """Map a Strategy B verification to (status, diagnostic, evidence)."""
    if verification.get("verifier_exception"):
        return "invalid", "invalid", "invalid"
    if verification.get("ok"):
        return "ok", "complete", "valid"
    problems = [str(item) for item in verification.get("problems") or []]
    expected_incomplete = [item for item in problems
                           if item == "reproduction is explicitly incomplete"]
    additional = [item for item in problems
                  if item not in expected_incomplete]
    if (verification.get("reproduction_status") == "incomplete"
            and expected_incomplete and not additional):
        return "incomplete", "incomplete", "unverified"
    return "invalid", "invalid", "invalid"


def _case_verification_view(verification: dict) -> dict:
    return {
        "schema": verification.get("schema"),
        "ok": verification.get("ok"),
        "case_id": verification.get("case_id"),
        "problems": verification.get("problems") or [],
        "files": verification.get("files"),
        "graph_status": verification.get("graph_status"),
        "sequence": verification.get("sequence") or {},
        "reproduction_status": verification.get("reproduction_status"),
        "reproduction_reason": verification.get("reproduction_reason"),
        "verifier_exception": bool(verification.get("verifier_exception")),
        "exception": verification.get("exception"),
        "independently_reproducible": verification.get(
            "independently_reproducible"),
    }


def _case_sequence_view(case_path: Path) -> dict:
    """Summarize the v2 ordered prefix without copying payloads."""
    sequence = _optional_json(case_path / "sequence.json")
    frame_keys = (
        "index", "dir", "capture_frame_id", "original_sequence_index",
        "manifest_sha256", "event_stream_sha256", "reference_sha256",
        "shader_dir", "shader_report_sha256", "checkpoint_count", "graph_status",
    )
    return {
        **{key: sequence.get(key) for key in
           ("schema", "session_id", "pid", "target_index", "minimal_prefix", "excluded_frames")},
        "frames": [{key: entry.get(key) for key in frame_keys}
                   for entry in sequence.get("frames") or [] if isinstance(entry, dict)],
        "carried_resources": sequence.get("carried_resources") or [],
        "reproduction": sequence.get("reproduction") or {},
    }



COMPACT_OUTPUT_CAP = 8192
COMPACT_CHECKPOINT_LIMIT = 8
COMPACT_INPUT_LIMIT = 6


def _diagnosis_report(case_path: Path, manifest: dict) -> dict:
    """Load the existing diagnostic report; never synthesize one."""
    relative = (manifest.get("diagnostic_report")
                or manifest.get("report"))
    names = [relative] if isinstance(relative, str) and relative else [
        "diagnostic-report.json", "report.json"]
    for name in names:
        path = case_path / str(name)
        if path.is_file():
            value = _optional_json(path)
            if value:
                inner = value.get("diagnosis")
                if isinstance(inner, dict) and ("pixels" in inner
                                                or "classification" in inner):
                    return inner
                return value
    return {}


def _artifact_view(case_path: Path) -> dict:
    """Names and sizes only; full artifacts stay on disk."""
    artifacts = {}
    for name in ("diagnostic-report.json", "agent-diagnosis.json",
                 "replay/replay.json", "replay/metal-frame.bgra",
                 "difference.bgra", "graph.json"):
        path = case_path / name
        if path.is_file():
            artifacts[name] = {
                "bytes": path.stat().st_size,
                "path": name,
            }
    return artifacts


def _case_capture_identity(case_path: Path) -> dict:
    seal = _optional_json(case_path / "capture" / "frame-seal.json")
    return {
        key: seal.get(key) for key in
        ("session_id", "capture_frame_id", "present_index", "scene_index",
         "event_stream_sha256", "reference_sha256")
    } if seal else {}


def _compact_case_view(case_path: Path, schema: str, manifest: dict,
                       verification: dict) -> dict:
    """Bounded case summary for a routine agent read.

    The full report, command streams and screenshots remain on disk.  This
    projection carries only identity, qualification, the first divergence, the
    selected checkpoint statistics, the next actionable checkpoint and
    artifact names/sizes.  It never promotes incomplete evidence.
    """
    report = _diagnosis_report(case_path, manifest)
    if report:
        diagnosis = gfx_independent_replay.compact_diagnosis(report)
        diagnosis["inputs"] = (diagnosis.get("inputs") or [])[
            :COMPACT_INPUT_LIMIT]
        diagnosis["checkpoints"] = (diagnosis.get("checkpoints") or [])[
            :COMPACT_CHECKPOINT_LIMIT]
        if isinstance(diagnosis.get("checkpoint_selection"), dict):
            diagnosis["checkpoint_selection"] = {
                key: diagnosis["checkpoint_selection"].get(key)
                for key in ("total", "selected", "bounded")
            }
        diagnosis.pop("artifacts", None)
        first = diagnosis.get("first_divergence")
        if isinstance(first, dict):
            for key in ("recommended_checkpoint", "suggested_checkpoint",
                        "recommended_action", "cross_capture_excluded",
                        "copy_source_evidence", "reason"):
                first.pop(key, None)
            interval = first.get("unresolved_interval")
            if isinstance(interval, dict):
                interval["note"] = (
                    "search_start_event is an operational search bound, not "
                    "a matching observation")
    else:
        diagnosis = {
            "schema": "gfx-agent-diagnosis/unavailable",
            "classification": manifest.get("classification"),
            "qualified": None,
            "reason": "the case has no readable diagnostic report",
        }
    value = {
        "schema": schema,
        "view_schema": "gfx-agent-case/v1",
        "case_id": manifest.get("case_id"),
        "title": manifest.get("title"),
        "verification": {
            "ok": verification.get("ok"),
            "case_id": verification.get("case_id"),
            "problems": (verification.get("problems") or [])[:6],
            "files": verification.get("files"),
            "graph_status": verification.get("graph_status"),
            "reproduction_status": verification.get("reproduction_status"),
            "verifier_exception": bool(verification.get("verifier_exception")),
            "independently_reproducible": verification.get(
                "independently_reproducible"),
            "producer_identity": (verification.get("producer_identity") or {}).get(
                "status"),
        },
        "capture": _case_capture_identity(case_path),
        "diagnosis": diagnosis,
        "terminal_failure": manifest.get("terminal_failure"),
        "artifacts": _artifact_view(case_path),
        "files": len(manifest.get("files") or []),
        "next": ("repair the case evidence before replay"
                 if not verification.get("ok")
                 else "reuse the verified case; do not recapture for the same "
                      "identity"),
    }
    if schema == gfx_replay_case.SEQUENCE_SCHEMA:
        sequence = _case_sequence_view(case_path)
        frames = []
        for frame in (sequence.get("frames") or [])[:4]:
            frames.append({
                key: frame.get(key) for key in
                ("index", "dir", "capture_frame_id",
                 "original_sequence_index", "event_stream_sha256",
                 "checkpoint_count", "graph_status")
            })
        value["sequence"] = {
            "session_id": sequence.get("session_id"),
            "target_index": sequence.get("target_index"),
            "frames": frames,
            "minimal_prefix": sequence.get("minimal_prefix"),
            "excluded_frames": sequence.get("excluded_frames"),
            "carried_resources": (sequence.get("carried_resources") or [])[:8],
        }
    value["truncated"] = False
    value["output_cap_bytes"] = COMPACT_OUTPUT_CAP
    return value


def _json_text(value) -> str:
    """The exact envelope serialization used on stdout (UTF-8, no escaping)."""
    if isinstance(value, dict) and (value.get("result") or {}).get("schema") in (
            "gfx-event-evidence/v1", "eu4-revision-qualification/v1"):
        return json.dumps(value, separators=(",", ":"),
                          ensure_ascii=False) + "\n"
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _utf8_len(value) -> int:
    return len(_json_text(value).encode("utf-8"))


def _emit_text(value) -> str:
    """Serialize the emitted envelope and report its exact UTF-8 size."""
    text = _json_text(value)
    result = value.get("result") if isinstance(value.get("result"), dict) else None
    if result is None or "output_bytes" not in result:
        return text
    for _attempt in range(4):
        size = len(text.encode("utf-8"))
        if result.get("output_bytes") == size:
            break
        result["output_bytes"] = size
        text = _json_text(value)
    return text


def _essential_divergence(first: dict) -> dict:
    result = {key: first[key] for key in (
        "kind", "event", "target", "resource", "operation", "program",
        "producer_event", "confidence",
    ) if key in first}
    if first.get("root_cause") is not None:
        result["root_cause"] = _truncate_utf8(first["root_cause"], 120)
    interval = first.get("unresolved_interval")
    if isinstance(interval, dict):
        result["unresolved_interval"] = {key: interval.get(key) for key in (
            "start_event", "end_event", "lower_bound", "search_start_event",
        )}
    for name in ("next_checkpoint", "recommended_checkpoint"):
        value = first.get(name)
        if isinstance(value, dict):
            result[name] = {key: value[key] for key in (
                "event", "target", "resource", "timing", "requires_capture",
            ) if key in value}
            if "reason" in value:
                result[name]["reason"] = _truncate_utf8(value["reason"], 80)
        elif isinstance(value, (str, int)) and _utf8_len(value) <= 256:
            result[name] = value
    if isinstance(first.get("limitations"), list):
        result["limitations"] = [_truncate_utf8(item, 80) for item in first["limitations"][:2]]
    return result


def _essential_compact(result: dict, original: int, cap: int) -> dict:
    """Smallest projection that still carries the evidence contract.

    Identity, verification state, qualification, first divergence, unresolved
    interval, recommended checkpoint and artifact references are never removed.
    Only descriptions, selected inputs/checkpoints and optional axes are shed.
    """
    diagnosis = result.get("diagnosis") if isinstance(
        result.get("diagnosis"), dict) else {}
    first = diagnosis.get("first_divergence") if isinstance(
        diagnosis.get("first_divergence"), dict) else {}
    artifacts = result.get("artifacts")
    if isinstance(artifacts, dict):
        artifacts = {
            name: {"bytes": value.get("bytes"), "path": value.get("path")}
            if isinstance(value, dict) else value
            for name, value in list(artifacts.items())[:6]
        }
    verification = result.get("verification") if isinstance(
        result.get("verification"), dict) else {}
    value = {
        "schema": result.get("schema"),
        "case_id": result.get("case_id"),
        "capture": result.get("capture") or {},
        "verification": {
            "ok": verification.get("ok"),
            "case_id": verification.get("case_id"),
            "independently_reproducible": verification.get(
                "independently_reproducible"),
            "graph_status": verification.get("graph_status"),
            "problems": (verification.get("problems") or [])[:2],
        },
        "diagnosis": {
            "schema": diagnosis.get("schema"),
            "classification": diagnosis.get("classification"),
            "qualified": diagnosis.get("qualified"),
            "first_divergence": _essential_divergence(first),
            "final": diagnosis.get("final"),
            "next_action": diagnosis.get("next_action"),
        },
        "artifacts": artifacts,
        "files": result.get("files"),
        "truncated": True,
        "output_bytes_before_cap": original,
        "output_cap_bytes": cap,
    }
    return value


def _essential_cap_error(value: dict, cap: int, original: int, *, required_fields=()) -> dict:
    """A bounded error when essential evidence cannot fit the cap.

    Silently dropping identity, provenance or the causal interval would make a
    truncated result look complete.  Failing explicitly is the only safe
    option.
    """
    result = {
        "truncated": True,
        "output_cap_bytes": cap,
        "output_bytes_before_cap": original,
        "error": "essential_fields_exceed_cap",
        "detail": "the essential fields exceed the compact output cap",
    }
    if required_fields:
        result["required_fields"] = list(required_fields)
    envelope = _envelope(
        str(value.get("tool") or "eu4_case_inspect"), "failed", result,
        reason="essential diagnostic fields exceed the compact output cap",
        diagnostic="failed", evidence="invalid", qualified=False)
    failure = {"kind": "output_cap_exceeded", "class": "failure",
               "detail": "essential evidence could not be represented within "
                         "the output cap"}
    envelope["failure"] = failure
    envelope["outcome"]["failure_kind"] = failure["kind"]
    envelope["outcome"]["failure_class"] = failure["class"]
    envelope["truncated"] = True
    envelope["output_cap_bytes"] = cap
    envelope["output_bytes_before_cap"] = original
    # The error envelope is small, but keep the hard guarantee if a caller
    # deliberately sets a tiny cap.
    if _utf8_len(envelope) > cap:
        envelope.pop("runtime", None)
    if _utf8_len(envelope) > cap:
        envelope["reason"] = "output cap exceeded"
    if _utf8_len(envelope) > cap:
        result.pop("required_fields", None)
        result.pop("detail", None)
    return _measured_envelope(envelope)


def _measured_envelope(value: dict) -> dict:
    result = value["result"]
    result["output_bytes"] = 0
    for _ in range(3):
        result["output_bytes"] = _utf8_len(value)
    return value


def _bounded_envelope(value: dict, cap: int = COMPACT_OUTPUT_CAP) -> dict:
    """Keep the complete compact view or one fixed evidence projection."""
    original = _utf8_len(value)
    normal = _measured_envelope({**value, "result": dict(value.get("result") or {})})
    if _utf8_len(normal) <= cap:
        return normal
    result = _essential_compact(value.get("result") or {}, original, cap)
    if diagnosis := result.get("diagnosis"):
        diagnosis.pop("final", None)
        diagnosis["next_action"] = _truncate_utf8(diagnosis.get("next_action"), 160)
    result["verification"]["problems"] = [
        _truncate_utf8(item, 80) for item in result["verification"]["problems"]]
    result.pop("files", None)
    projected = _measured_envelope({
        **{key: value[key] for key in ("schema", "tool", "ok", "status", "outcome", "failure")
           if key in value},
        "reason": _truncate_utf8(value.get("reason"), 120), "result": result,
        "truncated": True, "output_cap_bytes": cap, "output_bytes_before_cap": original,
        "truncated_fields": ["optional_diagnostics"],
    })
    if _utf8_len(projected) <= cap:
        return projected
    return _essential_cap_error(value, cap, original, required_fields=(
        "capture_identity", "verification", "first_divergence",
        "unresolved_interval", "next_checkpoint", "artifact_references",
    ))


def _diagnosis_outcome(report: dict, *, verification_ok: bool = True,
                       reproduction_complete: bool = True
                       ) -> tuple[str, str, str | None, str | None, bool]:
    """Map a canonical diagnosis to the envelope outcome without promotion.

    This delegates to the single shared projection in
    :mod:`automation.gfx_independent_replay`.  Incomplete or invalid evidence
    never becomes a qualified result, and a completed difference is a completed
    diagnostic rather than a tool failure.
    """
    if not verification_ok:
        return "invalid", "invalid", None, "invalid", False
    if not reproduction_complete:
        return "incomplete", "incomplete", None, "unverified", False
    canonical = gfx_independent_replay.classification_outcome(report)
    status = canonical["status"]
    if status not in ("ok", "invalid", "failed", "incomplete"):
        status = "incomplete"
    return (status, canonical["diagnostic"], canonical["verdict"],
            canonical["evidence"], canonical["qualified"])


_CAMPAIGN_TERMINAL_CLASSIFICATIONS = {
    "gpu_execution_failure", "invalid_provenance", "incomplete_capture",
    "incomplete_diagnostics",
}
_CAMPAIGN_TERMINAL_REASONS = {
    "gpu_execution_failure", "history_invalid", "host_failure",
    "frame_timeout", "overall_timeout", "frame_identity", "sequence_order",
    "tool_failure",
}
_CAMPAIGN_PIXEL_CLASSIFICATIONS = {
    "qualified_difference", "difference_with_partial_state_coverage",
    "unsupported_state",
}
_CAMPAIGN_PIXEL_AXIS = "divergent"


def _campaign_terminal_failure(report: dict) -> dict | None:
    """Return the first terminal execution/history/infrastructure failure.

    A terminal failure outranks every pixel result, including an earlier
    qualified difference.  A completed prefix that matched must never make a
    failed campaign look successful.
    """
    for row in report.get("frames") or []:
        if not isinstance(row, dict):
            continue
        sequence = row.get("sequence")
        if row.get("ok") is False:
            return {
                "kind": "gpu_execution_failure",
                "frame": sequence,
                "sequence": sequence,
                "detail": str(row.get("error") or row.get("stage")
                              or "Metal execution failed"),
            }
        if row.get("history_status") not in (1, None):
            return {
                "kind": "resource_history_failure",
                "frame": sequence,
                "sequence": sequence,
                "detail": f"history_status={row.get('history_status')}"
                          + (f": {row.get('error')}" if row.get("error") else ""),
            }
        classification = row.get("classification")
        if classification in _CAMPAIGN_TERMINAL_CLASSIFICATIONS:
            return {
                "kind": str(classification),
                "frame": sequence,
                "sequence": sequence,
                "detail": str((row.get("first_divergence") or {}).get("reason")
                              or row.get("error") or classification),
            }
    status = report.get("status")
    stop_reason = report.get("stop_reason")
    if status == "failed":
        return {
            "kind": str(stop_reason or "campaign_failure"),
            "frame": report.get("stopped_at"),
            "sequence": report.get("stopped_at"),
            "detail": str(report.get("error") or stop_reason
                          or "campaign failed"),
        }
    if stop_reason in _CAMPAIGN_TERMINAL_REASONS:
        return {
            "kind": str(stop_reason),
            "frame": report.get("stopped_at"),
            "sequence": report.get("stopped_at"),
            "detail": str(report.get("error") or stop_reason),
        }
    return None


def _campaign_row_trusted(row: dict) -> bool:
    """True when a row's pixel/checkpoint evidence is trustworthy.

    Only a frame whose diagnostic actually completed can supply pixel,
    checkpoint or classification evidence.  A terminal row (GPU failure,
    invalid provenance, incomplete capture or diagnostics), a failed history,
    or an explicitly invalid capture/provenance axis must never be promoted
    into a verified first-divergence claim.  Missing axes remain compatible
    with older synthetic reports; a missing diagnostic-completion marker does
    not.
    """
    if row.get("diagnostic_completed") is not True:
        return False
    if row.get("ok") is False:
        return False
    if row.get("history_status") not in (1, None):
        return False
    if row.get("classification") in _CAMPAIGN_TERMINAL_CLASSIFICATIONS:
        return False
    axes = row.get("axes") if isinstance(row.get("axes"), dict) else {}
    if axes.get("capture_validity") not in (None, "valid"):
        return False
    if axes.get("resource_provenance") not in (None, "verified"):
        return False
    if axes.get("gpu_execution") not in (None, "ok"):
        return False
    return True


def _campaign_pixel_divergence(row: dict) -> bool:
    """True only for a verified divergent final-pixel comparison.

    The classification alone is not enough: ``unsupported_state`` and some
    ``qualified_difference`` rows can be caused by intermediate or semantic
    mismatches while the final image matches.  Pixel divergence requires the
    authoritative ``pixel_comparison`` qualification axis to be ``divergent``
    and at least one mismatched final pixel, on a trustworthy frame.
    """
    if not _campaign_row_trusted(row):
        return False
    axes = row.get("axes") if isinstance(row.get("axes"), dict) else {}
    statistics = row.get("pixels") if isinstance(row.get("pixels"), dict) else {}
    try:
        mismatched = int(statistics.get("mismatched_pixels") or 0)
    except (TypeError, ValueError):
        mismatched = 0
    return (axes.get("pixel_comparison") == _CAMPAIGN_PIXEL_AXIS
            and mismatched > 0)


def _campaign_checkpoint_divergence(row: dict) -> bool:
    """True only for a confirmed authoritative intermediate checkpoint diff."""
    if not _campaign_row_trusted(row):
        return False
    validation = row.get("checkpoint_validation")
    if not isinstance(validation, dict) or validation.get("status") != "ok":
        return False
    try:
        return int(validation.get("divergent") or 0) > 0
    except (TypeError, ValueError):
        return False


def _campaign_outcome(report: dict) -> tuple[str, str, str | None, str | None,
                                              bool, dict]:
    """Apply terminal-first campaign precedence.

    Returns ``(status, diagnostic, verdict, evidence, qualified, details)``.
    Rendering differences remain visible in ``details`` even when a later
    terminal failure supplies the overall verdict.

    ``first_difference`` is retained for v1 consumers as the earliest frame
    whose classification is any non-equivalence (including ``unsupported_state``
    and semantic/intermediate mismatches).  It is *not* necessarily a pixel
    difference.  The aggregate verdict, however, is derived from confirmed
    evidence across all trusted, diagnosed frames: any verified final-pixel or
    authoritative checkpoint difference makes the verdict ``different`` even
    when an earlier frame only observed unsupported state.  Terminal failures
    still outrank every rendering verdict.  Consumers that need the individual
    evidence should use:

    * ``first_pixel_divergent_frame`` -- first frame whose final pixel axis is
      ``divergent`` with mismatched-pixel statistics.
    * ``first_checkpoint_divergent_frame`` -- first frame with an authoritative
      intermediate checkpoint difference.
    * ``first_unqualified_frame`` -- first frame not classified
      ``qualified_match``.
    * ``terminal_failure`` -- first terminal execution/history/provenance
      failure, which outranks every pixel result.
    """
    rows = [row for row in report.get("frames") or []
            if isinstance(row, dict)]
    differences = [row for row in rows
                   if row.get("classification")
                   in _CAMPAIGN_PIXEL_CLASSIFICATIONS]
    first_difference = None
    if differences:
        row = differences[0]
        first_difference = {
            "frame": row.get("sequence"),
            "sequence": row.get("sequence"),
            "classification": row.get("classification"),
            "event": (row.get("first_divergence") or {}).get("event"),
            "operation": (row.get("first_divergence") or {}).get("operation"),
            "pixels": row.get("pixels"),
            "pixel_divergent": _campaign_pixel_divergence(row),
            "checkpoint_divergent": _campaign_checkpoint_divergence(row),
            "meaning": (
                "earliest classified non-equivalence; not necessarily a "
                "divergent final image"),
        }
    first_pixel = next(
        (row.get("sequence") for row in rows
         if _campaign_pixel_divergence(row)), None)
    first_checkpoint = next(
        (row.get("sequence") for row in rows
         if _campaign_checkpoint_divergence(row)), None)
    first_unqualified = next(
        (row.get("sequence") for row in rows
         if row.get("classification") != "qualified_match"), None)
    terminal = _campaign_terminal_failure(report)
    identities = {
        int(row.get("sequence")): {
            "present_index": row.get("present_index"),
            "scene_index": row.get("scene_index"),
        }
        for row in rows
        if isinstance(row.get("sequence"), int)
    }

    def identity_for(sequence):
        return identities.get(sequence) if sequence is not None else None

    details = {
        "campaign_status": report.get("status"),
        "campaign_execution": "ok",
        "stop_reason": report.get("stop_reason"),
        "stopped_at": report.get("stopped_at"),
        "first_difference": first_difference,
        "first_pixel_divergent_frame": first_pixel,
        "first_pixel_divergent_frame_identity": identity_for(first_pixel),
        "first_checkpoint_divergent_frame": first_checkpoint,
        "first_checkpoint_divergent_frame_identity": identity_for(
            first_checkpoint),
        "first_unqualified_frame": first_unqualified,
        "first_unqualified_frame_identity": identity_for(first_unqualified),
        "frame_identities": identities,
        "engine_identity": report.get("engine_identity"),
        "terminal_failure": terminal,
        "sequence_order": report.get("sequence_order"),
        "qualified": False,
    }
    if terminal is not None:
        status = ("failed" if terminal["kind"] in (
            "gpu_execution_failure", "host_failure", "frame_timeout",
            "overall_timeout", "tool_failure", "frame_identity",
            "sequence_order")
            else "invalid")
        details["campaign_execution"] = "failed"
        details["qualified"] = False
        return status, ("failed" if status == "failed" else "invalid"), None, \
            "invalid", False, details
    if report.get("status") == "cancelled":
        details["campaign_execution"] = "cancelled"
        return "refused", "refused", None, "unverified", False, details
    if report.get("status") == "incomplete":
        details["campaign_execution"] = "incomplete"
        return "incomplete", "incomplete", None, "unverified", False, details
    trusted = [row for row in rows if _campaign_row_trusted(row)]
    trusted_classifications = {
        row.get("classification") for row in trusted
        if row.get("classification") in (
            "qualified_difference", "difference_with_partial_state_coverage")
    }
    fault_experiment = report.get("fault_injection")
    if fault_experiment:
        evidence = report.get("fault_evidence") or {}
        fired = evidence.get("fired") is True
        details["fault_experiment"] = {
            "request": fault_experiment,
            "fired": fired,
            "evidence": evidence,
            "qualified": False,
            "note": (
                "a selected fault experiment is never a qualified campaign, "
                "even when the fault is pixel-neutral or did not fire"),
        }
        details["qualified"] = False
        if first_pixel is not None or first_checkpoint is not None                 or trusted_classifications:
            diagnostic = ("complete"
                          if "qualified_difference" in trusted_classifications
                          else "partial")
            return "ok", diagnostic, "different", "valid", False, details
        if fired:
            return "ok", "complete", "unverified", "valid", False, details
        return "incomplete", "incomplete", None, "valid", False, details
    # Aggregate from confirmed evidence across every diagnosed frame, not from
    # the earliest classified non-equivalence.  A later verified pixel or
    # checkpoint difference therefore outranks an earlier unsupported state.
    if first_pixel is not None or first_checkpoint is not None \
            or trusted_classifications:
        diagnostic = ("complete"
                      if "qualified_difference" in trusted_classifications
                      else "partial")
        return "ok", diagnostic, "different", "valid", False, details
    if any(row.get("classification") == "unsupported_state"
           for row in trusted):
        # Unsupported state prevents an equivalence claim, but with no
        # verified pixel or checkpoint difference the verdict is unverified.
        return "ok", "partial", "unverified", "valid", False, details
    if (report.get("qualified")
            and first_unqualified is None
            and all(row.get("classification") == "qualified_match"
                    and row.get("diagnostic_completed") is True
                    for row in rows)):
        details["qualified"] = True
        return "ok", "complete", "equal", "valid", True, details
    return "incomplete", "incomplete", None, "unverified", False, details


def _replay_compact_view(report: dict, report_path: str | None = None) -> dict:
    """Compact replay projection for the default agent-facing path.

    Project the canonical diagnosis and retain its complete report locator.
    """
    diagnosis = report.get("diagnosis")
    if not diagnosis:
        # Legacy flat reports describe final pixels without a pixels record.
        diagnosis = report
        if not isinstance(report.get("pixels"), dict) and report.get("final"):
            diagnosis = {**report, "pixels": {"status": report["final"].get("status"),
                                              "final": report["final"]}}
    compact = gfx_independent_replay.compact_diagnosis(diagnosis)
    if report.get("policy") is not None:
        compact["policy"] = report.get("policy")
    if report_path:
        compact["report"] = report_path
    return compact


def _truncate_utf8(value, max_bytes: int) -> str:
    """Truncate text to at most ``max_bytes`` UTF-8 bytes without splitting."""
    text = "" if value is None else str(value)
    if max_bytes <= 0:
        return ""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def _bound_replay_compact(envelope: dict, cap: int = COMPACT_OUTPUT_CAP) -> dict:
    """Project oversized diagnostics onto a fixed set of actionable fields."""
    result = dict(envelope["result"], output_bytes=0)

    normal = _measured_envelope({**envelope, "result": result})
    if _utf8_len(normal) <= cap:
        return normal

    def fields(source, names):
        return {key: value for key in names
                if isinstance(source, dict) and (value := source.get(key)) is not None
                and isinstance(value, (str, int, float, bool))
                and _utf8_len(value) <= 256}

    divergence = _essential_divergence(result.get("first_divergence") or {})
    report = result.get("report")
    core = {**fields(result, ("schema", "classification", "qualified")),
            "first_divergence": divergence, "truncated": True, "output_bytes": 0,
            "output_cap_bytes": cap}
    if isinstance(report, str):
        core["report"] = report
    projected = _measured_envelope({
        **fields(envelope, ("schema", "tool", "ok", "status")),
        "reason": _truncate_utf8(envelope.get("reason"), 200),
        "result": core,
        "outcome": fields(envelope.get("outcome"), ("execution", "diagnostic",
                          "diagnostic_complete", "verdict", "evidence", "qualified")),
        "failure": fields(envelope.get("failure"), ("kind", "class")),
        "truncated": True, "output_cap_bytes": cap,
    })
    if _utf8_len(projected) <= cap:
        return projected
    return _essential_cap_error(envelope, cap, _utf8_len(normal))


def _replay_report_view(report: dict) -> dict:
    """Bounded, JSON-safe view of a v1 or v2 replay diagnosis."""
    diagnosis = report.get("diagnosis") if isinstance(
        report.get("diagnosis"), dict) else {}
    source = diagnosis or report
    return {
        **{key: report.get(key) for key in ("status", "reproduction", "campaign", "replay")},
        **{key: source.get(key, report.get(key)) for key in (
            "classification", "qualification", "policy", "evaluation", "checkpoints",
            "checkpoint_validation", "state_coverage", "provenance",
            "operation_coverage", "binding_audit", "first_divergence", "history")},
        "outcome": report.get("outcome") or source.get("qualification"),
        "final": (source.get("pixels") or report.get("pixels") or {}).get("final")
        or report.get("final"),
    }


def _strategy_b_verify(case_path: Path) -> dict:
    try:
        return gfx_replay_case.verify(case_path)
    except (RuntimeError, OSError, ValueError) as exc:
        return _case_verifier_exception(exc)


def _case_verifier_exception(exc: Exception) -> dict:
    detail = f"{type(exc).__name__}: {exc}"
    return {
        "schema": "gfx-strategy-b-case-verification/v1",
        "case_id": None,
        "ok": False,
        "verifier_exception": True,
        "exception": detail,
        "problems": [f"case verification raised {detail}"],
        "files": 0,
        "graph_status": None,
        "sequence": {},
        "reproduction_status": "invalid",
        "independently_reproducible": False,
    }


def _case_invalid_envelope(tool: str, case_path: Path, kind: str,
                           schema: str | None, detail: str | None) -> dict:
    status = ("not_found" if kind == gfx_replay_case.CASE_KIND_MISSING else "invalid")
    return _envelope(
        tool, status, {
            "case": str(case_path), "kind": kind, "schema": schema,
            "detail": detail,
        }, diagnostic=("unavailable" if status == "not_found"
                       else "invalid"),
        evidence="invalid", qualified=False,
        reason=detail or f"case cannot be used ({kind})")


def _strategy_b_verify_result(case_path: Path, schema: str,
                              manifest: dict, verification: dict,
                              *, compact: bool = True, tool: str = "eu4_replay") -> dict:
    status, diagnostic, evidence = _case_verification_outcome(verification)
    replay_tool = tool == "eu4_replay"
    if compact:
        value = _compact_case_view(case_path, schema, manifest, verification)
    else:
        value = {
            "schema": schema,
            **{key: manifest.get(key) for key in
               ("case_id", "title", "replay_command", "verify_command")},
            "verification": verification if replay_tool else _case_verification_view(verification),
            "diagnosis": _diagnosis_report(case_path, manifest),
        }
        if schema == gfx_replay_case.SEQUENCE_SCHEMA:
            value["sequence"] = _case_sequence_view(case_path)
        source = value.get("sequence") or verification.get("sequence") or {}
        value["reproduction"] = manifest.get("reproduction") or source.get("reproduction") or {}
        if not replay_tool:
            value.update({
                "graph": _optional_json(case_path / "graph.json"),
                "reproduction_scope": manifest.get("reproduction_scope"),
                "terminal_failure": manifest.get("terminal_failure"),
                "files": len(manifest.get("files") or []),
            })
    if replay_tool:
        value["execution"] = "not_run"
        value["next"] = ("pass --replay-out to re-execute this Strategy B case"
                         if verification.get("ok")
                         else "repair the case evidence before replay")
        if compact:
            value["verification_full"] = False
    envelope = _envelope(
        tool, status, value, diagnostic=diagnostic, evidence=evidence, qualified=False,
        reason="; ".join(str(item) for item in verification.get("problems") or []))
    return _bounded_envelope(envelope) if compact else envelope


def _replay_rationale(args: argparse.Namespace):
    """Validate the agent-supplied experiment rationale before any GPU work."""
    from automation import experiment_rationale

    try:
        return experiment_rationale.require(
            getattr(args, "rationale", None), run_root=Path.cwd()), None
    except experiment_rationale.RationaleError as exc:
        return None, _envelope(
            "eu4_replay", "refused", {"kind": "rationale_required"},
            diagnostic="refused", evidence="invalid",
            reason=str(exc))


def _replay_reason(report: dict) -> str:
    diagnosis = report.get("diagnosis") or report
    capture = diagnosis.get("capture") or {}
    identity = diagnosis.get("identity") or {}
    return (capture.get("error") or report.get("receipt_error")
            or "; ".join(identity.get("problems") or [])
            or "; ".join(((report.get("diagnosis") or report).get("evaluation") or {}).get("reasons") or []))


def _compact_qualification_case(row: dict) -> dict:
    summary = {key: row[key] for key in (
        "case_id", "category", "verdict", "requirement_satisfied", "receipt",
        "evidence_tier", "execution", "execution_reused", "preserved_exact",
    ) if key in row}
    summary["coverage_count"] = len(row.get("coverage", []))
    if problems := row.get("problems"):
        summary["problems"] = [_truncate_utf8(problem, 240) for problem in problems[:3]]
        if len(problems) > 3:
            summary["problem_count"] = len(problems)
    for name, fields in (
        ("scope", ("draw", "checkpoint", "later_readback", "resource", "through", "events",
                   "channels", "function", "cases", "memory_window", "return_register",
                   "attachment", "transitions", "pixels")),
        ("reference", ("key", "path", "manifest_sha256", "corpus_sha256")),
        ("earliest_observed_divergence", ("event", "transition", "phase", "field", "target",
                                         "resource", "status", "timing")),
    ):
        if source := row.get(name):
            summary[name] = {field: source[field] for field in fields if field in source}
    if divergence := row.get("earliest_observed_divergence"):
        detail = summary["earliest_observed_divergence"]
        for key in ("expected", "actual"):
            if key in divergence:
                if _utf8_len(divergence[key]) <= 160:
                    detail[key] = divergence[key]
                else:
                    detail[f"{key}_in_receipt"] = True
        if stats := divergence.get("statistics"):
            summary["earliest_observed_divergence"].update(
                {key: stats[key] for key in ("mismatched_pixels", "max_channel_error") if key in stats})
    return summary


def _replay_diagnosis_envelope(report: dict, destination: Path, *, full: bool) -> dict:
    status, diagnostic, verdict, evidence, qualified = _diagnosis_outcome(report)
    if full:
        result = {**_replay_report_view(report), "report": str(destination)}
    else:
        result = _replay_compact_view(report, str(destination))
    value = _envelope("eu4_replay", status, result, reason=_replay_reason(report),
                      diagnostic=diagnostic, verdict=verdict, evidence=evidence, qualified=qualified)
    return value if full else _bound_replay_compact(value)


def eu4_replay(args: argparse.Namespace) -> dict:
    if args.replay_op == "qualify":
        from automation import gfx_revision

        if args.workspace is None or args.commit is None:
            return _envelope("eu4_replay", "refused", reason="qualification requires --workspace and --commit")
        try:
            report = gfx_revision.qualify(
                args.workspace, args.commit, str(args.case) if args.case is not None else None)
        except (gfx_revision.QualificationError, OSError, RuntimeError, ValueError,
                subprocess.SubprocessError) as exc:
            return _envelope("eu4_replay", "refused", reason=str(exc), qualified=False)
        if not args.full:
            report["cases"] = [_compact_qualification_case(row) for row in report["cases"]]
        complete = report["qualified"] or (
            report["mode"] == "focused"
            and report["cases"][0]["verdict"] in ("exact", "different"))
        envelope = _envelope(
            "eu4_replay", "ok" if complete else "incomplete", report,
            qualified=report["qualified"],
            diagnostic="complete" if complete else "incomplete")
        if args.full:
            return envelope
        measured = _measured_envelope(envelope)
        size = _utf8_len(measured)
        return (measured if size <= COMPACT_OUTPUT_CAP
                else _essential_cap_error(measured, COMPACT_OUTPUT_CAP, size))
    if args.replay_op == "independent":
        if not args.frame or not args.replay_out:
            return _envelope("eu4_replay", "error",
                             reason="independent replay requires --frame and --replay-out")
        rationale, refusal = _replay_rationale(args)
        if refusal is not None:
            return refusal
        faults: dict = {}
        if getattr(args, "disable_render_target_vflip", False):
            faults["disable_render_target_vflip"] = True
        if getattr(args, "sampler_shift", None) is not None:
            faults["sampler_shift"] = args.sampler_shift
            faults["sampler_shift_min"] = args.sampler_shift_min
        report = gfx_independent_replay.replay(
            Path(args.frame), Path(args.replay_out), shaders=args.shaders,
            faults=faults, policy=getattr(args, "policy", None),
            rationale=rationale)
        return _replay_diagnosis_envelope(report, Path(args.replay_out) / "replay-report.json",
                                          full=bool(getattr(args, "full", False)))
    if args.replay_op == "verify":
        if not args.case:
            return _envelope("eu4_replay", "error",
                             reason="case verification requires --case")
        case_path = Path(args.case)
        if not case_path.is_dir():
            return _envelope("eu4_replay", "not_found", {
                "case": str(case_path), "exists": False,
            }, diagnostic="unavailable",
               reason=f"case directory does not exist: {case_path}")
        kind, schema, manifest, detail = gfx_replay_case.case_schema(case_path)
        if kind != gfx_replay_case.CASE_KIND_STRATEGY_B:
            if kind == gfx_replay_case.CASE_KIND_LEGACY:
                return _envelope(
                    "eu4_replay", "refused", {
                        "case": str(case_path), "kind": kind,
                    }, diagnostic="refused", evidence="invalid",
                    reason="legacy image-comparison case; use "
                           "`eu4_replay case`, not `verify`")
            return _case_invalid_envelope(
                "eu4_replay", case_path, kind, schema, detail)
        verification = _strategy_b_verify(case_path)
        return _strategy_b_verify_result(
            case_path, schema, manifest, verification,
            compact=not bool(getattr(args, "full", False)))
    if args.replay_op == "diagnose":
        if not args.frame or not args.metal or not args.replay_out:
            return _envelope(
                "eu4_replay", "error",
                reason="M7 diagnosis requires --frame, --metal and --replay-out/--out")
        try:
            report = gfx_independent_replay.diagnose_frame(
                Path(args.frame), Path(args.metal),
                Path(args.replay_out),
                getattr(args, "policy", None),
                require_seal=bool(getattr(args, "require_seal", False)),
                shaders=getattr(args, "shaders", None))
        except (RuntimeError, OSError, ValueError) as exc:
            return _envelope(
                "eu4_replay", "failed", {"frame": str(args.frame)},
                diagnostic="failed", evidence="invalid",
                reason=f"M7 diagnosis did not complete: {exc}")
        return _replay_diagnosis_envelope(report, Path(args.replay_out) / "diagnostic-report.json",
                                          full=bool(getattr(args, "full", False)))
    if args.replay_op == "sequence":
        from automation import m7_continuous

        frame_dirs = [Path(item) for item in
                      (getattr(args, "frame_dir", None) or [])]
        if not frame_dirs:
            return _envelope("eu4_replay", "error",
                             reason="sequence replay requires at least one --frame-dir")
        if not args.replay_out:
            return _envelope("eu4_replay", "error",
                             reason="sequence replay requires --replay-out/--out")
        rationale, refusal = _replay_rationale(args)
        if refusal is not None:
            return refusal
        shaders = getattr(args, "shaders", None)
        if shaders is None:
            try:
                shaders = gfx_independent_replay._shader_dir(frame_dirs[0], None)
            except (RuntimeError, OSError) as exc:
                return _envelope(
                    "eu4_replay", "refused", {"frames": [str(item) for item in frame_dirs]},
                    diagnostic="refused", evidence="invalid",
                    reason=f"no translated shader directory: {exc}")
        max_frames = getattr(args, "max_frames", None) or len(frame_dirs)
        if max_frames > 64:
            return _envelope("eu4_replay", "refused",
                             reason="--max-frames is bounded to 64")
        fault = None
        fault_frame = getattr(args, "fault_frame", None)
        if fault_frame is not None:
            if fault_frame < 1 or fault_frame > max_frames:
                return _envelope(
                    "eu4_replay", "refused",
                    reason="--fault-frame must select an executed frame")
            fault = {"frame": int(fault_frame), "kind": "sampler_shift",
                     "sampler_shift": int(getattr(args, "sampler_shift", 1)
                                          or 1),
                     "sampler_shift_min": int(
                         getattr(args, "sampler_shift_min", 7))}
            if getattr(args, "fault_event", None) is not None:
                fault["event"] = int(args.fault_event)
        from automation import campaign_guard

        try:
            # Replays are not budgeted; a safety stop still ends the campaign.
            campaign_guard.require_active_from_env()
            report = m7_continuous.run_campaign(
                frame_dirs, Path(args.replay_out), shaders=shaders,
                policy=getattr(args, "policy", None),
                max_frames=max_frames,
                max_bytes=int(getattr(args, "max_bytes", 0) or 0),
                max_seconds=float(getattr(args, "max_seconds", 0.0)
                                  or 0.0),
                stop_on_difference=not bool(
                    getattr(args, "continue_on_difference", False)),
                package_dir=getattr(args, "package_dir", None),
                timeout=float(getattr(args, "timeout", 3600.0) or 3600.0),
                frame_timeout=float(getattr(args, "frame_timeout", 180.0)
                                    or 180.0),
                case_max_bytes=int(getattr(args, "case_max_bytes", 0)
                                   or 0),
                require_engine_identity=bool(
                    getattr(args, "require_engine_identity", False)),
                fault=fault,
            )
        except campaign_guard.CampaignRefusal as exc:
            return _envelope(
                "eu4_replay", "refused",
                {"frames": [str(item) for item in frame_dirs],
                 "kind": exc.kind},
                diagnostic="refused", evidence="invalid",
                reason=f"campaign refused: {exc.kind}: {exc.detail}")
        except m7_continuous.ContinuousError as exc:
            policy_failure = exc.kind in ("policy_unavailable", "policy_invalid")
            return _envelope(
                "eu4_replay", "refused" if policy_failure else "failed",
                {"frames": [str(item) for item in frame_dirs],
                 "kind": exc.kind},
                diagnostic="refused" if policy_failure else "failed",
                evidence="invalid",
                reason=f"M7 sequence execution did not complete: {exc}")
        except (RuntimeError, OSError, ValueError) as exc:
            return _envelope(
                "eu4_replay", "failed",
                {"frames": [str(item) for item in frame_dirs]},
                diagnostic="failed", evidence="invalid",
                reason=f"M7 sequence execution did not complete: {exc}")
        rows = report.get("frames") or []
        last = rows[-1] if rows else {}
        status, diagnostic, verdict, evidence, qualified, details = (
            _campaign_outcome(report))
        value = {
            "status": report.get("status"),
            "campaign_execution": details["campaign_execution"],
            "stop_reason": report.get("stop_reason"),
            "stopped_at": report.get("stopped_at"),
            "qualified": bool(details["qualified"]),
            "first_difference": details["first_difference"],
            "first_pixel_divergent_frame":
                details["first_pixel_divergent_frame"],
            "first_pixel_divergent_frame_identity":
                details["first_pixel_divergent_frame_identity"],
            "first_checkpoint_divergent_frame":
                details["first_checkpoint_divergent_frame"],
            "first_checkpoint_divergent_frame_identity":
                details["first_checkpoint_divergent_frame_identity"],
            "first_unqualified_frame": details["first_unqualified_frame"],
            "first_unqualified_frame_identity":
                details["first_unqualified_frame_identity"],
            "frame_identities": details["frame_identities"],
            "engine_identity": details["engine_identity"],
            "pixel_verdict": (details["first_difference"] or {}).get(
                "classification"),
            "terminal_failure": details["terminal_failure"],
            "sequence_order": details.get("sequence_order"),
            # Diagnostic completion is evidence-based: every processed frame
            # must have actually executed the diagnosis and produced its
            # required evidence.  A populated diagnostic_report path is only a
            # locator and is never treated as proof of completion.
            "diagnostic_complete": bool(
                rows and all(row.get("diagnostic_completed") is True
                             for row in rows)),
            "validation": report.get("validation"),
            "engine_identity": report.get("engine_identity"),
            "fault_injection": report.get("fault_injection"),
            "fault_evidence": report.get("fault_evidence"),
            "fault_experiment": details.get("fault_experiment"),
            "policy": report.get("policy"),
            "frames": rows,
            "unexecuted_frames": report.get("unexecuted_frames") or [],
            "queue": report.get("queue"),
            "metrics": report.get("metrics"),
            "campaign_report": str(Path(args.replay_out) / "campaign-report.json"),
            "case": (last.get("case") if isinstance(last, dict) else None),
            "reproduction": (last.get("reproduction")
                             if isinstance(last, dict) else None),
        }
        return _envelope(
            "eu4_replay", status, value, diagnostic=diagnostic,
            verdict=verdict, evidence=evidence, qualified=qualified,
            reason=str((details["terminal_failure"] or {}).get("detail")
                       or report.get("error") or report.get("stop_reason") or ""))
    if args.replay_op == "case":
        case_path = Path(args.case)
        if not case_path.is_dir():
            return _envelope("eu4_replay", "not_found", {
                "case": str(case_path), "exists": False,
            }, diagnostic="unavailable",
               reason=f"case directory does not exist: {case_path}")
        kind, schema, manifest, detail = gfx_replay_case.case_schema(case_path)
        if kind == gfx_replay_case.CASE_KIND_STRATEGY_B:
            if not args.replay_out:
                return _strategy_b_verify_result(
                    case_path, schema, manifest, _strategy_b_verify(case_path))
            rationale, refusal = _replay_rationale(args)
            if refusal is not None:
                return refusal
            try:
                report = gfx_replay_case.replay_case(
                    case_path, Path(args.replay_out), rationale=rationale)
            except (RuntimeError, OSError, ValueError) as exc:
                verification = getattr(exc, "verification", None)
                if verification is None:
                    verification = _case_verifier_exception(exc)
                if not verification["ok"]:
                    return _strategy_b_verify_result(
                        case_path, schema, manifest, verification)
                return _envelope(
                    "eu4_replay", "failed", {
                        "case": _case_verification_view(verification),
                        "schema": schema,
                    }, diagnostic="failed", evidence="invalid",
                    reason=f"Strategy B replay did not complete: {exc}")
            verification = report["case_verification"]
            schema = verification["case_schema"]
            reproduction = report.get("reproduction") or {}
            status, diagnostic, verdict, evidence, qualified = _diagnosis_outcome(
                report, reproduction_complete=reproduction.get("status") != "incomplete")
            value = _replay_report_view(report)
            value.update({
                "case": _case_verification_view(verification),
                "schema": schema,
                "sequence": (_case_sequence_view(case_path)
                             if schema == gfx_replay_case.SEQUENCE_SCHEMA else {}),
                "case_id": verification.get("case_id"),
            })
            return _envelope(
                "eu4_replay", status, value, diagnostic=diagnostic,
                verdict=verdict, evidence=evidence, qualified=qualified,
                reason="; ".join(((report.get("diagnosis") or report).get("evaluation") or {}).get("reasons") or []))
        if kind != gfx_replay_case.CASE_KIND_LEGACY:
            return _case_invalid_envelope(
                "eu4_replay", case_path, kind, schema, detail)
        # The legacy image-comparison case needs NumPy; Strategy B replay above
        # does not, so the dependency gate is scoped to this path only.
        dependency = _dependency_failure("eu4_replay", case_path)
        if dependency is not None:
            return dependency
        result = dict(gfx_replay_case.compare_legacy_case(case_path, write=False))
        replay_status = str(result.get("status") or "unavailable")
        status, diagnostic, verdict, evidence = _case_replay_outcome(result)
        result["case_id"] = manifest.get("case_id")
        result["replay_status"] = replay_status
        result["reproduced"] = replay_status == "fail"
        result["verified"] = replay_status == "pass"
        return _envelope("eu4_replay", status, result,
                         reason=str(result.get("reason") or ""),
                         diagnostic=diagnostic, verdict=verdict,
                         evidence=evidence, qualified=False)
    if args.replay_op == "frame":
        if not args.frame or not args.replay_out:
            return _envelope(
                "eu4_replay", "error",
                reason="frame replay requires --frame and --replay-out/--out")
        rationale, refusal = _replay_rationale(args)
        if refusal is not None:
            return refusal
        argv = ["automation.gfx_frame", "replay", "--frame", args.frame,
                "--out", str(args.replay_out),
                "--rationale", str(args.rationale)]
        # ``gfx_frame`` resolves ``<frame>/shaders`` when no explicit
        # translation is supplied.  Passing an empty ``--shaders`` value here
        # would crash argv construction before that fallback can run.
        if args.shaders:
            argv += ["--shaders", args.shaders]
        if args.only_event is not None:
            argv += ["--only-event", str(args.only_event)]
            argv += ["--capture-pre", "--capture-post"]
        run = _run_module("automation.gfx_frame", argv[1:], timeout=600)
        value = run["value"]
        report = _subprocess_report(run)
        if not isinstance(value, dict):
            return _envelope(
                "eu4_replay", "failed",
                {"subprocess": report},
                diagnostic="failed",
                reason=(f"frame replay subprocess failed (exit {run['exit_code']}): "
                        f"{run['parse_error'] or run['stderr'] or 'no output'}"))
        result = dict(value)
        replay_status = str(value.get("status") or "unknown")
        result["replay_status"] = replay_status
        result["subprocess"] = report
        if replay_status in ("pass", "fail") and run["exit_code"] in (0, 2):
            return _envelope(
                "eu4_replay", "ok", result, diagnostic="complete",
                verdict="equal" if replay_status == "pass" else "different",
                evidence="valid")
        if replay_status == "invalid" or run["exit_code"] == 1:
            return _envelope(
                "eu4_replay", "failed", result, diagnostic="failed",
                verdict=replay_status or None, evidence="invalid",
                reason=(f"frame replay did not execute (exit {run['exit_code']}): "
                        f"{value.get('reason') or run['stderr'] or replay_status}"))
        return _envelope(
            "eu4_replay", "incomplete", result, diagnostic="incomplete",
            verdict=replay_status or None, evidence="unverified",
            reason=(f"frame replay returned {replay_status} "
                    f"(exit {run['exit_code']}); no complete verdict"))
    raise ValueError(f"unknown replay operation: {args.replay_op}")


def _parse_pixel(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    try:
        x, y = (int(part) for part in str(value).split(","))
    except (TypeError, ValueError) as exc:
        raise ValueError("--pixel requires two integers X,Y") from exc
    if x < 0 or y < 0:
        raise ValueError("--pixel coordinates must be non-negative")
    return x, y


def _event_case_inspect(args: argparse.Namespace, path: Path) -> dict:
    try:
        pixel = _parse_pixel(getattr(args, "pixel", None))
    except ValueError as exc:
        return _envelope("eu4_case_inspect", "invalid",
                         reason=str(exc), diagnostic="invalid")
    kind, schema, _manifest, detail = gfx_replay_case.case_schema(path)
    verification = None
    if kind == gfx_replay_case.CASE_KIND_STRATEGY_B:
        verification = _strategy_b_verify(path)
    elif kind != gfx_replay_case.CASE_KIND_MISSING and not (
            path / "frame-trace.json").is_file():
        return _case_invalid_envelope(
            "eu4_case_inspect", path, kind, schema, detail)
    try:
        value = gfx_event_evidence.inspect_event(
            path, args.event,
            frame_index=getattr(args, "inspect_frame", None),
            metal=getattr(args, "metal", None),
            shaders=getattr(args, "shaders", None),
            pixel=pixel, full=bool(getattr(args, "full", False)),
            verification=verification)
    except gfx_event_evidence.EventEvidenceError as exc:
        status = ("not_found" if exc.kind in (
            "not_found", "event_not_found", "frame_not_found") else "invalid")
        return _envelope(
            "eu4_case_inspect", status,
            {"case": str(path), "event": args.event},
            reason=str(exc),
            diagnostic="unavailable" if status == "not_found" else "invalid",
            evidence=None if status == "not_found" else "invalid")
    status, diagnostic, evidence = "ok", "complete", "valid"
    if verification is not None:
        verification_status, verification_diagnostic, evidence = (
            _case_verification_outcome(verification))
        if verification_status != "ok":
            status = verification_status
            diagnostic = verification_diagnostic
    capture = value.get("capture") or {}
    if evidence == "valid" and not (
            capture.get("sealed") is True
            and capture.get("authority") == "authentic"):
        evidence = "unverified"
    divergence = value.get("divergence") or {}
    verdict = {
        "match": "equal", "exact": "equal",
        "divergent": "different", "divergent_diagnostic": "different",
    }.get(str(divergence.get("status") or ""))
    envelope = _envelope(
        "eu4_case_inspect", status, value,
        reason="; ".join(str(item) for item in
                         (verification or {}).get("problems") or []),
        diagnostic=diagnostic, verdict=verdict, evidence=evidence,
        qualified=False)
    if not getattr(args, "full", False) and _utf8_len(envelope) > 8192:
        return _envelope(
            "eu4_case_inspect", "failed", {
                "error": "event_output_cap_exceeded",
                "output_cap_bytes": 8192,
                "artifacts": value.get("artifacts") or {},
                "detail": "the selected event exceeds the compact budget; "
                          "use --full or read the referenced artifacts",
            }, reason="the selected event exceeds the compact budget",
            diagnostic="failed", evidence="invalid", qualified=False)
    return envelope


def eu4_case_inspect(args: argparse.Namespace) -> dict:
    path = Path(args.case)
    if args.event is None and any(
            value is not None for value in
            (getattr(args, "pixel", None), getattr(args, "inspect_frame", None),
             getattr(args, "metal", None), getattr(args, "shaders", None))):
        return _envelope(
            "eu4_case_inspect", "invalid",
            reason="--pixel, --frame, --metal and --shaders require --event",
            diagnostic="invalid")
    if args.event is not None or getattr(args, "pixel", None) is not None:
        if not path.is_dir():
            return _envelope("eu4_case_inspect", "not_found", {
                "case": str(path), "exists": False,
            }, diagnostic="unavailable",
               reason=f"case directory does not exist: {path}")
        return _event_case_inspect(args, path)
    if not path.is_dir():
        return _envelope("eu4_case_inspect", "not_found", {
            "case": str(path), "exists": False,
        }, diagnostic="unavailable",
           reason=f"case directory does not exist: {path}")
    kind, schema, manifest, detail = gfx_replay_case.case_schema(path)
    if kind == gfx_replay_case.CASE_KIND_STRATEGY_B:
        return _strategy_b_verify_result(
            path, schema, manifest, _strategy_b_verify(path),
            compact=not bool(getattr(args, "full", False)), tool="eu4_case_inspect")
    if kind != gfx_replay_case.CASE_KIND_LEGACY:
        return _case_invalid_envelope(
            "eu4_case_inspect", path, kind, schema, detail)
    value = gfx_replay_case.inspect_legacy_case(path)
    return _envelope("eu4_case_inspect", "ok", value, diagnostic="complete",
                     verdict=_verdict_from_status(value.get("comparison_status")),
                     evidence="unverified", qualified=False)


# ---------------------------------------------------------------------------
# CLI


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", "--envelope-out", dest="out", type=Path,
                        default=None,
                        help="also write the JSON envelope to this file; "
                             "after a subcommand --out belongs to that tool")
    sub = parser.add_subparsers(dest="tool", required=True)

    re_query = sub.add_parser("eu4_re_query")
    re_query.add_argument("re_op", choices=(
        "resolve", "function", "callers", "callees", "decompile", "xrefs",
        "abi", "struct", "provenance"))
    re_query.add_argument("target", nargs="?", default=None)
    re_query.add_argument("--index", type=Path, default=None)
    re_query.add_argument("--limit", type=int, default=10)
    re_query.add_argument("--max-bytes", type=int, default=24000)

    research = sub.add_parser("eu4_research")
    research.add_argument("research_op", choices=(
        "status", "resolve", "function", "xrefs"))
    research.add_argument("target", nargs="?", default=None)
    research.add_argument("--view", choices=("static", "decompiled", "all"),
                          default="all")
    research.add_argument("--baseline-revision", default=None)
    research.add_argument("--max-bytes", type=int, default=24000)
    research.add_argument("--limit", type=int, default=None)
    research.add_argument("--static-offset", type=int, default=0)
    research.add_argument("--decompile-offset", type=int, default=0)
    research.add_argument("--incoming-offset", type=int, default=0)
    research.add_argument("--outgoing-offset", type=int, default=0)
    research.add_argument("--callsite-offset", type=int, default=0)

    session = sub.add_parser("eu4_session")
    session.add_argument("session_op", choices=(
        "list", "inspect", "terminate", "lease", "discover"))
    session.add_argument("--executable", default=None)
    session.add_argument("--run", type=Path, default=None)
    session.add_argument("--grace", type=float, default=6.0)
    session.add_argument("--force", action="store_true")
    session.add_argument("--include-history", "--all", dest="include_history",
                         action="store_true",
                         help="include closed and stale session records")
    session.add_argument("--status", choices=(
        "active", "closed", "stale", "all"), default=None)
    session.add_argument("--limit", type=int, default=20)
    session.add_argument("--offset", type=int, default=0)
    session.add_argument("--locator-limit", type=int, default=None,
                         help="locator page size (defaults to --limit)")
    session.add_argument("--locator-offset", type=int, default=0,
                         help="locator page offset")
    session.add_argument("--evidence-root", default=None,
                         help="pathsep-separated evidence roots (default EU4_EVIDENCE_ROOT)")
    session.add_argument("--query", default=None)
    session.add_argument("--classification", default=None)
    session.add_argument("--since", default=None,
                         help="ISO-8601 lower bound on the recorded finish time")

    capture = sub.add_parser("eu4_capture")
    capture.add_argument("capture_op", choices=(
        "health", "frames", "request", "probe", "graph", "gl-control",
        "gl-control-qualify"))
    capture.add_argument("--live-image", type=Path, default=None)
    capture.add_argument("--control-image", type=Path, default=None)
    capture.add_argument("--renderer", type=Path, default=None,
                         help="original-API control renderer executed by the "
                              "qualifier; must write result.bgra and receipt.json")
    capture.add_argument("--control-out", type=Path, default=None,
                         help="empty directory for the renderer receipt/artifact")
    capture.add_argument("--checkpoint-event", type=int, default=None)
    capture.add_argument("--draw-event", type=int, default=None)
    capture.add_argument("--resource", default="fb:0")
    capture.add_argument("--drawable", type=Path, default=None)
    capture.add_argument("--prestate", type=Path, default=None)
    capture.add_argument("--comparison-only", action="store_true")
    capture.add_argument("--width", type=int, default=None)
    capture.add_argument("--height", type=int, default=None)
    capture.add_argument("--frame-dir", type=Path, default=None)
    capture.add_argument("--run", type=Path, default=None)
    capture.add_argument("--role", choices=("reference", "candidate"), default=None)
    capture.add_argument("--frame", action="append", type=int, default=[])
    capture.add_argument("--around", type=int, default=0,
                         help="include at most this many neighbouring frames")
    capture.add_argument("--offset", type=int, default=0)
    capture.add_argument("--full", action="store_true",
                         help="return raw observation records instead of the compact view")
    capture.add_argument("--limit", type=int, default=20)
    capture.add_argument("--require-pixels", action="store_true")
    capture.add_argument("--checkpoint", default="after-present")
    capture.add_argument("--occurrence", type=int, default=1)
    capture.add_argument("--output", default=None)
    capture.add_argument("--campaign-run-root", default=None)
    capture.add_argument("--caller-rva", default=None)
    capture.add_argument("--shader-cache", default=None)
    capture.add_argument("--timeout", type=float, default=None)

    diff_run = sub.add_parser("eu4_diff_run")
    diff_run.add_argument("--scenario", required=True)
    diff_run.add_argument("--run-root", required=True)
    diff_run.add_argument("--ledger", required=True)
    diff_run.add_argument("--shader-cache", default=None)
    diff_run.add_argument("--qualified-sources", default=None)
    diff_run.add_argument("--mode", choices=("streaming", "lockstep"), default=None)
    diff_run.add_argument("--max-frames", type=int, default=None)
    diff_run.add_argument("--profile", choices=("early", "targeted", "qualification"),
                          default=None)
    diff_run.add_argument("--staggered", action="store_true")
    diff_run.add_argument("--stagger-wait", type=float, default=600.0)
    diff_run.add_argument("--background", action="store_true")
    diff_run.add_argument("--display-name", default="EU4 Test")

    inspect = sub.add_parser("eu4_diff_inspect")
    inspect.add_argument("--run", required=True, type=Path)
    inspect.add_argument("--build-case", action="store_true")
    inspect.add_argument("--limit", type=int, default=20)
    inspect.add_argument("--offset", type=int, default=0)
    inspect.add_argument("--temporal", action="store_true",
                         help="compare preserved consecutive frames and the cross-process pair")
    inspect.add_argument("--reference-frame", type=int, default=None)
    inspect.add_argument("--candidate-frame", type=int, default=None)
    inspect.add_argument("--temporal-window", type=int, default=1)
    inspect.add_argument("--temporal-artifacts", action="store_true",
                         help="write bounded difference PNGs (off by default)")
    inspect.add_argument("--temporal-artifact-dir", type=Path, default=None)
    inspect.add_argument("--full", action="store_true",
                         help="include the raw recorded comparison and first pair")

    replay = sub.add_parser("eu4_replay")
    replay.add_argument(
        "replay_op",
        choices=("case", "frame", "independent", "sequence", "diagnose",
                 "verify", "qualify"),
        help="case/frame/independent replay, diagnosis, verification or qualification")
    replay.add_argument("--case", type=Path, default=None)
    replay.add_argument("--workspace", type=Path, default=None)
    replay.add_argument("--commit", default=None)
    replay.add_argument("--frame", default=None)
    replay.add_argument("--frame-dir", action="append", default=[],
                        help="one ordered M7 frame directory (repeatable)")
    replay.add_argument("--metal", default=None,
                        help="already-produced Metal result directory for diagnose")
    replay.add_argument("--shaders", default=None)
    replay.add_argument("--rationale", default=None,
                        help="JSON file or inline JSON justifying an "
                             "expensive replay experiment")
    replay.add_argument("--replay-out", "--out", dest="replay_out",
                        type=Path, default=None)
    replay.add_argument("--policy", default=None,
                        help="comparison/state policy for M7 diagnosis")
    replay.add_argument("--require-seal", action="store_true",
                        help="require an authoritative frame seal for M7 diagnosis")
    replay.add_argument("--max-frames", type=int, default=None,
                        help="bounded M7 sequence length")
    replay.add_argument("--max-bytes", type=int, default=0,
                        help="bounded captured-sequence input budget (0 = unbounded)")
    replay.add_argument("--case-max-bytes", type=int, default=0,
                        help="bounded reproduction-case budget (0 = default)")
    replay.add_argument("--max-seconds", type=float, default=0.0,
                        help="bounded M7 overall wall-clock budget")
    replay.add_argument("--frame-timeout", type=float, default=180.0,
                        help="bounded M7 per-frame deadline")
    replay.add_argument("--timeout", type=float, default=3600.0,
                        help="bounded M7 overall deadline")
    replay.add_argument("--continue-on-difference", action="store_true",
                        help="keep executing after a nonqualified M7 frame")
    replay.add_argument("--package-dir", type=Path, default=None,
                        help="explicit reproduction case destination")
    replay.add_argument("--only-event", type=int, default=None)
    replay.add_argument("--full", action="store_true",
                        help="return the complete replay report instead of the "
                             "bounded agent projection")
    replay.add_argument("--disable-render-target-vflip", action="store_true")
    replay.add_argument("--sampler-shift", type=int, default=None)
    replay.add_argument("--sampler-shift-min", type=int, default=7)
    replay.add_argument(
        "--require-engine-identity", action="store_true",
        help="refuse an M7 sequence without consecutive engine present/scene "
             "frame indices")
    replay.add_argument(
        "--fault-frame", type=int, default=None,
        help="controlled divergence: apply the sampler fault in this 1-based "
             "frame only (diagnostic experiment, never qualified)")
    replay.add_argument(
        "--fault-event", type=int, default=None,
        help="controlled divergence: restrict the fault to this event index")

    case = sub.add_parser("eu4_case_inspect")
    case.add_argument("--case", required=True, type=Path)
    case.add_argument("--event", type=int, default=None,
                      help="inspect one captured event by its stream index")
    case.add_argument("--pixel", default=None, metavar="X,Y",
                      help="also read one pixel from the selected event's "
                           "original and Metal observations")
    case.add_argument("--frame", dest="inspect_frame", type=int, default=None,
                      help="1-based sequence index for a multi-frame case; "
                           "required when more than one frame is recorded")
    case.add_argument("--metal", type=Path, default=None,
                      help="already-produced Metal result directory for a "
                           "sealed frame without a packaged replay")
    case.add_argument("--shaders", default=None,
                      help="translated shader directory for a sealed frame")
    case.add_argument("--full", action="store_true",
                      help="return the complete selected-event view instead "
                           "of the bounded agent projection")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # ``--envelope-out`` is unambiguous in any position and must not be
    # confused with a subcommand's own ``--out`` (the replay output directory).
    envelope_out = None
    cleaned: list[str] = []
    index = 0
    while index < len(raw):
        token = raw[index]
        if token == "--envelope-out":
            if index + 1 >= len(raw):
                print(json.dumps({
                    "schema": SCHEMA, "tool": "agent_tools", "ok": False,
                    "status": "error",
                    "reason": "--envelope-out requires a file path",
                }, indent=2))
                return 3
            envelope_out = Path(raw[index + 1])
            index += 2
            continue
        cleaned.append(token)
        index += 1
    args = _parser().parse_args(cleaned)
    if envelope_out is not None:
        args.out = envelope_out
    implementations = {
        "eu4_re_query": eu4_re_query,
        "eu4_research": eu4_research,
        "eu4_session": eu4_session,
        "eu4_capture": eu4_capture,
        "eu4_diff_run": eu4_diff_run,
        "eu4_diff_inspect": eu4_diff_inspect,
        "eu4_replay": eu4_replay,
        "eu4_case_inspect": eu4_case_inspect,
    }
    try:
        value = implementations[args.tool](args)
        code = 0 if value.get("ok") else 2
    except Exception as exc:  # noqa: BLE001 - one JSON envelope, always
        value = _envelope(args.tool, "error", reason=f"{type(exc).__name__}: {exc}")
        code = 3
    text = _emit_text(value)
    if args.out is not None:
        envelope_path = Path(args.out)
        if envelope_path.exists() and envelope_path.is_dir():
            code = 3
            value = _envelope(
                args.tool, "error",
                reason=(f"envelope destination is a directory, not a file: "
                        f"{envelope_path}"))
            text = _emit_text(value)
        else:
            envelope_path.parent.mkdir(parents=True, exist_ok=True)
            envelope_path.write_text(text, encoding="utf-8")
    print(text, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
