"""Event-driven waiting for EU4 diagnostics.

Long fixed sleeps and ``--min-uptime-ms`` are not evidence: a loading scene can
be busy without presenting, and a gray window can be a valid in-progress frame.
This module waits on observable events instead:

* a requested checkpoint / frame / resource was captured;
* a relevant GPU or API error was recorded;
* the process exited or crashed;
* a genuine engine/GPU progress indicator advanced;
* a verified stall (progress was observed, then stopped while the process is
  still alive);
* a stage-aware deadline was exceeded.

The caller supplies conditions and a progress function; the clock and sleep are
injectable so the semantics are testable without real time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

MET = "met"
ABORTED = "aborted"
EXITED = "exited"
STALL = "stall"
DEADLINE = "deadline"
ERROR = "error"
STATUSES = (MET, ABORTED, EXITED, STALL, DEADLINE, ERROR)

PROFILE_EARLY = "early"
PROFILE_TARGETED = "targeted"
PROFILE_QUALIFICATION = "qualification"


@dataclass(frozen=True)
class WaitProfile:
    """Bounded, stage-aware wait policy for one logical profile."""

    name: str
    base_seconds: float
    hard_seconds: float
    idle_seconds: float
    poll_seconds: float = 0.1
    stall_requires_prior_progress: bool = True

    def __post_init__(self) -> None:
        for field_name in ("base_seconds", "hard_seconds", "idle_seconds", "poll_seconds"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{field_name} must be a finite positive number")
        if self.base_seconds > self.hard_seconds:
            raise ValueError("base_seconds must not exceed hard_seconds")
        if self.poll_seconds > self.idle_seconds:
            raise ValueError("poll_seconds must not exceed idle_seconds")

    def stage(self, *, base_seconds: float, hard_seconds: float,
              idle_seconds: float) -> "WaitProfile":
        return WaitProfile(
            self.name,
            base_seconds=base_seconds,
            hard_seconds=hard_seconds,
            idle_seconds=idle_seconds,
            poll_seconds=self.poll_seconds,
            stall_requires_prior_progress=self.stall_requires_prior_progress,
        )


PROFILES = {
    # Startup defects are expected within seconds; keep the window short.
    PROFILE_EARLY: WaitProfile(PROFILE_EARLY, base_seconds=45.0, hard_seconds=90.0,
                               idle_seconds=15.0),
    # One selected scene/frame/draw/resource.
    PROFILE_TARGETED: WaitProfile(PROFILE_TARGETED, base_seconds=120.0,
                                  hard_seconds=300.0, idle_seconds=25.0),
    # Complete authentic verification.  Still event-driven, never a fixed sleep.
    PROFILE_QUALIFICATION: WaitProfile(PROFILE_QUALIFICATION, base_seconds=300.0,
                                       hard_seconds=900.0, idle_seconds=60.0),
}


def profile(name: str) -> WaitProfile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown wait profile: {name!r}") from exc


@dataclass(frozen=True)
class Progress:
    """One genuine progress observation (not a screenshot heuristic)."""

    stage: str
    label: str
    at: float
    counter: float | None = None


@dataclass
class WaitResult:
    status: str
    matched: str | None = None
    reason: str = ""
    elapsed_seconds: float = 0.0
    profile: str = ""
    stage: str = ""
    progress_events: int = 0
    last_progress: Progress | None = None
    polls: int = 0

    @property
    def ok(self) -> bool:
        return self.status == MET

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "matched": self.matched,
            "reason": self.reason,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "profile": self.profile,
            "stage": self.stage,
            "progress_events": self.progress_events,
            "last_progress": None if self.last_progress is None else {
                "stage": self.last_progress.stage,
                "label": self.last_progress.label,
                "counter": self.last_progress.counter,
            },
            "polls": self.polls,
        }


def _check(mapping: dict[str, Callable[[], bool]] | None,
           *, kind: str, result: WaitResult, exceptions: list[str]) -> str | None:
    if not mapping:
        return None
    for name in sorted(mapping):
        try:
            if mapping[name]():
                return name
        except Exception as exc:  # noqa: BLE001 - a broken probe is an error, not a pass
            exceptions.append(f"{kind}:{name}:{type(exc).__name__}:{exc}")
    return None


def wait_for(
    conditions: dict[str, Callable[[], bool]],
    *,
    wait_profile: WaitProfile,
    progress: Callable[[], Progress | None] | None = None,
    aborts: dict[str, Callable[[], bool]] | None = None,
    is_alive: Callable[[], bool] | None = None,
    on_progress: Callable[[Progress], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    stage: str = "",
) -> WaitResult:
    """Wait until one condition is met, or a meaningful terminal event occurs.

    ``conditions`` are success events, checked in sorted name order.  ``aborts``
    are failure/terminal events checked before success.  ``progress`` is polled
    every iteration; an advance extends the deadline up to ``hard_seconds``.
    A stall is only declared after real progress was observed and then stopped,
    and only while ``is_alive`` still reports the owned process as alive.
    """
    if not conditions:
        raise ValueError("at least one wait condition is required")
    if not isinstance(wait_profile, WaitProfile):
        raise TypeError("wait_profile must be a WaitProfile")
    started = clock()
    base_deadline = started + wait_profile.base_seconds
    hard_deadline = started + wait_profile.hard_seconds
    deadline = base_deadline
    result = WaitResult(status=DEADLINE, profile=wait_profile.name, stage=stage)
    checks: list[str] = []
    last_progress: Progress | None = None
    last_progress_at = started
    observed_progress = False
    while True:
        result.polls += 1
        now = clock()
        result.elapsed_seconds = max(0.0, now - started)

        abort_name = _check(aborts, kind="abort", result=result, exceptions=checks)
        if abort_name is not None:
            result.status = ABORTED
            result.matched = abort_name
            result.reason = f"terminal event: {abort_name}"
            return result

        match = _check(conditions, kind="condition", result=result, exceptions=checks)
        if match is not None:
            result.status = MET
            result.matched = match
            result.reason = ""
            return result

        if is_alive is not None:
            try:
                alive = bool(is_alive())
            except Exception as exc:  # noqa: BLE001
                result.status = ERROR
                result.reason = f"liveness probe failed: {type(exc).__name__}: {exc}"
                return result
            if not alive:
                result.status = EXITED
                result.reason = "owned process is no longer alive"
                return result

        if progress is not None:
            try:
                current = progress()
            except Exception as exc:  # noqa: BLE001
                result.status = ERROR
                result.reason = f"progress probe failed: {type(exc).__name__}: {exc}"
                return result
            if current is not None:
                advanced = (
                    last_progress is None
                    or current.counter != last_progress.counter
                    or current.stage != last_progress.stage
                    or current.label != last_progress.label
                )
                if advanced:
                    observed_progress = True
                    result.progress_events += 1
                    last_progress = current
                    last_progress_at = now
                    result.last_progress = current
                    deadline = min(hard_deadline, max(deadline, now + wait_profile.base_seconds))
                    if on_progress is not None:
                        on_progress(current)

        if checks:
            result.status = ERROR
            result.reason = "; ".join(checks[:4])
            return result

        idle_limit = max(wait_profile.idle_seconds, wait_profile.poll_seconds)
        idle_for = now - last_progress_at
        if (
            observed_progress
            and idle_for >= idle_limit
            and (is_alive is None or _safe_alive(is_alive))
        ):
            result.status = STALL
            result.reason = (
                f"no progress for {idle_for:.1f}s at stage "
                f"{last_progress.stage if last_progress else stage!r}"
            )
            return result

        if now >= deadline:
            result.status = DEADLINE
            result.reason = (
                f"deadline after {result.elapsed_seconds:.1f}s"
                + (f" at stage {last_progress.stage!r}" if last_progress else "")
            )
            return result

        sleeper(min(wait_profile.poll_seconds, max(0.0, deadline - now)))


def _safe_alive(is_alive: Callable[[], bool]) -> bool:
    try:
        return bool(is_alive())
    except Exception:  # noqa: BLE001
        return False


@dataclass
class FileProgress:
    """Progress from append-only diagnostics or new artifact files.

    The indicator must be a genuine engine/GPU event stream: frame observation
    records, resolved draws, checkpoint writes.  A changed screenshot or window
    rectangle is deliberately not part of this helper.
    """

    root: Path
    patterns: tuple[str, ...] = ("frame-observe.jsonl", "obs/*.json", "checkpoint-*.json")
    stage: str = "rendering"
    _count: int = field(default=0, init=False)
    _mtime: float = field(default=0.0, init=False)
    _last_label: str = field(default="", init=False)

    def snapshot(self) -> Progress | None:
        count = 0
        newest = 0.0
        newest_name = ""
        for pattern in self.patterns:
            for path in self.root.glob(pattern):
                if not path.is_file():
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size <= 0:
                    continue
                count += size
                if path.stat().st_mtime > newest:
                    newest = path.stat().st_mtime
                    newest_name = path.name
        if count == 0:
            return None
        label = f"{newest_name}:{count}"
        if count == self._count and label == self._last_label:
            return self._last_progress()
        self._count = count
        self._last_label = label
        self._mtime = newest
        return self._last_progress()

    def _last_progress(self) -> Progress:
        return Progress(stage=self.stage, label=self._last_label,
                        at=time.monotonic(), counter=float(self._count))


@dataclass
class CountedProgress:
    """Progress from an explicit monotonic counter supplied by the caller."""

    counter: Callable[[], float]
    label: str = "counter"
    stage: str = "rendering"

    def snapshot(self) -> Progress | None:
        try:
            value = float(self.counter())
        except Exception:  # noqa: BLE001
            return None
        return Progress(stage=self.stage, label=self.label,
                        at=time.monotonic(), counter=value)


def first_true(mapping: dict[str, Callable[[], bool]]) -> Callable[[], bool]:
    """Combine named conditions into one predicate (useful for simple callers)."""
    if not mapping:
        raise ValueError("at least one condition is required")

    def check() -> bool:
        return any(mapping[name]() for name in sorted(mapping))

    return check


def all_true(mapping: Iterable[Callable[[], bool]]) -> Callable[[], bool]:
    checks = list(mapping)
    if not checks:
        raise ValueError("at least one condition is required")
    return lambda: all(check() for check in checks)
