"""F1 shadow invocation acceptance contract (independent of implementation layout)."""
from __future__ import annotations

import os
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

RESULT_SCHEMA = "eu4-oracle-shadow-result/v0"
TERMINAL_OUTCOMES = frozenset({
    "completed", "timeout", "crash", "refused", "dropped",
})
COMPARISONS = frozenset({"match", "mismatch", "infrastructure", "none"})
FLOOD_OUTCOMES = frozenset({"timeout", "crash"})


def assert_result_shape(result: Mapping[str, Any], snapshot_count: int) -> None:
    if result.get("schema") != RESULT_SCHEMA:
        raise AssertionError(f"schema must be {RESULT_SCHEMA!r}")
    for key in ("outcomes", "comparisons", "output_bytes", "accounting"):
        if key not in result:
            raise AssertionError(f"missing result key: {key}")
    if len(result["outcomes"]) != snapshot_count:
        raise AssertionError("outcomes length must match submitted snapshots")
    if len(result["comparisons"]) != snapshot_count:
        raise AssertionError("comparisons length must match submitted snapshots")
    for outcome in result["outcomes"]:
        if outcome not in TERMINAL_OUTCOMES:
            raise AssertionError(f"unknown outcome: {outcome}")
    for comparison in result["comparisons"]:
        if comparison not in COMPARISONS:
            raise AssertionError(f"unknown comparison: {comparison}")


def assert_accounting_partition(
        result: Mapping[str, Any],
        submitted: int,
        *,
        dropped_before_accept: int = 0) -> None:
    acc = result["accounting"]
    for key in ("submitted", "accepted", "completed", "refused",
                "timed_out", "crashed", "dropped"):
        if key not in acc:
            raise AssertionError(f"accounting missing {key}")
        if acc[key] < 0:
            raise AssertionError(f"negative accounting counter: {key}")
    if acc["submitted"] != submitted:
        raise AssertionError("submitted count mismatch")
    if acc["submitted"] != acc["accepted"] + acc["dropped"]:
        raise AssertionError("N_submitted must equal N_accepted + N_dropped")
    if acc["accepted"] != (
            acc["completed"] + acc["refused"] + acc["timed_out"] + acc["crashed"]):
        raise AssertionError(
            "N_accepted must equal completed + refused + timed_out + crashed")
    if dropped_before_accept and acc["dropped"] != dropped_before_accept:
        raise AssertionError("unexpected dropped count")
    counts = Counter(result["outcomes"])
    mapping = {
        "completed": "completed",
        "refused": "refused",
        "timeout": "timed_out",
        "crash": "crashed",
        "dropped": "dropped",
    }
    for outcome, acc_key in mapping.items():
        if counts[outcome] != acc[acc_key]:
            raise AssertionError(
                f"outcomes[{outcome}]={counts[outcome]} != accounting.{acc_key}")


def read_observed_output_bytes(work_dir: Path) -> int:
    """Independently measure retained supervisor output artifacts."""
    total = 0
    for path in work_dir.rglob("*"):
        if path.is_file() and path.name.startswith(("worker-", "capture-")):
            total += path.stat().st_size
    log = work_dir / "supervisor.stdout"
    if log.is_file():
        total += log.stat().st_size
    return total


def _pid_liveness(pid: int) -> str:
    """Classify a pid without ``ps`` (``os.kill`` cannot prove a zombie)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        return "running"
    except OSError:
        return "unknown"
    # The pid still exists.  A reaped-but-unwaited child is a zombie, which
    # ``os.kill`` cannot distinguish from a live process, so an existence
    # answer that ``ps`` could not refine is conservatively "running".
    return "running"


def pid_process_state(pid: int, *, timeout: float = 1.0) -> str:
    """Return running, zombie, gone, or unknown (inconclusive).

    ``ps`` can transiently fail (a timeout under heavy load, or an empty row
    when the pid vanished during the query).  Those cases fall back to
    ``os.kill`` so one slow process-table query cannot turn a clean containment
    result into an unverifiable one.
    """
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "state="],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # Retried by assert_no_running_pids inside its bounded deadline.
        return "unknown"
    except OSError:
        # ``ps`` is unavailable; os.kill is the only remaining answer.
        return _pid_liveness(pid)
    if proc.returncode != 0:
        return "gone"
    state = proc.stdout.strip()
    if not state:
        # The pid disappeared between the snapshot and the row lookup: retry.
        return "unknown"
    if state[0] == "Z":
        return "zombie"
    return "running"


def assert_no_running_pids(pid_file: Path, *, timeout: float = 5.0) -> None:
    """Wait, bounded, until every supervised pid is gone or a zombie.

    A transient ``ps`` failure is retried inside the deadline instead of
    aborting the observation: the previous single-shot form failed closed on a
    clean run when one process-table query timed out under full-suite load.
    """
    if not pid_file.is_file():
        raise AssertionError("flood fixture did not write pid file")
    pids = [int(line) for line in pid_file.read_text().split() if line.strip()]
    deadline = time.monotonic() + timeout
    states: dict[int, str] = {pid: "unknown" for pid in pids}
    while time.monotonic() < deadline:
        states = {pid: pid_process_state(pid) for pid in pids}
        if all(state in ("gone", "zombie") for state in states.values()):
            return
        if any(state == "running" for state in states.values()):
            time.sleep(0.05)
            continue
        time.sleep(0.05)
    running = [pid for pid, state in states.items() if state == "running"]
    if running:
        raise AssertionError(f"supervised PIDs still running: {running}")
    unknown = [pid for pid, state in states.items() if state == "unknown"]
    if unknown:
        raise AssertionError(
            f"cannot verify supervised process cleanup for PIDs: {unknown}")


def assert_flood_stage_markers(work_dir: Path) -> None:
    for name in (
            "flood.child.started",
            "flood.grandchild.started",
            "flood.output.started",
    ):
        if not (work_dir / name).is_file():
            raise AssertionError(f"flood fixture missing stage marker: {name}")


def assert_flood_supervised(
        work_dir: Path,
        result: Mapping[str, Any],
        *,
        deadline_seconds: float,
        output_limit_bytes: int,
        started: float,
        observed_output_bytes: int) -> None:
    started_marker = work_dir / "flood.started"
    if not started_marker.is_file():
        raise AssertionError("flood fixture did not start")
    if result["outcomes"] and result["outcomes"][0] == "refused":
        raise AssertionError("refused alone is not flood containment proof")
    if result["outcomes"] and result["outcomes"][0] not in FLOOD_OUTCOMES:
        raise AssertionError(
            f"flood outcome must be timeout or crash, got {result['outcomes'][0]}")
    if result["comparisons"] and result["comparisons"][0] == "match":
        raise AssertionError("flood fixture must not report match")
    elapsed = time.monotonic() - started
    if elapsed > deadline_seconds + 2.0:
        raise AssertionError(f"supervisor exceeded deadline ({elapsed:.2f}s)")
    if observed_output_bytes > output_limit_bytes:
        raise AssertionError(
            f"observed retained output {observed_output_bytes} > cap {output_limit_bytes}")
    if result["output_bytes"] > output_limit_bytes:
        raise AssertionError("reported output_bytes exceeds cap")
    assert_no_running_pids(work_dir / "flood.pid")


def assert_executable_identity_enforced(result: Mapping[str, Any]) -> None:
    if result["outcomes"][0] not in ("refused", "crashed", "timeout"):
        raise AssertionError(
            "replacing harness without updating executable_sha256 must not succeed")
    if result["comparisons"][0] == "match":
        raise AssertionError("identity mismatch must not report match")


def prepare_owned_executable(source: Path, work_dir: Path) -> Path:
    owned = work_dir / "candidate_executable"
    owned.write_bytes(source.read_bytes())
    owned.chmod(0o755)
    return owned


def weak_supervisor_stand_in(
        snapshots: Sequence[str],
        **_kwargs: Any) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "outcomes": ["completed"] * len(snapshots),
        "comparisons": ["match"] * len(snapshots),
        "output_bytes": 64,
        "accounting": {
            "submitted": len(snapshots),
            "accepted": len(snapshots),
            "completed": len(snapshots),
            "refused": 0,
            "timed_out": 0,
            "crashed": 0,
            "dropped": 0,
        },
    }
