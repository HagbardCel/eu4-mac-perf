"""Agent adapters and the process supervisor that bounds their lifecycle.

An adapter performs one *attempt* at a time. The controller owns attempts,
budget and the wall clock; the process supervisor owns the process tree, so a
stalled or crashed agent cannot leave a compiler, test binary or MCP server
running. Every child is started in its own session (process group) and the whole
group is terminated and reaped on timeout or abort.

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

from oracle.slice import harness
from oracle.slice.bridge import ToolError

KILL_GRACE_S = harness.KILL_GRACE_S


class DeadlineExceeded(RuntimeError):
    """The run's wall-clock deadline passed."""


class BudgetStop(RuntimeError):
    """The run's tool-call budget is exhausted; the controller should stop."""


@dataclass(frozen=True)
class GroupRun:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def crashed(self) -> bool:
        return self.returncode < 0


class ProcessSupervisor:
    """Tracks and terminates every child process group of a run."""

    def __init__(self, *, deadline: float, log_dir: Path):
        self.deadline = float(deadline)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._procs: list[subprocess.Popen] = []

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
        """Run a command to completion or until the run deadline, then reap it."""
        proc = self.spawn(argv, cwd=cwd, env=env)
        budget = self.remaining()
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=budget if budget > 0 else None)
        except subprocess.TimeoutExpired:
            timed_out = True
            harness.terminate_process_group(proc)
            try:
                stdout, stderr = proc.communicate(timeout=KILL_GRACE_S)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                stdout, stderr = "", ""
        harness.close_pipes(proc)
        return GroupRun(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout or "",
            stderr=stderr or "",
            timed_out=timed_out,
        )

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
