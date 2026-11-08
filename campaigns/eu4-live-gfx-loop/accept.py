#!/usr/bin/env python3
"""Offline acceptance for the live-gfx replay experiment."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run(command: list[str], worktree: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, cwd=worktree, env=env, capture_output=True, text=True, check=False
    )


def emit(verdict: str, reason: str, **extra) -> int:
    payload = {"verdict": verdict, "reason": reason}
    payload.update(extra)
    print(json.dumps(payload, sort_keys=True))
    if verdict == "pass":
        return 0
    if verdict == "fail":
        return 1
    return 2


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: accept.py <worktree>", file=sys.stderr)
        return 2
    worktree = Path(sys.argv[1]).resolve()

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{worktree}:{worktree / 'benchmark'}"
    tests = run(
        [
            sys.executable,
            "-m",
            "unittest",
            "tests.test_gfx_observe",
            "tests.test_gfx_op_diff",
            "tests.test_gfx_startup_diff",
            "tests.test_gfx_startup_diff_metal",
            "-q",
        ],
        worktree,
        env,
    )
    if tests.returncode != 0:
        return emit(
            "fail",
            "candidate tests failed",
            stdout=tests.stdout[-4000:],
            stderr=tests.stderr[-4000:],
        )

    note = worktree / "analysis/automation/pilot-gfx-set-viewport-repair/note.md"
    if not note.is_file() or not note.read_text().strip():
        return emit("fail", "repair note is missing or empty")

    with tempfile.TemporaryDirectory() as tmp:
        replay = run(
            [
                sys.executable,
                "-m",
                "automation.live_gfx",
                "replay",
                "--capture",
                "analysis/gfx-ops/v0/capture",
                "--output",
                str(Path(tmp) / "replay"),
            ],
            worktree,
            env,
        )
    try:
        result = json.loads(replay.stdout)
    except json.JSONDecodeError:
        return emit(
            "unavailable",
            "replay did not emit JSON",
            stdout=replay.stdout[-4000:],
            stderr=replay.stderr[-4000:],
        )
    status = result.get("status")
    if status == "pass":
        return emit("pass", "committed capture replays and candidate tests pass")
    if status == "mismatch":
        return emit(
            "fail",
            "committed capture still mismatches",
            replay=result,
            stderr=replay.stderr[-2000:],
        )
    return emit(
        "unavailable",
        f"replay returned {status}",
        replay=result,
        stderr=replay.stderr[-2000:],
    )


if __name__ == "__main__":
    raise SystemExit(main())
