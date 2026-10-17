"""Agent adapters and the process supervisor that bounds their lifecycle.

An adapter performs one *attempt* at a time. The controller owns attempts,
budget and the wall clock; the process supervisor owns the process tree, so a
stalled or crashed agent cannot leave a compiler, test binary or MCP server
running. Every child is started in its own session (process group) and the whole
group is terminated and reaped - after every exit, timed out or not.

Two adapters ship in PR-A:

* :class:`StubAdapter` - a deterministic, in-process stand-in for the agent. It
  drives the same five Oracle tools and applies a scripted repair. It exists to
  prove the infrastructure; PR-B proves a real OpenCode model can do the work.
* :class:`CommandAdapter` - launches an external command in a process group.
  PR-B binds it to OpenCode; the P3-A negative tests use it to prove that a
  timed-out process with a live descendant is fully terminated.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from oracle.slice import harness, receipts
from oracle.slice.bridge import ToolError

KILL_GRACE_S = harness.KILL_GRACE_S


class DeadlineExceeded(RuntimeError):
    """The run's wall-clock deadline passed."""


class BudgetStop(RuntimeError):
    """The run's tool-call budget is exhausted; the controller should stop."""


class BindingError(RuntimeError):
    """An adapter could not supply complete, trustworthy binding metadata."""


@dataclass(frozen=True)
class GroupRun:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    #: Whether retention (not production) truncated either captured stream.
    truncated: bool = False
    #: The bounded capture files. ``stdout``/``stderr`` retain at most
    #: ``harness.MAX_CAPTURE_BYTES``; evidence that must be *complete* reads these
    #: files, which the OS caps at ``harness.MAX_OUTPUT_BYTES``.
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0

    @property
    def crashed(self) -> bool:
        return self.returncode < 0

    def stream_digest(self) -> str | None:
        """SHA-256 over the *complete* bounded stdout capture, or ``None``.

        This is the digest a receipt binds; it is computed from the capture file
        rather than the retained ``stdout`` text, so it covers bytes that were
        dropped only from retention (not from the stream).
        """
        if self.stdout_path is None or not self.stdout_path.is_file():
            return None
        return receipts.file_sha256(self.stdout_path)


class ProcessSupervisor:
    """Tracks and terminates every child process group of a run."""

    def __init__(self, *, deadline: float, log_dir: Path):
        self.deadline = float(deadline)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._procs: list[subprocess.Popen] = []
        self._group_count = 0

    def expired(self) -> bool:
        return time.monotonic() >= self.deadline

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def spawn(self, argv: list[str], *, cwd: Path | None = None,
              env: dict[str, str] | None = None) -> subprocess.Popen:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self._procs.append(proc)
        return proc

    def run_group(self, argv: list[str], *, cwd: Path | None = None,
                  env: dict[str, str] | None = None) -> GroupRun:
        """Run a command to completion or until the run deadline, then reap it.

        If the deadline has already expired the command is *not* started: there
        is no borrowed time to spend. Output is captured under the harness's
        OS-enforced size limit, so a noisy agent cannot exhaust memory. The
        whole process group is cleaned up after *every* exit - a successful exit
        by the leader does not exempt its descendants. This is why the
        supervisor never has to learn about a group to be able to kill it.
        """
        budget = self.remaining()
        if budget <= 0:
            return GroupRun(returncode=-1, stdout="",
                            stderr="run deadline expired before the process started",
                            timed_out=True)
        self._group_count += 1
        try:
            capture = harness.run_bounded(argv, cwd=cwd or self.log_dir, env=env, timeout=budget,
                                          capture_dir=self.log_dir,
                                          tag=f"group-{self._group_count:03d}")
        except OSError as exc:  # pragma: no cover - defensive
            return GroupRun(returncode=-1, stdout="", stderr=str(exc), timed_out=False)
        stderr = capture.stderr
        if capture.truncated:
            stderr = (stderr + "\n[captured output exceeded the size cap]").strip()
        return GroupRun(returncode=capture.returncode, stdout=capture.stdout,
                        stderr=stderr, timed_out=capture.timed_out,
                        truncated=capture.truncated,
                        stdout_path=capture.stdout_path, stderr_path=capture.stderr_path,
                        stdout_bytes=capture.stdout_bytes, stderr_bytes=capture.stderr_bytes)

    def terminate_all(self) -> list[int]:
        """Terminate and reap every remaining child process group."""
        killed: list[int] = []
        for proc in reversed(self._procs):
            if proc.poll() is None:
                killed.append(proc.pid)
            harness.terminate_process_group(proc)
            harness.close_pipes(proc)
        self._procs.clear()
        return killed


class ToolClient:
    """The agent's view of the Oracle tools, bounded by the run deadline."""

    def __init__(self, bridge: Any, *, deadline: float):
        self._bridge = bridge
        self._deadline = float(deadline)
        self.calls = 0

    def call(self, method: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        if time.monotonic() >= self._deadline:
            raise DeadlineExceeded("run deadline reached before tool call")
        self.calls += 1
        try:
            return self._bridge.call(method, args or {})
        except ToolError as exc:
            message = str(exc)
            if message.startswith("tool-call budget exhausted") or message.startswith("tool calls are denied"):
                raise BudgetStop(message) from exc
            raise


@dataclass
class AttemptContext:
    attempt: int
    workspace: Path
    candidate_path: Path
    tools: Any
    deadline: float
    supervisor: ProcessSupervisor


@dataclass(frozen=True)
class AttemptResult:
    status: str  # progress | done | gave_up | crashed | timeout
    note: str | None = None


class AgentAdapter(Protocol):
    name: str

    def step(self, ctx: AttemptContext) -> AttemptResult:  # pragma: no cover - protocol
        ...

    def receipt_metadata(self) -> dict[str, Any] | None:  # pragma: no cover - protocol
        """Trusted binding metadata to fold into the receipt before it is sealed.

        The **controller** calls this after the agent's process groups are reaped
        and before ``receipts.seal()``; the model never supplies it. An adapter
        that binds an external toolchain returns the pinned executable identity,
        model and variant, configuration identity, prompt identity and a digest of
        its finalized tool-event evidence. An adapter with no such identity (the
        deterministic stub, the generic command adapter) returns ``None``.

        Raising :class:`BindingError` - or returning a malformed object - is a
        trusted-host fault: the controller records it as a finalization error and
        the run is not acceptable.
        """
        ...


# --- deterministic stub -------------------------------------------------------

_MODE0_ANCHOR = "case 0:\n        s->dirty = 1;\n        synth_emit(SYNTH_FIELD_DIRTY, 1);\n"
_MODE0_REPAIR = "        s->value = arg;\n        synth_emit(SYNTH_FIELD_VALUE, arg);\n"
_MODE1_ANCHOR = "    case 1:\n        s->counter += arg;\n"
_MODE1_OVERFIT = (
    "    case 1:\n"
    "        { long long c = (long long)s->counter + (long long)arg;\n"
    "          if (c > 2147483647LL) c = 2147483647LL;\n"
    "          if (c < -2147483648LL) c = -2147483648LL;\n"
    "          s->counter = (int)c; }\n"
)


class StubAdapter:
    """A deterministic stand-in that drives the real five Oracle tools.

    ``mode`` selects the scenario:

    * ``repair``    - inspect, test, diff, patch mode 0, re-test, finish.
    * ``overfit``   - repair mode 0 but clamp mode 1 (passes visible, fails held-out).
    * ``give_up``   - observe the mismatch and never repair; controller stops it.
    """

    name = "stub"

    def __init__(self, mode: str = "repair"):
        if mode not in {"repair", "overfit", "give_up"}:
            raise ValueError(f"unknown stub mode: {mode}")
        self.mode = mode
        self._fixed = False

    def _patch(self, ctx: AttemptContext) -> None:
        text = ctx.candidate_path.read_text(encoding="utf-8")
        if _MODE0_ANCHOR not in text:
            raise DeadlineExceeded("stub could not locate the mode 0 anchor")
        text = text.replace(_MODE0_ANCHOR, _MODE0_ANCHOR + _MODE0_REPAIR, 1)
        if self.mode == "overfit":
            if _MODE1_ANCHOR not in text:
                raise DeadlineExceeded("stub could not locate the mode 1 anchor")
            text = text.replace(_MODE1_ANCHOR, _MODE1_OVERFIT, 1)
        ctx.candidate_path.write_text(text, encoding="utf-8")

    def step(self, ctx: AttemptContext) -> AttemptResult:
        ctx.tools.call("inspect", {"function_id": "synth:apply"})
        ctx.tools.call("fixtures", {"function_id": "synth:apply", "limit": 20})
        ctx.tools.call("coverage", {"function_id": "synth:apply"})
        result = ctx.tools.call("test", {"function_id": "synth:apply",
                                         "candidate_path": str(ctx.candidate_path)})
        if result["status"] == "PASS":
            return AttemptResult("done", "visible suite passes")
        diff = ctx.tools.call("diff", {"run_id": result["test_run_id"], "max_events": 4})
        if self.mode == "give_up":
            return AttemptResult("gave_up", f"declined to repair: {diff.get('first_divergence')}")
        if not self._fixed:
            self._patch(ctx)
            self._fixed = True
            return AttemptResult("progress", f"patched mode 0 after {diff.get('first_divergence')}")
        return AttemptResult("gave_up", "visible suite still failing after repair")


class CommandAdapter:
    """Launches an external command as one attempt, in a supervised process group."""

    name = "command"

    def __init__(self, argv: list[str], *, cwd: Path | None = None,
                 env: dict[str, str] | None = None, done_codes: tuple[int, ...] = (0,)):
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        self.done_codes = done_codes

    def step(self, ctx: AttemptContext) -> AttemptResult:
        run = ctx.supervisor.run_group(self.argv, cwd=self.cwd, env=self.env)
        if run.timed_out:
            return AttemptResult("timeout", "process group exceeded the wall-clock deadline")
        if run.returncode in self.done_codes:
            return AttemptResult("done", "external command finished")
        if run.crashed:
            return AttemptResult("crashed", f"external command signalled ({run.returncode})")
        return AttemptResult("gave_up", f"external command exit {run.returncode}")
