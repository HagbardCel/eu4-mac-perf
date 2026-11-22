#!/usr/bin/env python3
"""Campaign safety enforcement: execution modes, one-use live-launch authorization, stop state.

This module used to enforce fixed budgets (replays, corrections, sealed frames,
shadow executions, wall clock) plus an identical-failure block.  Those quotas
were arbitrary limits on useful investigation and are gone.  What remains is
only what protects a boundary:

* **execution mode** -- ``autonomous_reconstruction`` (the default) refuses live
  capture entirely; ``controlled_capture`` permits the capture workflow.  The
  mode is fixed at initialization and ``ensure`` refuses to change it, so a
  prepared campaign cannot be silently downgraded to a weaker policy.
* **operator-issued one-use launch authorization** -- entering capture mode and
  authorizing one launch are separate decisions.  Every live launch needs an
  authorization issued by the operator with the campaign token; the capture tool
  never mints one.  The authorization is consumed atomically immediately before
  ``subprocess.Popen``; a crash or second contender cannot reuse it, and it is
  never refunded.
* **stop state** -- an explicit operator stop, an unsafe environment, invalid
  evidence or a missing observation ends campaign actions until a human acts.

There is no model-accessible budget reset because there is no budget.  The one
operator execution deadline is declared in the campaign sidecar and seeded into
the state at launch; ``ensure`` never rewinds it, and this module refuses
expensive actions after expiry.  Per-process timeouts live at the subprocess
boundary.

An EU4 launch authorization is one-use: ``authorize-launch`` records it,
``claim-launch`` consumes it immediately before process creation, and
``complete`` records the verified outcome.  An interrupted claim keeps its full
record: a crash cannot silently restore it.

Activation is host-wide and fail-closed.  ``EU4_CAMPAIGN_STATE`` names a state,
but the host-wide marker next to the EU4 launch lease is authoritative: an
explicit path naming a different campaign is a conflict and refuses every
decision, and a marker naming a missing or corrupt state still refuses launches.
There is no environment override for the marker path.  A completed campaign must
be deactivated explicitly:

    python3 -m automation.campaign_guard init --state <path> \\
        --mode autonomous_reconstruction
    python3 -m automation.campaign_guard authorize-launch --state <path>
    python3 -m automation.campaign_guard claim-launch --state <path> \\
        --authorization <id>
    python3 -m automation.campaign_guard complete --state <path> \\
        --authorization <id> --outcome ok
    python3 -m automation.campaign_guard status --state <path>
    python3 -m automation.campaign_guard stop --state <path> --reason blocked
    python3 -m automation.campaign_guard resume --state <path> --detail ...
    python3 -m automation.campaign_guard deactivate --state <path>
A state using the removed fixed-budget schema is refused: start a new campaign
state instead of migrating an old quota file.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from automation import launch_lease

SCHEMA = "eu4-campaign-state/v2"
LEGACY_SCHEMA = "eu4-campaign-state/v1"
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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    """One campaign's mode, launch authorizations and stop state."""

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
        if value.get("schema") == LEGACY_SCHEMA:
            raise CampaignRefusal(
                "legacy_state",
                "this state uses the removed fixed-budget schema; start a new "
                "campaign state instead of migrating an old one")
        if value.get("schema") != SCHEMA:
            raise CampaignRefusal("state_invalid", str(self.path))
        value.setdefault("launch_authorizations", {})
        value.setdefault("history", [])
        value.setdefault("stop", None)
        value.setdefault("deadline_epoch", None)
        return value

    # -- lifecycle ------------------------------------------------------
    def init(self, *, campaign_id: str = "", mode: str = MODE_AUTONOMOUS,
             worktree: str | None = None, objective_digest: str | None = None,
             admin_token: str | None = None,
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
            # Only used by an operator to authorize a live launch in
            # autonomous mode; it is never accepted from model tool arguments.
            "admin_token_sha256": (_sha256_text(admin_token)
                                   if admin_token else None),
            "launch_authorizations": {},
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
               admin_token: str | None = None,
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
                objective_digest=objective_digest, admin_token=admin_token,
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

    # -- launch authorization -------------------------------------------
    def authorize_launch(self, *, evidence=None,
                         operator_token: str | None = None) -> str:
        """Record one live-launch authorization.

        Every live launch needs an explicit operator authorization, in both
        modes.  The token is compared against the hash stored at
        initialization, which the launcher unsets before the agent session.
        Entering controlled-capture mode and authorizing one launch are
        therefore separate decisions.
        """
        with self._locked():
            state = self.state()
            self._require_active(state)
            if state.get("mode") != MODE_CONTROLLED:
                raise CampaignRefusal(
                    "live_capture_disabled",
                    "live launch authorization requires controlled_capture mode")
            expected = state.get("admin_token_sha256")
            if not expected or not operator_token \
                    or _sha256_text(operator_token) != expected:
                raise CampaignRefusal(
                    "operator_authorization_required",
                    "a live EU4 launch needs an operator-issued authorization; "
                    "the campaign cannot mint one itself")
            identifier = (f"auth-{time.time_ns()}-{os.getpid()}-"
                          f"{uuid.uuid4().hex[:8]}")
            now = time.time()
            state["launch_authorizations"][identifier] = {
                "state": "authorized",
                "created_at": now,
                "campaign_id": state.get("campaign_id"),
                "evidence": evidence,
                "launch_claim": None,
            }
            state["history"].append({
                "action": "authorize_launch",
                "outcome": "authorized",
                "at": now,
                "authorization": identifier,
                "detail": "one-use EU4 launch authorization",
                "evidence": evidence,
            })
            del state["history"][:-MAX_HISTORY]
            self._write(state)
        return identifier

    def claim_launch(self, authorization_id: str, *, evidence=None,
                     now: float | None = None) -> dict:
        """Atomically consume one authorization's EU4 launch entitlement.

        This is the one-use claim at the process-creation boundary.  The claim is
        persisted under the state lock *before* the caller may call
        ``subprocess.Popen``, so two contenders cannot both be authorized.
        """
        now = time.time() if now is None else now
        with self._locked():
            state = self.state()
            self._require_active(state)
            if state.get("mode") != MODE_CONTROLLED:
                raise CampaignRefusal(
                    "live_capture_disabled",
                    "a live launch is only possible in controlled_capture mode")
            if not authorization_id:
                raise CampaignRefusal(
                    "launch_authorization",
                    "a live launch requires a launch authorization")
            record = (state.get("launch_authorizations") or {}).get(
                str(authorization_id))
            if not isinstance(record, dict):
                raise CampaignRefusal("authorization_missing",
                                      str(authorization_id))
            owner = record.get("campaign_id")
            if owner is not None and owner != state.get("campaign_id"):
                raise CampaignRefusal(
                    "authorization_campaign",
                    f"{authorization_id} belongs to campaign {owner!r}, not "
                    f"{state.get('campaign_id')!r}")
            if record.get("state") not in ("authorized", "claimed"):
                raise CampaignRefusal(
                    "authorization_closed",
                    f"{authorization_id} is {record.get('state')!r}")
            if record.get("launch_claim"):
                claimed = record["launch_claim"]
                raise CampaignRefusal(
                    "launch_already_claimed",
                    f"{authorization_id} already authorized a launch at "
                    f"{claimed.get('at')}")
            claim = {"action": "eu4_launches", "at": now}
            record["launch_claim"] = claim
            record["state"] = "claimed"
            state["history"].append({
                "action": "claim_launch",
                "outcome": "claimed",
                "at": now,
                "authorization": str(authorization_id),
                "detail": "one-use eu4_launches entitlement consumed",
                "evidence": evidence,
            })
            del state["history"][:-MAX_HISTORY]
            self._write(state)
        return dict(claim)

    def complete(self, authorization_id: str, *, outcome: str = "ok",
                 detail: str = "", evidence=None,
                 now: float | None = None) -> dict:
        """Record the verified outcome of one launch authorization.

        A failed spawn keeps the consumed authorization: there is no refund,
        because the process may have existed.
        """
        now = time.time() if now is None else now
        if outcome not in ("ok", "failed"):
            raise CampaignRefusal("invalid_outcome", outcome)
        with self._locked():
            state = self.state()
            record = (state.get("launch_authorizations") or {}).get(
                authorization_id)
            if record is None:
                raise CampaignRefusal("authorization_missing",
                                      authorization_id)
            if record.get("state") in ("completed", "failed", "abandoned"):
                raise CampaignRefusal("authorization_closed",
                                      f"{authorization_id} is "
                                      f"{record.get('state')!r}")
            record["state"] = ("completed" if outcome == "ok" else "failed")
            record["completed_at"] = now
            record["detail"] = (detail or "")[:500]
            record["completion_evidence"] = evidence
            state["history"].append({
                "action": "complete",
                "outcome": outcome,
                "at": now,
                "authorization": authorization_id,
                "detail": (detail or "")[:500],
                "evidence": evidence,
            })
            del state["history"][:-MAX_HISTORY]
            self._write(state)
        return state

    def validate_authorization(self, authorization_id: str) -> bool:
        """True when the identifier is an open authorization for a launch.

        Read-only preflight only.  A launch must additionally call
        :meth:`claim_launch`; this check never consumes the authorization.
        """
        if not authorization_id:
            return False
        try:
            state = self.state()
        except CampaignRefusal:
            return False
        record = (state.get("launch_authorizations") or {}).get(
            str(authorization_id))
        return isinstance(record, dict) and record.get("state") in (
            "authorized", "claimed")

    def abandon_stale(self, *, max_age_seconds: float,
                      now: float | None = None) -> dict:
        """Mark abandoned authorizations without restoring them.

        An authorization older than ``max_age_seconds`` whose process never
        reported an outcome is marked ``abandoned``; it is never reusable.
        """
        now = time.time() if now is None else now
        with self._locked():
            state = self.state()
            changed = 0
            for identifier, record in (
                    state.get("launch_authorizations") or {}).items():
                if record.get("state") not in ("authorized", "claimed"):
                    continue
                created = float(record.get("created_at") or now)
                if now - created < float(max_age_seconds):
                    continue
                record["state"] = "abandoned"
                record["abandoned_at"] = now
                changed += 1
                state["history"].append({
                    "action": "abandon",
                    "outcome": "abandoned",
                    "at": now,
                    "authorization": identifier,
                    "detail": "authorization exceeded its maximum age; it is "
                              "not reusable",
                    "evidence": None,
                })
            if changed:
                del state["history"][:-MAX_HISTORY]
                self._write(state)
        return state

    # -- stop -----------------------------------------------------------
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
        counts: dict[str, int] = {}
        open_rows = []
        for identifier, record in (
                state.get("launch_authorizations") or {}).items():
            name = str(record.get("state"))
            counts[name] = counts.get(name, 0) + 1
            if name in ("authorized", "claimed"):
                open_rows.append({
                    "authorization": identifier,
                    "state": name,
                    "created_at": record.get("created_at"),
                    "launch_claimed": bool(record.get("launch_claim")),
                })
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
            "authorization_counts": counts,
            "open_authorizations": open_rows,
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


def refuse_unmanaged_launch(operation: str) -> None:
    """Refuse a launch from a tool that is not the campaign capture path.

    The legacy benchmark drivers acquire the host lease themselves and never
    claim a campaign authorization, so they must not act while a campaign is
    active: otherwise a campaign could escape to live capture by invoking a
    lower-level command instead of ``automation.m6_frame capture``.
    """
    if from_env() is not None:
        raise CampaignRefusal(
            "launch_authorization",
            f"{operation} is not the campaign capture path; use "
            "automation.m6_frame capture so the launch is authorized")


def require_active_from_env() -> Guard | None:
    """Refuse an expensive action when the active campaign is stopped.

    There is no quota to check: this only enforces a stop, so ordinary
    investigation is never blocked by a remaining-allowance counter.
    """
    guard = from_env()
    if guard is not None:
        guard.require()
    return guard


def require_launch_authorization(authorization: str | None = None,
                                 guard: "Guard | None" = None) -> None:
    """Refuse an EU4 spawn that does not hold an open launch authorization.

    Both launch choke points call this read-only preflight, so an alternative
    tool cannot spend an unauthorized launch.  The one-use entitlement is
    consumed separately by :func:`claim_launch_authorization` immediately before
    process creation.
    """
    guard = guard if guard is not None else from_env()
    if guard is None:
        return
    # Refuse a stopped/expired/conflicting campaign before any caller spends
    # preflight or build work, not only at the final atomic claim.
    guard.require()
    state = guard.state()
    if state.get("mode") != MODE_CONTROLLED:
        raise CampaignRefusal(
            "live_capture_disabled",
            "a live launch is only possible in controlled_capture mode")
    if not guard.validate_authorization(_launch_token(authorization)):
        raise CampaignRefusal(
            "launch_authorization",
            "this campaign requires an open EU4 launch authorization; start the "
            "capture through automation.m6_frame capture")


def claim_launch_authorization(authorization: str | None = None,
                               guard: "Guard | None" = None,
                               evidence=None) -> dict | None:
    """Atomically claim the one-use EU4 launch entitlement, just before spawn.

    Returns ``None`` when no campaign is active.  Otherwise the claim is
    persisted before the caller is allowed to create the process; refusal means
    no process may be created.
    """
    guard = guard if guard is not None else from_env()
    if guard is None:
        return None
    return guard.claim_launch(_launch_token(authorization), evidence=evidence)


def _launch_token(authorization: str | None) -> str:
    if authorization is not None:
        return authorization
    return (os.environ.get("EU4_CAMPAIGN_AUTHORIZATION")
            or os.environ.get("EU4_CAMPAIGN_RESERVATION") or "").strip()


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
    command = common("authorize-launch")
    command.add_argument("--evidence", default=None)
    command = common("claim-launch")
    command.add_argument("--authorization", required=True)
    command.add_argument("--evidence", default=None)
    command = common("complete")
    command.add_argument("--authorization", required=True)
    command.add_argument("--outcome", default="ok", choices=("ok", "failed"))
    command.add_argument("--detail", default="")
    command = common("status")
    stop = common("stop")
    stop.add_argument("--reason", required=True, choices=STOP_REASONS)
    stop.add_argument("--detail", default="")
    command = common("abandon-stale")
    command.add_argument("--max-age-seconds", type=float, required=True)
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
                admin_token=os.environ.get("EU4_CAMPAIGN_ADMIN_TOKEN") or None,
                deadline_seconds=args.deadline_seconds)
        elif args.operation == "ensure":
            value = guard.ensure(
                campaign_id=args.campaign_id, mode=args.mode,
                worktree=args.worktree,
                objective_digest=args.objective_digest,
                admin_token=os.environ.get("EU4_CAMPAIGN_ADMIN_TOKEN") or None,
                deadline_seconds=args.deadline_seconds)
        elif args.operation == "authorize-launch":
            value = {"ok": True, "authorization": guard.authorize_launch(
                evidence=args.evidence,
                operator_token=os.environ.get("EU4_CAMPAIGN_ADMIN_TOKEN")
                or None)}
        elif args.operation == "claim-launch":
            value = guard.claim_launch(args.authorization,
                                       evidence=args.evidence)
        elif args.operation == "complete":
            value = guard.complete(args.authorization, outcome=args.outcome,
                                   detail=args.detail)
        elif args.operation == "status":
            value = guard.status()
        elif args.operation == "abandon-stale":
            guard.abandon_stale(max_age_seconds=args.max_age_seconds)
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
