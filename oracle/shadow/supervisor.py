"""Queue, deadline, output cap and process-group supervision for one batch.

Nested harness output is bounded here. ``docs/oracle/audits/F1-shadow-acceptance.md``
forbids an unbounded in-memory capture of child stdout: this module streams the
pipes, retains at most the cap, and kills the harness session at the cap or the
deadline.
:func:`run_harness_bounded` streams the harness through a pipe, retains at most
``output_limit_bytes``, and :func:`oracle.slice.harness.terminate_process_group`
kills the harness session when the cap or the deadline is hit. The worker
installs this runner before calling
:func:`oracle.function.replay.replay_capture`, so the frozen comparator still
owns match and mismatch.

This is the same application-level boundary as P3. It is not a sandbox, and it
does not launch EU IV or accept a live pointer or a session directory.
"""
from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from oracle.function.replay import replay_capture
from oracle.slice.harness import close_pipes, terminate_process_group
from oracle.shadow.contract import (
    REPORT_SCHEMA,
    SNAPSHOT_SCHEMA,
    CandidateIdentity,
    ResultError,
    ShadowContractError,
    SnapshotError,
    admission_failure,
    build_snapshot,
    classify_replay,
    make_result,
    parse_candidate,
)

# Extra wait so a worker that records its own deadline can flush the report
# before the supervisor escalates. The acceptance flood bound is deadline + 2s.
_SUPERVISOR_SLACK_SECONDS = 1.25
_KILL_GRACE_SECONDS = 0.5
_POLL_SECONDS = 0.01


class Containment(Exception):
    """The harness group was killed because the deadline or output cap fired."""

    def __init__(self, kind: str, output_bytes: int):
        if kind not in ("timeout", "crash"):
            raise ShadowContractError(f"unknown containment: {kind}")
        self.kind = kind
        self.output_bytes = output_bytes
        super().__init__(kind)


def _fsync_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _assert_inside(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise SnapshotError(f"{path} is outside the worker directory")
    return resolved


def _terminate_pgid(pgid: int, grace: float = _KILL_GRACE_SECONDS) -> None:
    """TERM-then-KILL a process group the supervisor does not directly reap.

    The escalation matches :func:`oracle.slice.harness.terminate_process_group`.
    The harness leader is the worker's child, so after the worker exits only
    the group signal can reach descendants such as the flood grandchild.
    """
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            break
        time.sleep(_POLL_SECONDS)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        time.sleep(_POLL_SECONDS)


def _read_pgid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    if not text.isdigit():
        return None
    return int(text)


def _terminate_recorded_harness(pgid_path: Path) -> None:
    pgid = _read_pgid(pgid_path)
    if pgid is not None:
        _terminate_pgid(pgid)


def _measured_output_bytes(work_dir: Path) -> int:
    """Same retention set the acceptance probe counts."""
    total = 0
    for path in work_dir.rglob("*"):
        if path.is_file() and path.name.startswith(("worker-", "capture-")):
            total += path.stat().st_size
    log = work_dir / "supervisor.stdout"
    if log.is_file():
        total += log.stat().st_size
    return total


def run_harness_bounded(harness: Path, slot: int, selector: int,
                        buffer_dword: int | None, prestate: bytes,
                        env: dict[str, str] | None, *, deadline: float,
                        output_limit_bytes: int, capture_path: Path,
                        pgid_path: Path, cwd: Path) -> dict[str, str]:
    """Run one reconstruction scenario with a hard cap on retained output.

    The command line matches ``reconstruction_diff.run_harness``. Stdout and
    stderr are pipes. Bytes past ``output_limit_bytes`` are not retained, and
    the harness process group is killed before this function returns a
    containment error. Child output is streamed, never buffered without a cap.
    """
    from reverse_engineering import reconstruction_diff as diff

    argument = "absent" if buffer_dword is None else f"{buffer_dword:08x}"
    command = [str(harness), "scenario", str(slot), f"{selector:x}", argument]
    child_env = diff.sanitizer_env() if env is None else env
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd),
        env=child_env,
        start_new_session=True,
        bufsize=0,
    )
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    _fsync_write(pgid_path, f"{proc.pid}\n".encode("ascii"))
    stdout = b""
    stderr = b""
    try:
        try:
            proc.stdin.write((prestate.hex() + "\n").encode("ascii"))
        except BrokenPipeError:
            pass
        proc.stdin.close()
        stdout, stderr, kind = _stream_bounded(
            proc, deadline=deadline, output_limit_bytes=output_limit_bytes)
        retained = stdout + stderr
        if kind is not None:
            terminate_process_group(proc, pgid=proc.pid)
            _fsync_write(capture_path, retained)
            raise Containment(kind, len(retained))
        _fsync_write(capture_path, retained)
        returncode = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        # Reap the session after every exit, including success, so a descendant
        # cannot outlive the leader. Same rule as the P3 harness.
        terminate_process_group(proc, pgid=proc.pid)
    except subprocess.TimeoutExpired:
        terminate_process_group(proc, pgid=proc.pid)
        produced = capture_path.stat().st_size if capture_path.is_file() else 0
        raise Containment("timeout", produced) from None
    finally:
        if proc.poll() is None:
            terminate_process_group(proc, pgid=proc.pid)
        close_pipes(proc)
    stdout_text = _decode(stdout, "stdout")
    stderr_text = _decode(stderr, "stderr")
    fields: dict[str, str] = {}
    for line in stdout_text.splitlines():
        key, _, value = line.partition("=")
        fields[key] = value
    fields["returncode"] = str(returncode)
    fields["stdout"] = stdout_text
    fields["stderr"] = stderr_text.strip()
    return fields


def _decode(data: bytes, stream: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeError as error:
        raise UnicodeError(f"{stream} is not utf-8: {error}") from error


def _stream_bounded(proc: subprocess.Popen[bytes], *, deadline: float,
                    output_limit_bytes: int) -> tuple[bytes, bytes, str | None]:
    """Read both pipes until EOF, the deadline, or the retention cap."""
    assert proc.stdout is not None and proc.stderr is not None
    streams = {proc.stdout.fileno(): bytearray(), proc.stderr.fileno(): bytearray()}
    stdout_fd = proc.stdout.fileno()
    open_fds = set(streams)
    kind: str | None = None
    while open_fds:
        if time.monotonic() >= deadline:
            kind = "timeout"
            break
        timeout = min(0.05, max(0.0, deadline - time.monotonic()))
        readable, _, _ = select.select(list(open_fds), [], [], timeout)
        if not readable:
            if proc.poll() is not None and not open_fds:
                break
            continue
        for fd in readable:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                open_fds.discard(fd)
                continue
            if not chunk:
                open_fds.discard(fd)
                continue
            retained = sum(len(buf) for buf in streams.values())
            room = output_limit_bytes - retained
            if room <= 0:
                kind = "crash"
                open_fds.clear()
                break
            if len(chunk) > room:
                streams[fd].extend(chunk[:room])
                kind = "crash"
                open_fds.clear()
                break
            streams[fd].extend(chunk)
    stdout = bytes(streams[stdout_fd])
    stderr = b"".join(buf for fd, buf in streams.items() if fd != stdout_fd)
    return stdout, stderr, kind


def _worker_argv(job_dir: Path) -> list[str]:
    return [sys.executable, "-m", "oracle.shadow.worker", str(job_dir)]


def _worker_env() -> dict[str, str]:
    """Import path for the worker, independent of the job directory it runs in."""
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parents[2])
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root if not current else os.pathsep.join((root, current))
    return env


def _containment_report(kind: str) -> dict[str, Any]:
    if kind == "timeout":
        return {
            "schema": REPORT_SCHEMA,
            "outcome": "timeout",
            "comparison": "infrastructure",
            "failures": ["infrastructure:timeout"],
            "output_bytes": 0,
        }
    return {
        "schema": REPORT_SCHEMA,
        "outcome": "crash",
        "comparison": "infrastructure",
        "failures": ["infrastructure:worker-crash"],
        "output_bytes": 0,
    }


def _read_report(job_dir: Path) -> dict[str, Any] | None:
    path = job_dir / "report.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(report, dict) or report.get("schema") != REPORT_SCHEMA:
        return None
    if report.get("outcome") not in ("completed", "timeout", "crash", "refused"):
        return None
    if report.get("comparison") not in ("match", "mismatch", "infrastructure", "none"):
        return None
    if not isinstance(report.get("failures"), list):
        return None
    return report


def _supervise(job_dir: Path, deadline_seconds: float) -> dict[str, Any]:
    """Run one worker in its own session and always reap both process groups."""
    pgid_path = job_dir / "pgid"
    proc = subprocess.Popen(
        _worker_argv(job_dir),
        cwd=str(job_dir),
        env=_worker_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    timed_out = False
    try:
        proc.wait(timeout=deadline_seconds + _SUPERVISOR_SLACK_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
    _terminate_recorded_harness(pgid_path)
    terminate_process_group(proc, pgid=proc.pid)
    close_pipes(proc)
    report = _read_report(job_dir)
    if timed_out:
        return _containment_report("timeout")
    if report is None:
        return _containment_report("crash")
    return report


def _prepare_job(work: Path, index: int, transcript: str,
                 candidate: CandidateIdentity, deadline_seconds: float,
                 output_limit_bytes: int) -> Path:
    job = work / "jobs" / str(index)
    job.mkdir(parents=True, exist_ok=False)
    _fsync_write(job / "snapshot.txt", transcript.encode("utf-8"))
    request = {
        "schema": SNAPSHOT_SCHEMA,
        "work_dir": str(work.resolve()),
        "harness_path": candidate.harness_path,
        "executable_sha256": candidate.executable_sha256,
        "source_sha256": candidate.source_sha256,
        "function_id": candidate.function_id,
        "deadline_seconds": deadline_seconds,
        "output_limit_bytes": output_limit_bytes,
        "capture_path": str((work / f"capture-{index}.out").resolve()),
        "pgid_path": str((job / "pgid").resolve()),
    }
    _fsync_write(job / "request.json", json.dumps(request, sort_keys=True).encode("utf-8"))
    return job


def _refuse(reason: str) -> tuple[str, str, list[str]]:
    if reason == "harness-unavailable":
        return "refused", "infrastructure", ["infrastructure:harness-unavailable"]
    if reason == "unsupported-function":
        return "refused", "none", ["reference:unsupported-function"]
    if reason == "identity":
        return "refused", "none", ["identity:executable-sha256"]
    if reason == "malformed":
        return "refused", "none", ["reference:malformed"]
    return "refused", "none", [reason]


def _check_limits(deadline_seconds: float, output_limit_bytes: int,
                  queue_limit: int) -> None:
    if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float)):
        raise ShadowContractError("deadline_seconds must be a number")
    if isinstance(output_limit_bytes, bool) or not isinstance(output_limit_bytes, int):
        raise ShadowContractError("output_limit_bytes must be an integer")
    if isinstance(queue_limit, bool) or not isinstance(queue_limit, int):
        raise ShadowContractError("queue_limit must be an integer")
    if output_limit_bytes < 0 or queue_limit < 0:
        raise ShadowContractError("limits must be non-negative")


def _run_owned(snapshots: Sequence[str], candidate: Mapping[str, Any],
               work_dir: str | Path, *,
               deadline_seconds: float = 5.0,
               output_limit_bytes: int = 65536,
               queue_limit: int = 64) -> dict[str, Any]:
    """Admit owned transcript copies, run each one in a supervised worker.

    ``snapshots`` are transcript texts. A filesystem session is never accepted.
    Items past ``queue_limit`` are dropped. Each accepted item is executed only
    when the candidate identity matches the harness bytes and the function is
    the frozen one. The caller blocks until every admitted item has a terminal
    outcome; a worker crash or timeout does not prevent the rest of the queue
    from finishing.
    """
    _check_limits(deadline_seconds, output_limit_bytes, queue_limit)
    if isinstance(snapshots, (str, bytes)) or not isinstance(snapshots, Sequence):
        raise SnapshotError("snapshots must be a sequence of transcript strings")
    work = Path(work_dir)
    if work.exists() and not work.is_dir():
        raise SnapshotError("work directory is not a directory")
    work.mkdir(parents=True, exist_ok=True)

    try:
        identity = parse_candidate(candidate)
        identity_error = None
    except SnapshotError as error:
        identity = None
        identity_error = str(error)

    outcomes: list[str] = []
    comparisons: list[str] = []
    failures: list[list[str]] = []
    remaining = output_limit_bytes

    for index, transcript in enumerate(snapshots):
        if index >= queue_limit:
            outcomes.append("dropped")
            comparisons.append("none")
            failures.append(["queue:capacity"])
            continue
        if not isinstance(transcript, str):
            outcomes.append("refused")
            comparisons.append("none")
            failures.append(["reference:malformed"])
            continue
        owned = build_snapshot(transcript)
        if identity is None or identity_error is not None:
            outcomes.append("refused")
            comparisons.append("none")
            failures.append([identity_error or "missing descriptor input"])
            continue
        reason = admission_failure(owned.transcript, identity)
        if reason is not None:
            outcome, comparison, failure = _refuse(reason)
            outcomes.append(outcome)
            comparisons.append(comparison)
            failures.append(failure)
            continue
        if deadline_seconds <= 0:
            outcomes.append("timeout")
            comparisons.append("infrastructure")
            failures.append(["infrastructure:timeout"])
            continue
        if remaining <= 0:
            outcomes.append("crash")
            comparisons.append("infrastructure")
            failures.append(["infrastructure:output-cap"])
            continue
        job = _prepare_job(
            work, index, owned.transcript, identity,
            float(deadline_seconds), remaining)
        report = _supervise(job, float(deadline_seconds))
        outcomes.append(str(report["outcome"]))
        comparisons.append(str(report["comparison"]))
        failures.append([str(item) for item in report["failures"]])
        remaining = output_limit_bytes - _measured_output_bytes(work)
        if remaining < 0:
            raise ResultError("retained output exceeded the cap")

    return make_result(
        outcomes, comparisons, _measured_output_bytes(work), failures)


class _OwnedRunner:
    """Call ``run_owned`` without becoming a bound method on a test class.

    The acceptance suite stores the public callable on a ``TestCase`` class.
    A plain function would then receive the test instance as its first argument.
    """

    def __call__(self, snapshots: Sequence[str], candidate: Mapping[str, Any],
                 work_dir: str | Path, *,
                 deadline_seconds: float = 5.0,
                 output_limit_bytes: int = 65536,
                 queue_limit: int = 64) -> dict[str, Any]:
        return _run_owned(
            snapshots, candidate, work_dir,
            deadline_seconds=deadline_seconds,
            output_limit_bytes=output_limit_bytes,
            queue_limit=queue_limit)


run_owned = _OwnedRunner()


def execute_replay(job_dir: Path) -> dict[str, Any]:
    """Worker body: replay one owned snapshot through the bounded runner."""
    request = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
    root = Path(request["work_dir"]).resolve()
    job_dir = _assert_inside(root, job_dir)
    capture_path = _assert_inside(root, Path(request["capture_path"]))
    pgid_path = _assert_inside(root, Path(request["pgid_path"]))
    transcript = (job_dir / "snapshot.txt").read_text(encoding="utf-8")
    candidate = CandidateIdentity(
        function_id=str(request["function_id"]),
        source_sha256=str(request["source_sha256"]),
        executable_sha256=str(request["executable_sha256"]),
        harness_path=str(request["harness_path"]),
    )
    reason = admission_failure(transcript, candidate)
    if reason is not None:
        outcome, comparison, failure = _refuse(reason)
        return _report(outcome, comparison, failure, 0)

    deadline = time.monotonic() + float(request["deadline_seconds"])
    limit = int(request["output_limit_bytes"])

    import reverse_engineering.reconstruction_diff as diff

    def bounded(harness: Path, slot: int, selector: int, buffer_dword: int | None,
                prestate: bytes, env: dict[str, str] | None = None) -> dict[str, str]:
        return run_harness_bounded(
            Path(harness), slot, selector, buffer_dword, prestate, env,
            deadline=deadline, output_limit_bytes=limit,
            capture_path=capture_path, pgid_path=pgid_path, cwd=job_dir)

    diff.run_harness = bounded
    try:
        replayed = replay_capture(transcript, candidate.harness_path)
    except Containment as error:
        failure = ("infrastructure:timeout" if error.kind == "timeout"
                   else "infrastructure:output-cap")
        return _report(error.kind, "infrastructure", [failure], error.output_bytes)
    outcome, comparison, failure = classify_replay(replayed)
    produced = capture_path.stat().st_size if capture_path.is_file() else 0
    return _report(outcome, comparison, failure, produced)


def _report(outcome: str, comparison: str, failures: Sequence[str],
            output_bytes: int) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "outcome": outcome,
        "comparison": comparison,
        "failures": list(failures),
        "output_bytes": output_bytes,
    }
