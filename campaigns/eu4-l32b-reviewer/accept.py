#!/usr/bin/env python3
"""Reviewer campaign accept: pass when the out-of-tree eu4-review/v1 is valid."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / ".git").exists()
)
sys.path.insert(0, str(ROOT))

from automation.review_result import load_review, validate_review  # noqa: E402


def emit(verdict: str, diagnostics: str) -> None:
    print(json.dumps({"verdict": verdict, "diagnostics": diagnostics}))


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        emit("unavailable", "usage: accept.py <workspace>")
        return 0
    report = os.environ.get("EU4_REVIEW_REPORT_PATH")
    if not report:
        emit("fail", "EU4_REVIEW_REPORT_PATH not set")
        return 0
    path = Path(report)
    if not path.is_file():
        emit("fail", f"review report missing: {path}")
        return 0
    workspace = Path(argv[1]).resolve()
    try:
        path.resolve().relative_to(workspace)
        emit("fail", "review report must be outside the candidate worktree")
        return 0
    except ValueError:
        pass
    try:
        review = load_review(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        emit("fail", f"review report unreadable: {exc}")
        return 0
    errors = validate_review(
        review,
        expected_candidate_commit=os.environ.get("EU4_REVIEW_EXPECTED_CANDIDATE") or None,
        expected_oracle_manifest_sha256=os.environ.get("EU4_REVIEW_EXPECTED_ORACLE") or None,
    )
    if errors:
        emit("fail", "invalid review: " + "; ".join(errors))
        return 0
    emit("pass", f"review operation complete; outcome={review.get('outcome')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
