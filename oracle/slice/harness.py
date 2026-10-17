"""Trusted build and execution harness for the Oracle P3 synthetic slice.

The harness - never the candidate - constructs the actual observation. For each
build it assembles a private directory containing the trusted driver and header
plus the candidate source, so a candidate cannot redefine the ABI or the
observation protocol.

Three guarantees are enforced here:

* **Whole-group termination.** Every child starts in its own session, so the
  child's pid is also its process-group id. The *group* is signalled after every
  exit - timed out or not - and polled to extinction, escalating to ``SIGKILL``;
  the leader's exit is never taken as evidence that its descendants are gone.
* **Bounded wall clock.** Compilation and every fixture run are bounded by the
  smaller of their operation-specific limit and the remaining controller
  deadline, and a run whose deadline has already passed does not start.
* **Bounded output.** Child stdout/stderr is redirected to files under an
  OS-enforced size limit, so a process cannot consume unbounded memory (or fill
  the disk). Retained text is capped separately; output over the cap is reported
  as *incomplete*, never as equality.

The line protocol emitted by ``driver.c`` is parsed here into an observation
envelope. Missing, malformed or truncated output is reported as an *incomplete*
run, never as equality.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # POSIX-only; the slice already relies on process groups and signals.
    import resource
except ImportError:  # pragma: no cover - non-POSIX platform
    resource = None  # type: ignore[assignment]

from oracle.slice import compare
from oracle.slice.corpus import Fixture

SLICE_DIR = Path(__file__).resolve().parent
DRIVER_SOURCE = SLICE_DIR / "driver.c"
HEADER_SOURCE = SLICE_DIR / "synth.h"

#: Fixed, recorded flags. ``-fwrapv`` makes the counter-overflow fixtures
#: defined behavior instead of UB, so expected bytes are reproducible.
COMPILE_FLAGS: tuple[str, ...] = ("-std=c11", "-O0", "-fwrapv", "-fno-strict-aliasing")

STATE_BYTES = 12
DEFAULT_TIMEOUT_S = 10.0
COMPILE_TIMEOUT_S = 120.0
#: Best-effort bound on the compiler-identity probe (``cc --version``).
COMPILER_PROBE_TIMEOUT_S = 20.0
KILL_GRACE_S = 0.5

#: Hard cap on bytes any single child may write (OS-enforced via RLIMIT_FSIZE).
#: A writer that exceeds it is signalled, so output *production* is bounded - not
#: merely output retention. It must stay comfortably above the linked binary.
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
#: Bytes of each stream retained for parsing and diagnostics.
MAX_CAPTURE_BYTES = 256 * 1024
#: Polling interval while waiting for a process group to disappear.
PROBE_INTERVAL_S = 0.01


class HarnessError(RuntimeError):
    """Trusted-harness fault: not attributable to the candidate."""


class BuildError(HarnessError):
    """The compiler rejected the candidate (or link failed)."""

    def __init__(self, message: str, stderr: str):
        super().__init__(message)
        self.stderr = stderr


class DeadlineExpired(HarnessError):
    """A blocking operation was refused because the deadline had passed."""


@dataclass(frozen=True)
class Build:
    exe: Path
    build_dir: Path
    cc: str
    flags: tuple[str, ...]
    compiler_version: str


@dataclass(frozen=True)
class RawRun:
    exit_kind: str  # normal | crash | timeout | incomplete
    returncode: int
    stdout: str
    stderr: str
    duration_s: float


@dataclass(frozen=True)
class Capture:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    duration_s: float
    #: The bounded capture files themselves. ``stdout``/``stderr`` retain at most
    #: :data:`MAX_CAPTURE_BYTES` for parsing; a caller that needs *complete*
    #: bounded evidence (for example an event-stream digest) reads these files,
    #: which the OS caps at :data:`MAX_OUTPUT_BYTES`. They are ``None`` when no
    #: process was started.
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0


# --- deadlines ----------------------------------------------------------------


def remaining(deadline: float | None) -> float:
    """Seconds left until ``deadline``, or ``inf`` when unbounded."""
    if deadline is None:
        return float("inf")
    return deadline - time.monotonic()


def _effective_timeout(limit: float, deadline: float | None) -> float:
    """The smaller of an operation-specific limit and the remaining deadline."""
    return min(limit, remaining(deadline))


# --- process-group termination ------------------------------------------------


def _signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _group_alive(pgid: int) -> bool:
    """Whether any member of the process group still exists.

    ``killpg(pgid, 0)`` reports group existence; it cannot prove that only
    *runnable* members remain (a zombie keeps a group visible). Escalation is
    therefore bounded in time rather than driven by this probe alone.
    """
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - defensive
        return True
    return True


def _await_group_exit(pgid: int, grace: float) -> None:
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _group_alive(pgid):
            return
        time.sleep(PROBE_INTERVAL_S)


def terminate_process_group(proc: subprocess.Popen, *, grace: float = KILL_GRACE_S,
                            pgid: int | None = None) -> None:
    """Terminate a whole process group, then reap the leader.

    The group id is the leader's pid, because children are started with
    ``start_new_session=True``. It is passed in (or read from the live pid) but
    never recovered with ``getpgid`` *after* the leader may have exited.
    """
    group = pgid if pgid is not None else proc.pid
    _signal_group(group, signal.SIGTERM)
    # Reap the leader promptly: an unreaped zombie still counts as a member of
    # its process group, so it would otherwise masquerade as a live group.
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    if not _group_alive(group):
        return
    _signal_group(group, signal.SIGKILL)
    _await_group_exit(group, grace)
    # The controller owns its direct children and can reap them; orphaned
    # grandchildren are terminated but not waitable by us.
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        pass


def close_pipes(proc: subprocess.Popen) -> None:
    """Close a process's stdio pipes after it has been reaped."""
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:  # pragma: no cover - defensive
                pass


_terminate_group = terminate_process_group


# --- bounded subprocess execution ---------------------------------------------


def _limit_output() -> None:  # pragma: no cover - runs in the forked child
    if resource is not None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))


def _read_bounded(path: Path, cap: int = MAX_CAPTURE_BYTES) -> tuple[str, bool]:
    try:
        size = path.stat().st_size
    except OSError:  # pragma: no cover - defensive
        return "", False
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(cap)
    except OSError:  # pragma: no cover - defensive
        return "", False
    return data.decode("utf-8", errors="replace"), size > cap


def _file_size(path: Path) -> int:
    """Size of a capture file in bytes, or 0 if it cannot be read."""
    try:
        return path.stat().st_size
    except OSError:  # pragma: no cover - defensive
        return 0


def run_bounded(argv: list[str], *, cwd: Path | None, env: dict[str, str] | None,
                timeout: float, capture_dir: Path, tag: str) -> Capture:
    """Run a command in its own process group with bounded time and output."""
    if timeout <= 0:
        return Capture(returncode=-1, stdout="", timed_out=True, truncated=False,
                       stderr="deadline expired before the process started", duration_s=0.0)
    capture_dir = Path(capture_dir)
    capture_dir.mkdir(parents=True, exist_ok=True)
    safe_tag = "".join(c if c.isalnum() or c in "-_." else "_" for c in tag)
    out_path = capture_dir / f"{safe_tag}.out"
    err_path = capture_dir / f"{safe_tag}.err"
    started = time.monotonic()
    timed_out = False
    with out_path.open("wb") as out_file, err_path.open("wb") as err_file:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out_file,
            stderr=err_file,
            start_new_session=True,
            preexec_fn=_limit_output,
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        # Clean the group up after *every* exit, not only after a timeout: a
        # leader that exits - even successfully - must not leave a descendant
        # running, and a supervisor that only tracks its own children would
        # never learn about such a descendant. The original ``pgid`` (the
        # leader's pid) is retained from creation. On the common path the group
        # is already empty and this is a no-op.
        terminate_process_group(proc, pgid=proc.pid)
    stdout, out_truncated = _read_bounded(out_path)
    stderr, err_truncated = _read_bounded(err_path)
    return Capture(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        truncated=out_truncated or err_truncated,
        duration_s=time.monotonic() - started,
        stdout_path=out_path,
        stderr_path=err_path,
        stdout_bytes=_file_size(out_path),
        stderr_bytes=_file_size(err_path),
    )


def compiler_version(cc: str = "cc", *, deadline: float | None = None,
                     capture_dir: Path | None = None) -> str:
    """Best-effort compiler identity, bounded by the caller's ``deadline``.

    The probe is an ordinary bounded child: it may not outlive the run, and its
    output is capped like any other. A probe that cannot *complete* - an expired
    budget, a timeout or output over the cap - or that exits non-zero reports
    ``"unknown"`` rather than asserting an identity it could not establish. A
    failure to launch the compiler at all is still a trusted-harness fault.
    """
    budget = _effective_timeout(COMPILER_PROBE_TIMEOUT_S, deadline)
    if budget <= 0:
        return "unknown"
    scratch: Path | None = None
    if capture_dir is not None:
        directory = Path(capture_dir)
    else:
        directory = scratch = Path(tempfile.mkdtemp(prefix="oracle-cc-probe-"))
    try:
        try:
            capture = run_bounded([cc, "--version"], cwd=directory, env=None, timeout=budget,
                                  capture_dir=directory, tag="cc-version")
        except OSError as exc:
            raise HarnessError(f"cannot run compiler {cc!r}: {exc}") from exc
        if capture.timed_out or capture.truncated or capture.returncode != 0:
            return "unknown"
        lines = (capture.stdout or capture.stderr).strip().splitlines()
        return lines[0].strip() if lines else "unknown"
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def build(candidate: Path, build_dir: Path, *, cc: str = "cc", deadline: float | None = None,
          timeout_s: float = COMPILE_TIMEOUT_S) -> Build:
    """Compile the trusted driver together with ``candidate`` in ``build_dir``."""
    candidate = Path(candidate)
    if not candidate.is_file():
        raise BuildError(f"candidate source is not a regular file: {candidate}", "")
    if candidate.is_symlink():
        raise BuildError(f"candidate source must not be a symlink: {candidate}", "")

    budget = _effective_timeout(timeout_s, deadline)
    if budget <= 0:
        raise DeadlineExpired("run deadline expired before compilation")

    shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    # Trusted assets are copied last, so a candidate-supplied driver.c/synth.h
    # cannot survive into the build directory.
    shutil.copyfile(candidate, build_dir / "candidate.c")
    shutil.copyfile(DRIVER_SOURCE, build_dir / "driver.c")
    shutil.copyfile(HEADER_SOURCE, build_dir / "synth.h")

    exe = build_dir / "candidate.bin"
    argv = [cc, *COMPILE_FLAGS, "-I", str(build_dir), str(build_dir / "driver.c"),
            str(build_dir / "candidate.c"), "-o", str(exe)]
    try:
        capture = run_bounded(argv, cwd=build_dir, env=None, timeout=budget,
                              capture_dir=build_dir, tag="compile")
    except OSError as exc:
        raise HarnessError(f"compiler invocation failed: {exc}") from exc
    stderr = capture.stdout + capture.stderr
    if capture.timed_out:
        raise HarnessError(f"compiler exceeded its {budget:.3f}s budget")
    if capture.truncated:
        raise HarnessError("compiler output exceeded the capture cap")
    if capture.returncode != 0 or not exe.exists():
        raise BuildError("candidate failed to compile", stderr)
    return Build(exe=exe, build_dir=build_dir, cc=cc, flags=COMPILE_FLAGS,
                 compiler_version=compiler_version(cc, deadline=deadline, capture_dir=build_dir))


def run_fixture(build: Build, fixture: Fixture, *, timeout_s: float = DEFAULT_TIMEOUT_S,
                cwd: Path | None = None, deadline: float | None = None) -> RawRun:
    """Run one fixture in its own process group with a bounded wall clock."""
    budget = _effective_timeout(timeout_s, deadline)
    if budget <= 0:
        return RawRun("timeout", -1, "", "run deadline expired before the fixture started", 0.0)
    try:
        capture = run_bounded(
            [str(build.exe), *fixture.driver_argv()],
            cwd=cwd or build.build_dir,
            env=None,
            timeout=budget,
            capture_dir=build.build_dir,
            tag=f"fixture-{fixture.fixture_id}",
        )
    except OSError as exc:  # pragma: no cover - defensive
        return RawRun("crash", -1, "", str(exc), 0.0)
    if capture.timed_out:
        return RawRun("timeout", capture.returncode, capture.stdout, capture.stderr, capture.duration_s)
    if capture.truncated:
        note = "candidate output exceeded the capture cap"
        return RawRun("incomplete", capture.returncode, capture.stdout, note, capture.duration_s)
    if capture.returncode != 0:
        return RawRun("crash", capture.returncode, capture.stdout, capture.stderr, capture.duration_s)
    return RawRun("normal", capture.returncode, capture.stdout, capture.stderr, capture.duration_s)


def parse_observation(stdout: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse the driver's line protocol into an observation envelope.

    The parser is strict: RET, MEM and END must each appear exactly once, the END
    count must be a non-negative integer that equals the number of EFF records,
    and no output may follow the terminator. A candidate that prints its own
    protocol lines therefore yields duplicates, and is reported as incomplete,
    rather than being able to spoof the observation or the completeness marker.
    """
    problems: list[str] = []
    return_hex: str | None = None
    post_memory: str | None = None
    effects: list[dict[str, int]] = []
    terminated = False
    declared_count: int | None = None

    for line in stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        if terminated:
            problems.append(f"output after the END terminator: {line!r}")
            continue
        tag = parts[0]
        if tag == "RET" and len(parts) == 2:
            if return_hex is not None:
                problems.append("duplicate RET line")
            return_hex = parts[1]
        elif tag == "MEM" and len(parts) == 2:
            if post_memory is not None:
                problems.append("duplicate MEM line")
            post_memory = parts[1]
        elif tag == "EFF" and len(parts) == 3:
            try:
                effects.append({"field": int(parts[1]), "value": int(parts[2])})
            except ValueError:
                problems.append(f"malformed effect line: {line!r}")
        elif tag == "END":
            terminated = True
            if len(parts) != 2:
                problems.append(f"malformed END line: {line!r}")
                continue
            try:
                value = int(parts[1])
            except ValueError:
                problems.append(f"malformed END count: {parts[1]!r}")
                continue
            if value < 0:
                problems.append(f"negative END count: {value}")
                continue
            declared_count = value
        else:
            problems.append(f"unexpected driver line: {line!r}")

    if not terminated:
        problems.append("driver did not emit the END terminator")
    elif declared_count is not None and declared_count != len(effects):
        problems.append(
            f"END count {declared_count} does not match {len(effects)} effect records"
        )

    if return_hex is None or post_memory is None:
        return None, problems

    if len(post_memory) != STATE_BYTES * 2:
        problems.append(f"post-state is {len(post_memory)} hex chars, expected {STATE_BYTES * 2}")

    observation = {
        "capture": {"complete": True, "lost_events": 0, "unknown_dependencies": []},
        "return_hex": return_hex,
        "exit": "normal",
        "post_memory": {"state": post_memory},
        "effects": effects,
    }
    return observation, problems


def observe(build: Build, fixture: Fixture, *, timeout_s: float = DEFAULT_TIMEOUT_S,
            cwd: Path | None = None, deadline: float | None = None
            ) -> tuple[RawRun, dict[str, Any] | None, list[str]]:
    """Run a fixture and parse its observation, refining the exit classification."""
    raw = run_fixture(build, fixture, timeout_s=timeout_s, cwd=cwd, deadline=deadline)
    if raw.exit_kind != "normal":
        return raw, None, []
    observation, problems = parse_observation(raw.stdout)
    if observation is None or problems:
        raw = RawRun("incomplete", raw.returncode, raw.stdout, raw.stderr, raw.duration_s)
        return raw, observation, problems
    return raw, observation, []


def evaluate(build: Build, fixture: Fixture, *, run_id: str,
             timeout_s: float = DEFAULT_TIMEOUT_S, deadline: float | None = None) -> dict[str, Any]:
    """Run one fixture and produce a completeness-checked diagnostic."""
    raw, observation, problems = observe(build, fixture, timeout_s=timeout_s, deadline=deadline)
    note = "; ".join(problems[:3]) if problems else None
    return compare.compare(
        fixture.expected,
        observation,
        function_id=fixture.function_id,
        fixture_id=fixture.fixture_id,
        run_id=run_id,
        exit_kind=raw.exit_kind,
        note=note,
    )
