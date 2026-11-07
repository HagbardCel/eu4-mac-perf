#!/usr/bin/env python3
"""Reviewer campaign accept: pass when out-of-tree eu4-review/v1 report is complete.

The review *outcome* (qualified|rejected|unresolved) is consumed by the outer
handoff controller. This checker only confirms the review operation finished.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / ".git").exists() and (p / "automation").is_dir()
)
sys.path.insert(0, str(_REPO))

from automation.review_result import load_review, validate_review  # noqa: E402


def emit(verdict: str, diagnostics: str) -> None:
    print(json.dumps({"verdict": verdict, "diagnostics": diagnostics}))


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        emit("unavailable", "usage: accept.py <workspace>")
        return 0
    report = os.environ.get("EU4_REVIEW_REPORT_PATH")
    if not report:
        emit("fail", "EU4_REVIEW_REPORT_PATH not set (report must be outside worktree)")
        return 0
    path = Path(report)
    if not path.is_file():
        emit("fail", f"review report missing: {path}")
        return 0
    # Report must not live inside the candidate worktree
    workspace = Path(argv[1]).resolve()
    try:
        path.resolve().relative_to(workspace)
        emit("fail", "review report must be outside the candidate worktree")
        return 0
    except ValueError:
        pass
    review = load_review(path)
    expected_commit = os.environ.get("EU4_REVIEW_EXPECTED_CANDIDATE")
    expected_oracle = os.environ.get("EU4_REVIEW_EXPECTED_ORACLE")
    errors = validate_review(
        review,
        expected_candidate_commit=expected_commit or None,
        expected_oracle_manifest_sha256=expected_oracle or None,
    )
    if errors:
        emit("fail", "invalid review: " + "; ".join(errors))
        return 0
    emit(
        "pass",
        f"review operation complete; outcome={review.get('outcome')}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
