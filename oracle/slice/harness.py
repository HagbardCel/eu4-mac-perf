"""Trusted build and execution harness for the Oracle P3 synthetic slice.

The harness - never the candidate - constructs the actual observation. For each
build it assembles a private directory containing the trusted driver and header
plus the candidate source, so a candidate cannot redefine the ABI or the
observation protocol. Each fixture runs in its own process group with a bounded
wall clock; on timeout the whole group is terminated and reaped.

The line protocol emitted by ``driver.c`` is parsed here into an observation
envelope. Missing or malformed output is reported as an *incomplete* run, never
as equality.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
KILL_GRACE_S = 0.5


class HarnessError(RuntimeError):
    """Trusted-harness fault: not attributable to the candidate."""


class BuildError(HarnessError):
    """The compiler rejected the candidate (or link failed)."""

    def __init__(self, message: str, stderr: str):
        super().__init__(message)
        self.stderr = stderr


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


def compiler_version(cc: str = "cc") -> str:
    try:
        completed = subprocess.run([cc, "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HarnessError(f"cannot run compiler {cc!r}: {exc}") from exc
    first = (completed.stdout or completed.stderr).strip().splitlines()
    return first[0].strip() if first else "unknown"


def build(candidate: Path, build_dir: Path, *, cc: str = "cc") -> Build:
    """Compile the trusted driver together with ``candidate`` in ``build_dir``."""
    candidate = Path(candidate)
    if not candidate.is_file():
        raise BuildError(f"candidate source is not a regular file: {candidate}", "")
    if candidate.is_symlink():
        raise BuildError(f"candidate source must not be a symlink: {candidate}", "")

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
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HarnessError(f"compiler invocation failed: {exc}") from exc
    if completed.returncode != 0 or not exe.exists():
        raise BuildError("candidate failed to compile", completed.stderr)
    return Build(exe=exe, build_dir=build_dir, cc=cc, flags=COMPILE_FLAGS,
                 compiler_version=compiler_version(cc))


def _terminate_group(proc: subprocess.Popen) -> None:
    """Terminate a process group, then reap it, escalating to SIGKILL."""
    if proc.poll() is not None:
        proc.wait()
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=KILL_GRACE_S)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=KILL_GRACE_S)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        pass


def run_fixture(build: Build, fixture: Fixture, *, timeout_s: float = DEFAULT_TIMEOUT_S,
                cwd: Path | None = None) -> RawRun:
    """Run one fixture in its own process group with a bounded wall clock."""
    argv = [str(build.exe), *fixture.driver_argv()]
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd or build.build_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _terminate_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=KILL_GRACE_S)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            stdout, stderr = "", ""
        return RawRun("timeout", proc.returncode if proc.returncode is not None else -1,
                      stdout or "", stderr or "", time.monotonic() - started)
    duration = time.monotonic() - started
    code = proc.returncode if proc.returncode is not None else -1
    if code != 0:
        return RawRun("crash", code, stdout or "", stderr or "", duration)
    return RawRun("normal", code, stdout or "", stderr or "", duration)


def parse_observation(stdout: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse the driver's line protocol into an observation envelope.

    The parser is strict: each of RET, MEM and END must appear exactly once. A
    candidate that prints its own protocol lines therefore yields duplicates and
    is reported as incomplete, rather than being able to spoof the observation.
    """
    problems: list[str] = []
    return_hex: str | None = None
    post_memory: str | None = None
    effects: list[dict[str, int]] = []
    terminated = False

    for line in stdout.splitlines():
        parts = line.split()
        if not parts:
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
        elif tag == "END" and len(parts) == 2:
            if terminated:
                problems.append("duplicate END line")
            terminated = True
        else:
            problems.append(f"unexpected driver line: {line!r}")

    if not terminated:
        problems.append("driver did not emit the END terminator")

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
            cwd: Path | None = None) -> tuple[RawRun, dict[str, Any] | None, list[str]]:
    """Run a fixture and parse its observation, refining the exit classification."""
    raw = run_fixture(build, fixture, timeout_s=timeout_s, cwd=cwd)
    if raw.exit_kind != "normal":
        return raw, None, []
    observation, problems = parse_observation(raw.stdout)
    if observation is None or problems:
        raw = RawRun("incomplete", raw.returncode, raw.stdout, raw.stderr, raw.duration_s)
        return raw, observation, problems
    return raw, observation, []


def evaluate(build: Build, fixture: Fixture, *, run_id: str,
             timeout_s: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Run one fixture and produce a completeness-checked diagnostic."""
    raw, observation, problems = observe(build, fixture, timeout_s=timeout_s)
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
