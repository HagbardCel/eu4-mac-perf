"""Stable JSON tool core for agents (CLI, DSH plugin, MCP, OpenCode).

Seven logical tools share one implementation:

``eu4_re_query``       reverse-engineering knowledge
``eu4_session``        owned session inspection and safe termination
``eu4_capture``        frame/health inspection and targeted capture requests
``eu4_diff_run``       run a dual-process differential campaign
``eu4_diff_inspect``   inspect the first discrepancy of a campaign
``eu4_replay``         run a stored local reproduction
``eu4_case_inspect``   summarize a diagnostic case package

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
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import (gfx_align, gfx_case, gfx_diagnostics, gfx_dual_run,
                        gfx_frame_graph, gfx_independent_replay, gfx_replay_case,
                        gfx_scenario)  # noqa: E402
from automation import (evidence_index, gfx_frame_compare, gfx_session, launch_lease,
                        launch_ledger)  # noqa: E402

SCHEMA = "eu4-agent-tool/v2"
RUNTIME_SCHEMA = "eu4-tool-runtime/v1"
TOOLS = (
    "eu4_re_query",
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
    """Report the actual interpreter and dependency state of this process.

    This is deliberately computed from ``sys.executable``/the imported module,
    not from configuration: when the adapter and the documented CLI disagree,
    the disagreement must be visible in the envelope that failed.
    """
    configured = os.environ.get("EU4_DSH_PYTHON", "")
    mismatch = False
    if configured:
        try:
            mismatch = Path(configured).expanduser().resolve() != Path(sys.executable).resolve()
        except OSError:
            mismatch = True
    available = gfx_frame_compare.np is not None
    return {
        "schema": RUNTIME_SCHEMA,
        "executable": str(Path(sys.executable).resolve()),
        "version": list(sys.version_info[:3]),
        "configured": configured,
        "mismatch": mismatch,
        "numpy": {
            "required": True,
            "available": available,
            "version": getattr(gfx_frame_compare.np, "__version__", None),
        },
    }


def _envelope(tool: str, status: str, result=None, *, reason: str = "",
              exit_code: int | None = None, diagnostic: str | None = None,
              verdict: str | None = None, evidence: str | None = None,
              qualified: bool | None = None) -> dict:
    state = diagnostic or _DIAGNOSTIC_BY_STATUS.get(status, "incomplete")
    execution = "error" if status == "error" else "ok"
    complete = state == "complete"
    ok = execution == "ok" and state in _COMPLETE_DIAGNOSTIC
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
        },
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
    completed = subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=timeout,
    )
    text = completed.stdout.strip()
    if not text:
        raise RuntimeError(completed.stderr.strip() or "tool produced no output")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"tool output is not JSON: {exc}") from exc
    if isinstance(value, dict):
        return value
    return {"result": value}


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
    """Staticity from the recorded dumps, then the preserved captured frames.

    Recorded sessions predate the neighbour-preserving stop path, so the
    preserved raw frames on disk are the only complete digest series.  Both
    sources use the same predicate; the provenance is reported.
    """
    required = int(((result.get("scenario") or {}).get("settle") or {}).get(
        "stable_frames", 1) or 1)
    value = gfx_align.staticity_evidence(result.get("dumps"), required)
    value["source"] = "result.dumps"
    if value.get("verified"):
        return value
    captured = {}
    for role in ("reference", "candidate"):
        metas, _missing = _frame_meta_entries(run, role)
        captured[role] = {
            "captured": [
                {"frame_index": index, "meta": meta,
                 "sha256": meta.get("sha256")}
                for index, meta in sorted(metas.items())
            ]
        }
    alternate = gfx_align.staticity_evidence(captured, required)
    alternate["source"] = "preserved_captured_frames"
    if alternate.get("verified"):
        return alternate
    value["captured_frames"] = {
        role: [row["frame_index"] for row in payload["captured"]]
        for role, payload in captured.items()
    }
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


def discover_evidence(*, roots, query=None, classification=None, since=None,
                      limit=20, offset=0) -> dict:
    return evidence_index.discover(
        configured_roots=roots,
        query=query,
        classification=classification,
        since=since,
        limit=limit,
        offset=offset,
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
        return _envelope("eu4_capture", status, graph,
                         reason="; ".join(graph.get("reasons") or []))
    run = Path(args.run).expanduser().resolve()
    if args.capture_op == "health":
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
        value = _run_module(*argv, timeout=(args.timeout or 300) + 120)
        return _envelope("eu4_capture", "ok", value)
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
        metas.setdefault(index, {
            "frame_index": index,
            "raw_file": str(path),
            "sha256": _sha256_file(path),
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


def _load_preserved_frames(run: Path, role: str) -> tuple[list[dict], list[int]]:
    """Decode every preserved full-resolution frame for one role."""
    metas, notes = _frame_meta_entries(run, role)
    frames: list[dict] = []
    missing: list[int] = []
    for index in sorted(metas):
        meta = metas[index]
        path = next((item for item in _frame_candidates(run, role, meta)
                     if item.is_file()), None)
        if path is None:
            missing.append(index)
            continue
        try:
            payload = path.read_bytes()
            width = int(meta.get("width") or 0)
            height = int(meta.get("height") or 0)
            if width <= 0 or height <= 0:
                missing.append(index)
                continue
            image = gfx_frame_compare.decode_frame(
                payload, width, height,
                origin=str(meta.get("origin") or "top_left"),
                pixel_format=str(meta.get("pixel_format") or "BGRA8"),
            )
        except (OSError, ValueError, TypeError, gfx_frame_compare.CompareError):
            missing.append(index)
            continue
        frames.append({
            "frame_index": index,
            "image": image,
            "sha256": meta.get("sha256"),
            "identity": {
                key: meta.get(key) for key in (
                    "backend", "game_day", "update_identity", "caller_signature",
                    "draws", "width", "height", "origin", "pixel_format")
                if meta.get(key) is not None
            },
            "path": str(path),
        })
    return frames, sorted(set(missing))


def _first_preserved_pair(frames: list[dict], window: int) -> list[dict]:
    ordered = {int(row["frame_index"]): row for row in frames}
    for index in sorted(ordered):
        if all(number in ordered for number in range(index, index + window + 1)):
            return [ordered[number] for number in range(index, index + window + 1)]
    return []


def _temporal_report(run: Path, pair: dict | None,
                     args: argparse.Namespace) -> dict:
    scenario = _optional_json(run / "result.json").get("scenario") or {}
    policy = dict(scenario.get("comparison") or {})
    policy.setdefault("policy", "diagnostic")
    regions = policy.get("regions") or {}
    reference, reference_missing = _load_preserved_frames(run, "reference")
    candidate, candidate_missing = _load_preserved_frames(run, "candidate")
    window = max(0, int(getattr(args, "temporal_window", 1) or 0))
    reference_available = {int(row["frame_index"]) for row in reference}
    candidate_available = {int(row["frame_index"]) for row in candidate}
    selected_reference = getattr(args, "reference_frame", None)
    selected_candidate = getattr(args, "candidate_frame", None)
    if selected_reference is None:
        selected_reference = (pair or {}).get("frame_index")
    if selected_candidate is None:
        selected_candidate = (pair or {}).get("candidate_frame_index")
    if selected_reference is None and reference:
        selected_reference = int(reference[0]["frame_index"])
    if selected_candidate is None and candidate:
        selected_candidate = int(candidate[0]["frame_index"])
    reference_frames = [
        row for row in reference
        if type(selected_reference) is int
        and int(selected_reference) <= int(row["frame_index"])
        <= int(selected_reference) + window
    ]
    candidate_frames = [
        row for row in candidate
        if type(selected_candidate) is int
        and int(selected_candidate) <= int(row["frame_index"])
        <= int(selected_candidate) + window
    ]
    if not reference_frames:
        fallback = _first_preserved_pair(reference, window)
        if fallback:
            selected_reference = int(fallback[0]["frame_index"])
            reference_frames = fallback
    if not candidate_frames:
        fallback = _first_preserved_pair(candidate, window)
        if fallback:
            selected_candidate = int(fallback[0]["frame_index"])
            candidate_frames = fallback
    artifacts = None
    if getattr(args, "temporal_artifacts", False):
        directory = getattr(args, "temporal_artifact_dir", None)
        artifacts = {
            "dir": Path(directory) if directory else run / "temporal",
            "amplification": 4,
        }
    if not reference_frames or not candidate_frames:
        return {
            "schema": gfx_frame_compare.TEMPORAL_SCHEMA,
            "status": "incomplete",
            "reason": (
                "no preserved full-resolution frames for the requested window"),
            "preserved": {
                "reference": sorted(reference_available),
                "candidate": sorted(candidate_available),
            },
            "missing": {
                "reference": reference_missing,
                "candidate": candidate_missing,
            },
            "selected": {
                "reference": selected_reference,
                "candidate": selected_candidate,
                "window": window,
            },
            "pairs": [],
            "overlap": None,
        }
    report = gfx_frame_compare.compare_temporal(
        reference_frames, candidate_frames,
        policy=policy, regions=regions,
        cross_pairs=[(int(selected_reference), int(selected_candidate))],
        max_pairs=max(3, 2 * (window + 1) + 1),
        artifacts=artifacts,
    )
    report["selected"] = {
        "reference": selected_reference,
        "candidate": selected_candidate,
        "window": window,
    }
    report["preserved"] = {
        "reference": sorted(reference_available),
        "candidate": sorted(candidate_available),
    }
    report["missing"] = {
        "reference": reference_missing,
        "candidate": candidate_missing,
    }
    report["artifacts_requested"] = bool(getattr(args, "temporal_artifacts", False))
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
    if temporal is not None and temporal.get("status") == "incomplete":
        diagnostic = "incomplete"
        reason = str(temporal.get("reason") or "temporal diagnostics incomplete")
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
    }
    return _envelope(
        "eu4_diff_inspect", "ok" if diagnostic == "complete" else "incomplete",
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
    if replay_status in (gfx_frame_compare.PASS, "pass",
                         gfx_frame_compare.FAIL, "fail",
                         gfx_frame_compare.UNVERIFIED, "unverified"):
        verdict = {
            "pass": "equal", "fail": "different", "unverified": "unverified",
        }.get(replay_status)
        return "ok", "complete", verdict, "valid"
    if "numpy" in reason.lower() or "unavailable" in replay_status:
        return "unavailable", "unavailable", None, None
    return "incomplete", "incomplete", None, "unverified"


def eu4_replay(args: argparse.Namespace) -> dict:
    if args.replay_op == "independent":
        if not args.frame or not args.replay_out:
            return _envelope("eu4_replay", "error",
                             reason="independent replay requires --frame and --replay-out")
        faults: dict = {}
        if getattr(args, "disable_render_target_vflip", False):
            faults["disable_render_target_vflip"] = True
        if getattr(args, "sampler_shift", None) is not None:
            faults["sampler_shift"] = args.sampler_shift
            faults["sampler_shift_min"] = args.sampler_shift_min
        report = gfx_independent_replay.replay(
            Path(args.frame), Path(args.replay_out), shaders=args.shaders, faults=faults)
        outcome = report.get("outcome") or {}
        status = report.get("status") or "invalid"
        envelope = _envelope("eu4_replay", status, {
            "status": report.get("status"),
            "outcome": outcome,
            "qualified": outcome.get("qualified"),
            "evaluation": report.get("evaluation"),
            "checkpoints": report.get("checkpoints"),
            "checkpoint_validation": report.get("checkpoint_validation"),
            "state_coverage": report.get("state_coverage"),
            "binding_audit": report.get("binding_audit"),
            "final": report.get("final"),
            "first_divergence": report.get("first_divergence"),
            "graph": {
                "status": (report.get("graph") or {}).get("status"),
                "counts": (report.get("graph") or {}).get("counts"),
                "reasons": (report.get("graph") or {}).get("reasons"),
                "boundary": (report.get("graph") or {}).get("boundary"),
            },
            "report": str(Path(args.replay_out) / "replay-report.json"),
        }, reason="; ".join((report.get("evaluation") or {}).get("reasons") or []),
            diagnostic="complete" if status not in ("invalid", "unavailable") else status,
            verdict=(outcome.get("pixels") if isinstance(outcome.get("pixels"), str)
                     else None),
            evidence=("valid" if report.get("checkpoint_validation", {}).get("status")
                      == "valid" and outcome.get("execution") == "ok" else "unverified"),
            qualified=bool(outcome.get("qualified")))
        envelope["ok"] = bool(outcome.get("qualified"))
        return envelope
    if args.replay_op == "case":
        case_path = Path(args.case)
        if not case_path.is_dir():
            return _envelope("eu4_replay", "not_found", {
                "case": str(case_path), "exists": False,
            }, diagnostic="unavailable",
               reason=f"case directory does not exist: {case_path}")
        strategy_b_case = False
        if (case_path / "case.json").is_file():
            try:
                strategy_b_case = (
                    json.loads((case_path / "case.json").read_text()).get("schema")
                    == gfx_replay_case.SCHEMA
                )
            except (OSError, UnicodeError, json.JSONDecodeError):
                strategy_b_case = False
        if strategy_b_case:
            verification = gfx_replay_case.verify(case_path)
            if args.replay_out:
                report = gfx_replay_case.replay_case(
                    case_path, Path(args.replay_out))
                outcome = report.get("outcome") or {}
                status = report.get("status", "invalid")
                envelope = _envelope("eu4_replay", status, {
                    "case": verification,
                    "status": report.get("status"),
                    "outcome": outcome,
                    "qualified": outcome.get("qualified"),
                    "evaluation": report.get("evaluation"),
                    "checkpoints": report.get("checkpoints"),
                    "checkpoint_validation": report.get("checkpoint_validation"),
                    "state_coverage": report.get("state_coverage"),
                    "final": report.get("final"),
                    "first_divergence": report.get("first_divergence"),
                }, diagnostic=("complete" if status not in ("invalid", "unavailable")
                               else status),
                   verdict=(outcome.get("pixels")
                            if isinstance(outcome.get("pixels"), str) else None),
                   evidence=("valid" if outcome.get("execution") == "ok"
                             else "unverified"),
                   qualified=bool(outcome.get("qualified")))
                envelope["ok"] = bool(outcome.get("qualified"))
                envelope["diagnostic_ok"] = outcome.get("execution") == "ok"
                return envelope
            graph = _read_json(case_path / "graph.json") if (
                case_path / "graph.json").is_file() else {}
            return _envelope("eu4_replay", "ok", {
                "case": verification,
                "execution": "not_run",
                "coverage": graph.get("status"),
                "qualified": False,
                "next": "pass --replay-out to re-execute this Strategy B case",
            }, diagnostic="not_applicable",
               evidence=("valid" if verification.get("ok") else "invalid"),
               qualified=False)
        # The legacy image-comparison case needs NumPy; Strategy B replay above
        # does not, so the dependency gate is scoped to this path only.
        dependency = _dependency_failure("eu4_replay", case_path)
        if dependency is not None:
            return dependency
        result = dict(gfx_case.compare_case(case_path, write=False))
        replay_status = str(result.get("status") or "unavailable")
        status, diagnostic, verdict, evidence = _case_replay_outcome(result)
        result["replay_status"] = replay_status
        result["reproduced"] = replay_status == "fail"
        result["verified"] = replay_status == "pass"
        return _envelope("eu4_replay", status, result,
                         reason=str(result.get("reason") or ""),
                         diagnostic=diagnostic, verdict=verdict,
                         evidence=evidence,
                         qualified=(replay_status == "pass"))
    if args.replay_op == "frame":
        argv = ["automation.m6_frame", "replay", "--frame", args.frame,
                "--shaders", args.shaders, "--out", args.out]
        if args.only_event is not None:
            argv += ["--only-event", str(args.only_event)]
            argv += ["--capture-pre", "--capture-post"]
        value = _run_module(*argv, timeout=600)
        result = dict(value)
        result["replay_status"] = value.get("status", "unknown")
        return _envelope("eu4_replay", "ok", result,
                         diagnostic="complete",
                         verdict=None,
                         evidence=("valid" if value.get("status") in ("pass", "fail")
                                   else "unverified"))
    raise ValueError(f"unknown replay operation: {args.replay_op}")


def eu4_case_inspect(args: argparse.Namespace) -> dict:
    path = Path(args.case)
    strategy_b_case = False
    if (path / "case.json").is_file():
        try:
            strategy_b_case = (
                json.loads((path / "case.json").read_text()).get("schema")
                == gfx_replay_case.SCHEMA
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            strategy_b_case = False
    if strategy_b_case:
        verification = gfx_replay_case.verify(path)
        manifest = _read_json(path / "case.json")
        graph = _read_json(path / "graph.json") if (path / "graph.json").is_file() else {}
        report = _read_json(path / "report.json") if (path / "report.json").is_file() else {}
        return _envelope(
            "eu4_case_inspect", "ok" if verification["ok"] else "partial", {
                "schema": manifest.get("schema"),
                "case_id": manifest.get("case_id"),
                "title": manifest.get("title"),
                "verification": verification,
                "graph": {
                    "status": graph.get("status"),
                    "counts": graph.get("counts"),
                    "reasons": graph.get("reasons"),
                    "unproven": graph.get("unproven"),
                    "external_inputs": graph.get("external_inputs"),
                    "renderer_generated": graph.get("renderer_generated"),
                },
                "replay": {
                    "status": (report.get("status")),
                    "outcome": report.get("outcome"),
                    "qualified": (report.get("outcome") or {}).get("qualified"),
                    "final": report.get("final"),
                    "checkpoints": report.get("checkpoints"),
                    "binding_audit": report.get("binding_audit"),
                    "first_divergence": report.get("first_divergence"),
                },
                "replay_command": manifest.get("replay_command"),
                "files": len(manifest.get("files") or []),
            }, diagnostic="complete",
               evidence=("valid" if verification["ok"] else "invalid"),
               verdict=(report.get("outcome") or {}).get("pixels"),
               qualified=(report.get("outcome") or {}).get("qualified"))
    value = gfx_case.inspect_case(path)
    verdict = {
        "pass": "equal", "fail": "different", "unverified": "unverified",
    }.get(str(value.get("comparison_status") or ""))
    return _envelope("eu4_case_inspect", "ok", value, diagnostic="complete",
                     verdict=verdict,
                     evidence=("valid" if value.get("comparison_status")
                               in ("pass", "fail", "unverified") else "unverified"),
                     qualified=None)


# ---------------------------------------------------------------------------
# CLI


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None,
                        help="also write the JSON envelope to this file")
    sub = parser.add_subparsers(dest="tool", required=True)

    re_query = sub.add_parser("eu4_re_query")
    re_query.add_argument("re_op", choices=(
        "resolve", "function", "callers", "callees", "decompile", "xrefs",
        "abi", "struct", "provenance"))
    re_query.add_argument("target", nargs="?", default=None)
    re_query.add_argument("--index", type=Path, default=None)
    re_query.add_argument("--limit", type=int, default=10)
    re_query.add_argument("--max-bytes", type=int, default=24000)

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
    session.add_argument("--evidence-root", default=None,
                         help="pathsep-separated evidence roots (default EU4_EVIDENCE_ROOT)")
    session.add_argument("--query", default=None)
    session.add_argument("--classification", default=None)
    session.add_argument("--since", default=None,
                         help="ISO-8601 lower bound on the recorded finish time")

    capture = sub.add_parser("eu4_capture")
    capture.add_argument("capture_op", choices=("health", "frames", "request", "probe", "graph"))
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
    replay.add_argument("replay_op", choices=("case", "frame", "independent"))
    replay.add_argument("--case", type=Path, default=None)
    replay.add_argument("--frame", default=None)
    replay.add_argument("--shaders", default=None)
    replay.add_argument("--replay-out", type=Path, default=None)
    replay.add_argument("--only-event", type=int, default=None)
    replay.add_argument("--disable-render-target-vflip", action="store_true")
    replay.add_argument("--sampler-shift", type=int, default=None)
    replay.add_argument("--sampler-shift-min", type=int, default=7)

    case = sub.add_parser("eu4_case_inspect")
    case.add_argument("--case", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if getattr(args, "out", None) is None and argv:
        # ``argparse`` subparsers reset parent defaults in this layout; honour
        # ``--out`` in either position without duplicating it on every parser.
        for index, token in enumerate(argv):
            if token == "--out" and index + 1 < len(argv):
                args.out = Path(argv[index + 1])
                break
    implementations = {
        "eu4_re_query": eu4_re_query,
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
    text = json.dumps(value, indent=2, sort_keys=False) + "\n"
    if args.out is not None:
        args.out = Path(args.out)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
