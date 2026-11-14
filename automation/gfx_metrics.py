"""Efficiency metrics for the differential-debugging workflow (PR 5).

The metrics are descriptive, not a performance score: they count launches,
observations, comparisons, inconclusive cases, artifact bytes and the time to
the first discrepancy.  They must never be improved by weakening a diagnostic
check.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SCHEMA = "gfx-efficiency-metrics/v1"


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _session_results(root: Path) -> list[tuple[Path, dict]]:
    rows = []
    for path in sorted(root.glob("**/result.json")):
        if path.parent.name.startswith("case"):
            continue
        if not (path.parent / "session.json").is_file():
            continue
        value = _read_json(path)
        if value is not None:
            rows.append((path.parent, value))
    return rows


def summarize(root: Path, *, ledger: Path | None = None) -> dict:
    root = Path(root)
    sessions = []
    total_frames = total_paired = total_aligned = total_compared = 0
    total_not_compared = 0
    total_failed = 0
    total_bytes = 0
    launches = None
    if ledger is not None and ledger.is_file():
        value = _read_json(ledger) or {}
        launches = {
            "limit": value.get("limit"),
            "used": value.get("used"),
            "receipts": len(value.get("launches", [])),
            "statuses": {},
        }
        for receipt in value.get("launches", []):
            status = receipt.get("status", "unknown")
            launches["statuses"][status] = launches["statuses"].get(status, 0) + 1
    for run, result in _session_results(root):
        frames = result.get("frames") or {}
        comparison = frames.get("comparison") or {}
        health = result.get("health") or {}
        readiness = result.get("readiness") or {}
        artifacts = result.get("artifacts") or {}
        pair_count = int(frames.get("paired") or 0)
        compared = int(comparison.get("compared") or 0)
        failed = int(comparison.get("failed") or 0)
        not_compared = int(comparison.get("not_compared") or 0)
        total_frames += int(frames.get("observed_reference") or 0)
        total_frames += int(frames.get("observed_candidate") or 0)
        total_paired += pair_count
        total_aligned += int(frames.get("aligned") or 0)
        total_compared += compared
        total_not_compared += not_compared
        total_failed += failed
        total_bytes += int(artifacts.get("bytes") or 0)
        duration = result.get("duration_seconds")
        sessions.append({
            "run": str(run),
            "session_id": result.get("session_id"),
            "result": result.get("result"),
            "mode": (result.get("scenario") or {}).get("mode"),
            "policy": (result.get("scenario") or {}).get("comparison_policy"),
            "startup_seconds": readiness.get("elapsed_seconds"),
            "duration_seconds": duration,
            "frames_paired": pair_count,
            "frames_aligned": int(frames.get("aligned") or 0),
            "frames_compared": compared,
            "frames_not_compared": not_compared,
            "first_discrepancy_seconds": duration if failed else None,
            "health": health.get("verdict"),
            "artifact_bytes": int(artifacts.get("bytes") or 0),
            "dropped_artifacts": int(artifacts.get("dropped") or 0),
        })
    cases = []
    for path in sorted(root.glob("**/case/case.json")):
        value = _read_json(path) or {}
        cases.append({
            "case": str(path.parent),
            "classification": value.get("classification"),
            "bytes": value.get("total_bytes"),
        })
    return {
        "schema": SCHEMA,
        "root": str(root),
        "sessions": len(sessions),
        "launches": launches,
        "frames": {
            "observed": total_frames,
            "paired": total_paired,
            "aligned": total_aligned,
            "compared": total_compared,
            "not_compared": total_not_compared,
            "failed": total_failed,
        },
        "inconclusive_comparisons": total_not_compared,
        "artifact_bytes": total_bytes,
        "cases": cases,
        "defects_with_offline_cases": sum(
            1 for case in cases if case.get("classification") != "match"
        ),
        "per_session": sessions,
        "notes": [
            "launch counts come from the authoritative workflow ledger, not from session directories",
            "an inconclusive comparison is not a pass and is counted separately",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report")
    report.add_argument("--root", required=True, type=Path)
    report.add_argument("--ledger", type=Path, default=None)
    report.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    value = summarize(args.root, ledger=args.ledger)
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
