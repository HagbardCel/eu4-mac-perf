"""Trusted, run-scoped state shared with the MCP server process.

The controller is one process; the MCP server is another. The server cannot ask
the controller for the remaining time, and it must never accept a deadline from
the agent. So the controller persists the immutable annotations of a run -
identity and an **absolute monotonic deadline** - into controller-owned state
that the server loads at startup and enforces independently.

The deadline is a monotonic timestamp, so it is only meaningful within one system
boot. ``run_state`` records that assumption explicitly: a boot token is stored
where the platform can provide one, and a declared budget bounds how large the
remaining time may legitimately appear to be. If the monotonic clock appears to
have been reset, loading **fails closed** rather than granting a fresh budget.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RUN_STATE_FILE = "run-state.json"
SCHEMA_VERSION = "eu4-oracle-runstate/v1"

#: Remaining time may exceed the declared budget only by scheduling slop.
_CLOCK_RESET_SLACK_S = 1.0


class RunStateError(RuntimeError):
    """Trusted run state is missing, malformed or provably stale."""


def _is_finite_number(value: Any) -> bool:
    """Whether ``value`` is a real, finite number.

    ``NaN`` and the infinities are excluded because comparisons against them are
    always false - ``time.monotonic() >= nan`` never expires a run. JSON admits
    ``NaN``/``Infinity``, and a very large integer literal can overflow the
    conversion to float, so both are rejected here rather than downstream.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


@dataclass(frozen=True)
class RunState:
    run_id: str
    deadline_monotonic: float
    budget_seconds: float
    boot_token: str | None

    def expired(self) -> bool:
        return time.monotonic() >= self.deadline_monotonic

    def remaining(self) -> float:
        return self.deadline_monotonic - time.monotonic()


def boot_token() -> str | None:
    """A best-effort identifier for the current boot, or ``None``.

    Linux exposes ``/proc/sys/kernel/random/boot_id``; macOS exposes the boot
    timestamp through ``sysctl``. Where neither is available the monotonic
    remaining-time bound in :func:`load_run_state` still applies.
    """
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        if value:
            return f"linux:{value}"
    except OSError:
        pass
    try:
        completed = subprocess.run(
            ["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"sec\s*=\s*(\d+)", completed.stdout or "")
    if match:
        return f"darwin:{match.group(1)}"
    return None


def write_run_state(run_dir: Path, *, run_id: str, deadline_monotonic: float,
                    budget_seconds: float) -> Path:
    """Persist the controller-owned run state. Controller-only.

    A non-finite or overflowed deadline or budget is refused rather than
    written: it could only make the MCP server's expiry check meaningless.
    """
    if not isinstance(run_id, str) or not run_id:
        raise RunStateError("run_id is required")
    if not _is_finite_number(deadline_monotonic):
        raise RunStateError("deadline_monotonic must be a finite number")
    if not _is_finite_number(budget_seconds) or float(budget_seconds) <= 0:
        raise RunStateError("budget_seconds must be positive and finite")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "deadline_monotonic": float(deadline_monotonic),
        "budget_seconds": float(budget_seconds),
        "boot_token": boot_token(),
        "created": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    path = Path(run_dir) / RUN_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_run_state(run_dir: Path) -> RunState:
    """Load and validate trusted run state. Fails closed on any doubt."""
    path = Path(run_dir) / RUN_STATE_FILE
    if not path.is_file():
        raise RunStateError(f"run state is missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunStateError(f"run state is unreadable: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise RunStateError("run state has an unexpected schema")

    run_id = data.get("run_id")
    deadline = data.get("deadline_monotonic")
    budget = data.get("budget_seconds")
    if not isinstance(run_id, str) or not run_id:
        raise RunStateError("run state has no run_id")
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        raise RunStateError("run state has no numeric deadline")
    if not _is_finite_number(deadline):
        raise RunStateError("run state has a non-finite deadline")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)):
        raise RunStateError("run state has no numeric budget")
    if not _is_finite_number(budget) or float(budget) <= 0:
        raise RunStateError("run state has no finite positive budget")

    recorded_boot = data.get("boot_token")
    current_boot = boot_token()
    if recorded_boot is not None and current_boot is not None and recorded_boot != current_boot:
        raise RunStateError("run state was written on a different boot")

    remaining = float(deadline) - time.monotonic()
    if remaining > float(budget) + _CLOCK_RESET_SLACK_S:
        # The monotonic clock cannot hand back more time than the run was
        # granted; a larger value means the clock was reset. Deny.
        raise RunStateError("monotonic clock was reset since the run was created")

    return RunState(run_id=run_id, deadline_monotonic=float(deadline),
                    budget_seconds=float(budget), boot_token=recorded_boot)
