"""The one acceptance implementation both backends use.

A checker is an argv command that prints one JSON object. Its verdict vocabulary
and the way malformed output is reported are project contracts, not backend
details: the OpenCode runner and the DSH bridge must never disagree about what a
candidate's checker said. This module is the only place that runs a checker.

Verdicts are ``pass``, ``fail``, ``inconclusive``, ``blocked`` and
``unavailable``. An execution problem -- timeout, truncated output, unreadable
JSON, an unknown verdict, or ``pass`` with a non-zero exit -- is reported as
``ok: False`` rather than as a verdict, so a caller can keep "the checker
rejected this" separate from "the checker could not run".
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Protocol, Sequence

VERDICTS = ("pass", "fail", "inconclusive", "blocked", "unavailable")
OUTPUT_CAP = 65536


class CheckerBinding(Protocol):
    """The three facts running a checker needs, and nothing more."""

    accept: Sequence[str]
    root: Path
    accept_timeout: float


def run_accept(campaign: CheckerBinding, workspace: Path) -> dict:
    """Run one checker against *workspace* and classify what it printed."""
    env = os.environ.copy()
    env.pop("OPENCODE_SERVER_PASSWORD", None)
    env.pop("OPENCODE_SERVER_USERNAME", None)
    try:
        completed = subprocess.run(
            [*campaign.accept, str(workspace)],
            cwd=campaign.root,
            env=env,
            capture_output=True,
            timeout=campaign.accept_timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return _execution_failure(None, "timeout")
    if len(completed.stdout) > OUTPUT_CAP:
        return _execution_failure(completed.returncode, "truncated")
    try:
        payload = json.loads(completed.stdout.decode())
    except (json.JSONDecodeError, UnicodeError):
        return _execution_failure(completed.returncode, "unreadable")
    verdict = payload.get("verdict") if isinstance(payload, dict) else None
    diagnostics = ""
    if isinstance(payload, dict) and payload.get("diagnostics") is not None:
        diagnostics = str(payload["diagnostics"])
    if verdict not in VERDICTS:
        return _execution_failure(completed.returncode, diagnostics or "unreadable")
    if verdict == "pass" and completed.returncode != 0:
        return _execution_failure(completed.returncode, diagnostics)
    return {
        "ok": True,
        "verdict": verdict,
        "exit_code": completed.returncode,
        "diagnostics": diagnostics,
    }


def _execution_failure(exit_code, diagnostics: str) -> dict:
    return {
        "ok": False,
        "verdict": None,
        "exit_code": exit_code,
        "diagnostics": diagnostics,
    }
