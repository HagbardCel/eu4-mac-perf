#!/usr/bin/env python3
"""Track active campaign modes, deadlines and stops.

The host marker prevents conflicting campaigns from sharing the game host.
An active offline campaign refuses live launches. Development tools can
run independently when no campaign is active. Launch leases and subprocess
timeouts belong to the launchers.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import time
from pathlib import Path

from automation import launch_lease

SCHEMA = "eu4-campaign-state/v3"
REFUSAL_SCHEMA = "eu4-campaign-refusal/v1"
MARKER_SCHEMA = "eu4-campaign-active/v1"
MARKER_NAME = "active-campaign.json"

MODE_AUTONOMOUS = "autonomous_reconstruction"
MODE_CONTROLLED = "controlled_capture"
MODES = (MODE_AUTONOMOUS, MODE_CONTROLLED)

# Stop reasons are conditions a human or a trusted checker establishes.  An
# arbitrary attempt counter is deliberately not one of them.
STOP_REASONS = (
    "objective_met",
    "blocked",
    "invalid_evidence",
    "evidence_missing",
    "environment_unsafe",
    "environment_unavailable",
    "operator_stop",
)

MAX_HISTORY = 400


class CampaignRefusal(RuntimeError):
    """A campaign action was refused; ``kind`` names the condition."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail

    def as_dict(self) -> dict:
        return {"schema": REFUSAL_SCHEMA, "ok": False, "kind": self.kind,
                "detail": self.detail}


def _marker_path() -> Path:
    """Host-wide activation marker, beside the host-wide launch lease.

    There is deliberately no environment override: a production shell must not
    be able to repoint the marker and thereby disable an active campaign.
    Tests replace this function in-process.
    """
    # Fixed host-wide location.  Deliberately independent of every environment
    # override (including the launch-lease directory) so an active stop cannot
    # be hidden by changing the environment.
    return launch_lease.DEFAULT_LEASE_DIR / MARKER_NAME


def _marker_lock_path() -> Path:
    return _marker_path().with_name(_marker_path().name + ".lock")


class Guard:
    """One campaign's mode, deadline and stop state."""

    def __init__(self, state_path: Path | str):
        self.path = Path(state_path).expanduser().resolve()
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        # Set when an explicit EU4_CAMPAIGN_STATE names a different campaign
        # than the host-wide active marker.  Every decision then fails closed.
        self.conflict: str | None = None

    # -- persistence ----------------------------------------------------
    @contextlib.contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _write(self, value: dict) -> None:
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        os.replace(temporary, self.path)

    def state(self) -> dict:
        value = self._read()
        if value is None:
            raise CampaignRefusal("state_missing", str(self.path))
        if value.get("schema") != SCHEMA:
            raise CampaignRefusal("state_invalid", f"{self.path}: start a fresh campaign for this schema")
        value.setdefault("history", [])
        value.setdefault("stop", None)
        value.setdefault("deadline_epoch", None)
        return value

    # -- lifecycle ------------------------------------------------------
    def init(self, *, campaign_id: str = "", mode: str = MODE_AUTONOMOUS,
             worktree: str | None = None, objective_digest: str | None = None,
             deadline_seconds: float | None = None) -> dict:
        if mode not in MODES:
            raise CampaignRefusal("invalid_mode", mode)
        if deadline_seconds is not None and (
                isinstance(deadline_seconds, bool)
                or not isinstance(deadline_seconds, (int, float))
                or deadline_seconds <= 0):
            raise CampaignRefusal("invalid_deadline", str(deadline_seconds))
        now = time.time()
        state = {
            "schema": SCHEMA,
            "campaign_id": campaign_id or f"campaign-{int(now)}",
            "created_at": now,
            "mode": mode,
            "live_capture": mode == MODE_CONTROLLED,
            "worktree": worktree,
            "objective_digest": objective_digest,
            "auto_merge": False,
            "stop": None,
            # The operator's unattended runtime allowance.  It starts when the
            # launcher activates the campaign, not when it was prepared; a
            # resume keeps the original epoch instead of silently extending it.
            "deadline_epoch": (now + float(deadline_seconds)
                               if deadline_seconds is not None else None),
            "history": [],
        }
        with self._locked():
            if self.path.is_file():
                raise CampaignRefusal("state_exists", str(self.path))
            self._write(state)
        self.activate()
        return state

    def ensure(self, *, campaign_id: str = "", mode: str = MODE_AUTONOMOUS,
               worktree: str | None = None,
               objective_digest: str | None = None,
                 deadline_seconds: float | None = None) -> dict:
        """Create a state, or verify and resume an existing one.

        An existing state is never rewritten with a different mode: changing
        the mode of a campaign would weaken its safety policy.  Resuming keeps
        the recorded stop and the original deadline; the deadline is set here
        only when it was never set (the launcher calls this at launch time, so
        preparation time does not silently consume the allowance).
        """
        if not self.path.is_file():
            return dict(self.init(
                campaign_id=campaign_id, mode=mode, worktree=worktree,
                objective_digest=objective_digest,
                deadline_seconds=deadline_seconds),
                created=True)
        with self._locked():
            state = self.state()
            if state.get("mode") != mode:
                raise CampaignRefusal(
                    "mode_conflict",
                    f"existing campaign mode is {state.get('mode')!r}, "
                    f"requested {mode!r}; prepare a new campaign state instead "
                    "of changing the safety policy of an active one")
            if (state.get("deadline_epoch") is None
                    and deadline_seconds is not None):
                state["deadline_epoch"] = time.time() + float(deadline_seconds)
                state["history"].append({
                    "action": "deadline", "outcome": "started",
                    "at": time.time(),
                    "detail": f"unattended allowance {deadline_seconds}s "
                              "started at launch",
                    "evidence": None,
                })
                del state["history"][:-MAX_HISTORY]
                self._write(state)
        self.activate()
        return dict(state, created=False)

    def activate(self) -> dict:
        """Publish this campaign host-wide so unsetting env vars changes nothing.

        Activating a *different* campaign never silently replaces the active
        safety policy: the operator must deactivate the current campaign first.
        A marker that exists but cannot be read is also not overwritten.
        """
        state = self.state()
        path = _marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _marker_lock_path().open("a+") as marker_lock:
            fcntl.flock(marker_lock.fileno(), fcntl.LOCK_EX)
            try:
                return self._activate_locked(state, path)
            finally:
                fcntl.flock(marker_lock.fileno(), fcntl.LOCK_UN)

    def _activate_locked(self, state: dict, path: Path) -> dict:
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise CampaignRefusal(
                    "marker_invalid",
                    f"{path} exists but cannot be read ({exc}); inspect and "
                    "deactivate it before starting another campaign") from exc
            if not isinstance(existing, dict) or existing.get(
                    "schema") != MARKER_SCHEMA:
                raise CampaignRefusal(
                    "marker_invalid",
                    f"{path} is not a campaign marker; inspect and remove it "
                    "before starting a campaign")
            active = str(existing.get("state") or "")
            if active and active != str(self.path):
                raise CampaignRefusal(
                    "campaign_active",
                    f"another campaign is active ({active}); deactivate it "
                    "before starting a different campaign")
        value = {
            "schema": MARKER_SCHEMA,
            "state": str(self.path),
            "campaign_id": state.get("campaign_id"),
            "mode": state.get("mode"),
            "updated_at": time.time(),
        }
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        os.replace(temporary, path)
        return value

    def deactivate(self) -> bool:
        """Remove the host-wide marker, but only if it names this state."""
        path = _marker_path()
        with _marker_lock_path().open("a+") as marker_lock:
            fcntl.flock(marker_lock.fileno(), fcntl.LOCK_EX)
            try:
                return self._deactivate_locked(path)
            finally:
                fcntl.flock(marker_lock.fileno(), fcntl.LOCK_UN)

    def _deactivate_locked(self, path: Path) -> bool:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        if not isinstance(value, dict) or value.get("schema") != MARKER_SCHEMA:
            return False
        if str(value.get("state")) != str(self.path):
            return False
        path.unlink()
        return True

    # -- decisions ------------------------------------------------------
    def _require_active(self, state: dict) -> None:
        if self.conflict:
            raise CampaignRefusal(
                "campaign_conflict",
                "the explicit campaign state does not match the host-wide "
                f"active campaign; refusing the explicit override ({self.conflict})")
        stop = state.get("stop")
        if stop:
            raise CampaignRefusal("stopped", str(stop.get("reason")))
        deadline = state.get("deadline_epoch")
        if deadline is not None and time.time() > float(deadline):
            raise CampaignRefusal(
                "deadline",
                "the operator's unattended execution allowance has expired")

    def require(self, *, what: str = "this action") -> None:
        """Refuse when the campaign is stopped (no quotas exist to check)."""
        self._require_active(self.state())

    def stop(self, reason: str, detail: str = "") -> dict:
        if reason not in STOP_REASONS:
            raise CampaignRefusal("unknown_stop_reason", reason)
        with self._locked():
            state = self.state()
            state["stop"] = {"reason": reason, "detail": detail[:1000],
                             "at": time.time()}
            state["history"].append({
                "action": "stop", "outcome": reason, "at": time.time(),
                "detail": detail[:1000], "evidence": None})
            del state["history"][:-MAX_HISTORY]
            self._write(state)
        return state

    def resume(self, *, detail: str = "") -> dict:
        """Clear an operator stop after the condition was actually resolved.

        This is not a budget reset (there is no budget) and it never rewinds an
        expired deadline.
        """
        with self._locked():
            state = self.state()
            stopped = state.get("stop")
            if stopped:
                state["stop"] = None
                state["history"].append({
                    "action": "resume", "outcome": "active",
                    "at": time.time(),
                    "detail": (detail or "operator resumed the campaign")[:500],
                    "evidence": {"cleared_stop": stopped},
                })
                del state["history"][:-MAX_HISTORY]
                self._write(state)
        return state

    def status(self) -> dict:
        state = self.state()
        return {
            "schema": SCHEMA,
            "campaign_id": state.get("campaign_id"),
            "mode": state.get("mode"),
            "live_capture": bool(state.get("live_capture")),
            "stop": state.get("stop"),
            "deadline_epoch": state.get("deadline_epoch"),
            "seconds_remaining": (
                None if state.get("deadline_epoch") is None else
                max(0.0, float(state["deadline_epoch"]) - time.time())),
            "auto_merge": state.get("auto_merge"),
            "worktree": state.get("worktree"),
            "history_count": len(state.get("history") or []),
        }


def _marker_campaign() -> Guard | None:
    """The host-wide active campaign, published by :meth:`Guard.activate`.

    An existing marker always yields a guard, even when it names a missing or
    malformed state: the launch entry points then refuse through
    ``CampaignRefusal`` instead of treating the campaign as absent.  A marker
    that cannot be parsed at all yields a sentinel guard whose state is missing,
    which fails closed for the same reason.
    """
    path = _marker_path()
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return Guard(path)
    if not isinstance(value, dict) or value.get("schema") != MARKER_SCHEMA:
        return Guard(path)
    state = value.get("state")
    if not isinstance(state, str) or not state:
        return Guard(path)
    # Do not validate the state here.  A deleted or corrupt state must refuse
    # launches, not read as "no campaign".
    return Guard(state)


def from_env() -> Guard | None:
    """The configured campaign guard, or ``None`` when no campaign exists.

    The host-wide marker is authoritative.  An explicit ``EU4_CAMPAIGN_STATE``
    that names a different state does not override it: the returned guard
    carries a conflict and refuses every decision, so a stale environment
    variable cannot bypass the active campaign's policy.
    """
    env_path = os.environ.get("EU4_CAMPAIGN_STATE", "").strip()
    marker = _marker_campaign()
    if marker is None:
        return Guard(env_path) if env_path else None
    if env_path:
        explicit = Guard(env_path)
        if str(explicit.path) != str(marker.path):
            marker.conflict = str(explicit.path)
    return marker


def require_active_from_env() -> Guard | None:
    """Refuse an expensive action when the active campaign is stopped.

    There is no quota to check: this only enforces a stop, so ordinary
    investigation is never blocked by a remaining-allowance counter.
    """
    guard = from_env()
    if guard is not None:
        guard.require()
    return guard


def require_live_campaign(guard: Guard | None = None) -> Guard | None:
    """Apply the active campaign's mode and deadline at a live launch boundary."""
    guard = guard if guard is not None else from_env()
    if guard is not None:
        guard.require()
        if guard.state().get("mode") != MODE_CONTROLLED:
            raise CampaignRefusal("live_capture_disabled",
                                  "the active campaign is offline")
    return guard


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)

    def common(name: str) -> argparse.ArgumentParser:
        item = sub.add_parser(name)
        item.add_argument("--state", required=True, type=Path)
        return item

    init = common("init")
    init.add_argument("--campaign-id", default="")
    init.add_argument("--mode", choices=MODES, default=MODE_AUTONOMOUS)
    init.add_argument("--worktree", default=None)
    init.add_argument("--objective-digest", default=None)
    init.add_argument("--deadline-seconds", type=float, default=None,
                      help="operator unattended allowance, starting at launch")
    ensure = common("ensure")
    ensure.add_argument("--campaign-id", default="")
    ensure.add_argument("--mode", choices=MODES, default=MODE_AUTONOMOUS)
    ensure.add_argument("--worktree", default=None)
    ensure.add_argument("--objective-digest", default=None)
    ensure.add_argument("--deadline-seconds", type=float, default=None,
                        help="operator unattended allowance, starting at launch")
    command = common("status")
    stop = common("stop")
    stop.add_argument("--reason", required=True, choices=STOP_REASONS)
    stop.add_argument("--detail", default="")
    command = common("activate")
    command = common("deactivate")
    command = common("resume")
    command.add_argument("--detail", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    guard = Guard(args.state)
    try:
        if args.operation == "init":
            value = guard.init(
                campaign_id=args.campaign_id, mode=args.mode,
                worktree=args.worktree,
                objective_digest=args.objective_digest,
                deadline_seconds=args.deadline_seconds)
        elif args.operation == "ensure":
            value = guard.ensure(
                campaign_id=args.campaign_id, mode=args.mode,
                worktree=args.worktree,
                objective_digest=args.objective_digest,
                deadline_seconds=args.deadline_seconds)
        elif args.operation == "status":
            value = guard.status()
        elif args.operation == "activate":
            value = guard.activate()
        elif args.operation == "deactivate":
            value = {"ok": guard.deactivate()}
        elif args.operation == "resume":
            value = guard.resume(detail=args.detail)
        else:
            value = guard.stop(args.reason, args.detail)
    except CampaignRefusal as exc:
        print(json.dumps(exc.as_dict(), indent=2, sort_keys=True))
        return 2
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0 if value.get("ok", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
