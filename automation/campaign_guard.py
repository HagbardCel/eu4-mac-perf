#!/usr/bin/env python3
"""Externally enforced campaign budgets, reservations and stop state.

DSH owns orchestration: its native goal/round driver bounds the number of
rounds, and the goal plugin denies model-initiated completion.  DSH does not
know about EU4 launches, Metal shadow executions, candidate replays or
correction attempts, so those limits are enforced here, outside the model
prompt, at the action sites that actually consume the resource.

Accounting is reservation-first.  A resource is charged atomically under the
state lock *before* the action starts; completion records the actual verified
outcome and, when it is safe to prove the work never happened, releases the
unused part of the reservation.  An interrupted or abandoned reservation keeps
its full charge (conservative), so a crash cannot silently restore a spent
execution allowance.

An identical failure repeated ``MAX_IDENTICAL_RETRIES`` times blocks that action
for the campaign.  There is no model-accessible reset: only an administrative
recovery authorized by the operator's token can clear a stop or a block, and it
records the reason in the audit history.  A different textual "strategy" is not
accepted as a recovery.

    python3 -m automation.campaign_guard init --state <path> [--budget k=v]...
    python3 -m automation.campaign_guard reserve --state <path> \\
        --action eu4_launches=1 --action sealed_frames=1
    python3 -m automation.campaign_guard complete --state <path> \\
        --reservation <id> --outcome ok
    python3 -m automation.campaign_guard status --state <path>
    python3 -m automation.campaign_guard stop --state <path> --reason blocked

A campaign state is opt-in: when ``EU4_CAMPAIGN_STATE`` is unset, the action
sites behave exactly as before.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

SCHEMA = "eu4-campaign-state/v1"
REFUSAL_SCHEMA = "eu4-campaign-refusal/v1"

PILOT_BUDGETS = {
    "eu4_launches": 1,
    "sealed_frames": 1,
    "shadow_executions": 1,
    "replays": 2,
    "corrections": 1,
    "wall_clock_seconds": 1800,
}
MAX_IDENTICAL_RETRIES = 2
MAX_HISTORY = 400

STOP_REASONS = (
    "budget_exhausted",
    "deadline",
    "repeated_failure",
    "invalid_evidence",
    "capture_failed",
    "environment_unsafe",
    "objective_met",
    "blocked",
)


class CampaignRefusal(RuntimeError):
    """A campaign action was refused; ``kind`` names the stop condition."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail

    def as_dict(self) -> dict:
        return {"schema": REFUSAL_SCHEMA, "ok": False, "kind": self.kind,
                "detail": self.detail}


def classify_failure(detail: str) -> str:
    """Classify one operational failure without inventing a renderer cause."""
    lowered = (detail or "").lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "execution_timeout"
    if any(token in lowered for token in (
            "credential", "unauthorized", "authentication", "api key")):
        return "missing_credential"
    if any(token in lowered for token in (
            "no metal device", "metal device is unavailable", "no gpu",
            "graphics unavailable", "no display", "command buffer",
            "command_status", "readback")):
        return "gpu_execution_failure"
    if any(token in lowered for token in (
            "digest", "sha256", "identity", "provenance", "belongs to another",
            "invalid")):
        return "evidence_invalid"
    if any(token in lowered for token in ("missing", "not found", "absent")):
        return "evidence_missing"
    return "tool_failure"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _actions(value) -> dict[str, int]:
    actions: dict[str, int] = {}
    for item in value or ():
        name, _, count = str(item).partition("=")
        if not name or not _:
            raise CampaignRefusal("invalid_action", item)
        try:
            amount = int(count)
        except ValueError as exc:
            raise CampaignRefusal("invalid_action", item) from exc
        if amount < 0:
            raise CampaignRefusal("invalid_action", item)
        actions[name] = actions.get(name, 0) + amount
    return actions


class Guard:
    """One campaign's persistent reservation, budget and failure state."""

    def __init__(self, state_path: Path | str):
        self.path = Path(state_path).expanduser().resolve()
        self.lock_path = self.path.with_name(self.path.name + ".lock")

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

    def _normalize(self, state: dict) -> dict:
        state.setdefault("counts", {})
        state.setdefault("failures", {})
        state.setdefault("blocked_actions", {})
        state.setdefault("reservations", {})
        state.setdefault("history", [])
        state.setdefault("exhausted", [])
        state.setdefault("admin_token_sha256", None)
        state.setdefault("auto_merge", False)
        return state

    def state(self) -> dict:
        value = self._read()
        if value is None or value.get("schema") != SCHEMA:
            raise CampaignRefusal("state_missing", str(self.path))
        return self._normalize(value)

    # -- lifecycle ------------------------------------------------------
    def init(self, *, campaign_id: str = "", budgets=None,
             deadline_seconds: float | None = None,
             worktree: str | None = None, objective_digest: str | None = None,
             auto_merge: bool = False,
             admin_token: str | None = None) -> dict:
        now = time.time()
        values = dict(PILOT_BUDGETS)
        for key, value in (budgets or {}).items():
            if key not in values:
                raise CampaignRefusal("unknown_budget", key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) \
                    or value < 0:
                raise CampaignRefusal("invalid_budget", f"{key}={value!r}")
            values[key] = value
        seconds = (values["wall_clock_seconds"] if deadline_seconds is None
                   else float(deadline_seconds))
        if seconds <= 0:
            raise CampaignRefusal("invalid_budget", "wall_clock_seconds")
        state = {
            "schema": SCHEMA,
            "campaign_id": campaign_id or f"campaign-{int(now)}",
            "created_at": now,
            "deadline_epoch": now + seconds,
            "budgets": values,
            "counts": {key: 0 for key in values},
            "failures": {},
            "blocked_actions": {},
            "reservations": {},
            "stop": None,
            "worktree": worktree,
            "objective_digest": objective_digest,
            "auto_merge": bool(auto_merge),
            "admin_token_sha256": (_sha256_text(admin_token)
                                   if admin_token else None),
            "exhausted": [],
            "history": [],
        }
        with self._locked():
            if self.path.is_file():
                raise CampaignRefusal("state_exists", str(self.path))
            self._write(state)
        return state

    # -- decisions ------------------------------------------------------
    def _refusal(self, state: dict, needed: dict[str, int],
                 now: float) -> None:
        if state.get("stop"):
            raise CampaignRefusal("stopped",
                                  str(state["stop"].get("reason")))
        if now > float(state.get("deadline_epoch") or 0):
            raise CampaignRefusal("deadline",
                                  "the campaign wall-clock deadline has passed")
        budgets = state.get("budgets") or {}
        counts = state.get("counts") or {}
        for action, amount in needed.items():
            if action not in budgets:
                raise CampaignRefusal("unknown_action", action)
            if action == "wall_clock_seconds":
                raise CampaignRefusal("invalid_action", action)
            if amount <= 0:
                continue
            blocked = (state.get("blocked_actions") or {}).get(action)
            if blocked:
                raise CampaignRefusal("requires_admin_recovery", blocked)
            budget = budgets.get(action)
            used = int(counts.get(action, 0))
            if budget is not None and used + amount > int(budget):
                raise CampaignRefusal(
                    "budget_exhausted",
                    f"{action} needs {amount}, {used} of {budget} already "
                    "spent or reserved")

    def check(self, action: str, *, now: float | None = None) -> dict:
        state = self.state()
        now = time.time() if now is None else now
        try:
            self._refusal(state, {action: 1}, now)
        except CampaignRefusal as exc:
            return {"ok": False, "kind": exc.kind, "detail": exc.detail}
        return {"ok": True, "kind": None, "detail": ""}

    def require(self, action: str) -> None:
        decision = self.check(action)
        if not decision["ok"]:
            raise CampaignRefusal(decision["kind"], decision.get("detail") or "")

    # -- reservations ---------------------------------------------------
    def reserve(self, actions: dict[str, int], *, evidence=None,
                now: float | None = None) -> str:
        """Atomically charge every requested allowance and reserve it.

        All-or-nothing: if one action is unavailable nothing is charged, so a
        caller can refuse before spawning any child process.
        """
        now = time.time() if now is None else now
        needed = {key: int(value) for key, value in (actions or {}).items()}
        with self._locked():
            state = self.state()
            self._refusal(state, needed, now)
            identifier = (f"res-{time.time_ns()}-{os.getpid()}-"
                          f"{uuid.uuid4().hex[:8]}")
            for action, amount in needed.items():
                state["counts"][action] = int(
                    state["counts"].get(action, 0)) + amount
            state["reservations"][identifier] = {
                "actions": needed,
                "state": "reserved",
                "created_at": now,
                "evidence": evidence,
            }
            state["exhausted"] = sorted(
                key for key, budget in (state.get("budgets") or {}).items()
                if key != "wall_clock_seconds" and budget is not None
                and int(state["counts"].get(key, 0)) >= int(budget))
            state["history"].append({
                "action": "reserve",
                "outcome": "reserved",
                "at": now,
                "reservation": identifier,
                "detail": ", ".join(f"{key}={value}"
                                    for key, value in sorted(needed.items())),
                "evidence": evidence,
            })
            del state["history"][:-MAX_HISTORY]
            self._write(state)
        return identifier

    def complete(self, reservation_id: str, *, outcome: str = "ok",
                 detail: str = "", evidence=None,
                 consumed: dict[str, int] | None = None,
                 failure_action: str | None = None,
                 now: float | None = None) -> dict:
        """Record the outcome of a reservation.

        ``consumed`` reconciles a conservative multi-resource reservation with
        verified actual outcomes; only a successfully completed action may
        release its unused part.  A failure keeps the full charge.
        """
        now = time.time() if now is None else now
        if outcome not in ("ok", "failed"):
            raise CampaignRefusal("invalid_outcome", outcome)
        with self._locked():
            state = self.state()
            reservation = (state.get("reservations") or {}).get(
                reservation_id)
            if reservation is None:
                raise CampaignRefusal("reservation_missing", reservation_id)
            if reservation.get("state") != "reserved":
                raise CampaignRefusal("reservation_closed",
                                      f"{reservation_id} is "
                                      f"{reservation.get('state')!r}")
            actions = dict(reservation.get("actions") or {})
            if outcome == "failed":
                action = failure_action or next(iter(actions), None)
                if action:
                    kind = classify_failure(detail)
                    key = f"{action}:{kind}:{(detail or '')[:120]}"
                    failures = state.setdefault("failures", {})
                    row = failures.get(key) or {
                        "count": 0, "class": kind,
                        "detail": (detail or "")[:500]}
                    row["count"] = int(row.get("count", 0)) + 1
                    row["last_at"] = now
                    failures[key] = row
                    if row["count"] >= MAX_IDENTICAL_RETRIES:
                        state.setdefault("blocked_actions", {})[action] = (
                            f"identical failure {key!r} occurred "
                            f"{row['count']} times; administrative recovery is "
                            "required before retrying this action")
            elif consumed is not None:
                for action, reserved_amount in actions.items():
                    actual = int(consumed.get(action, reserved_amount))
                    actual = max(0, min(actual, int(reserved_amount)))
                    release = int(reserved_amount) - actual
                    if release:
                        state["counts"][action] = max(
                            0, int(state["counts"].get(action, 0)) - release)
                reservation["consumed"] = {
                    key: int(consumed.get(key, actions[key]))
                    for key in actions}
            reservation["state"] = ("completed" if outcome == "ok"
                                    else "failed")
            reservation["completed_at"] = now
            reservation["detail"] = (detail or "")[:500]
            state["exhausted"] = sorted(
                key for key, budget in (state.get("budgets") or {}).items()
                if key != "wall_clock_seconds" and budget is not None
                and int(state["counts"].get(key, 0)) >= int(budget))
            state["history"].append({
                "action": "complete",
                "outcome": outcome,
                "at": now,
                "reservation": reservation_id,
                "detail": (detail or "")[:500],
                "evidence": evidence,
            })
            del state["history"][:-MAX_HISTORY]
            self._write(state)
        return state

    def validate_reservation(self, reservation_id: str, action: str) -> bool:
        """True when the identifier holds an open reservation for the action."""
        if not reservation_id:
            return False
        try:
            state = self.state()
        except CampaignRefusal:
            return False
        reservation = (state.get("reservations") or {}).get(
            str(reservation_id))
        if not isinstance(reservation, dict):
            return False
        return (reservation.get("state") == "reserved"
                and action in (reservation.get("actions") or {}))

    def abandon_stale(self, *, max_age_seconds: float,
                      now: float | None = None) -> dict:
        """Mark abandoned reservations without restoring their charge."""
        now = time.time() if now is None else now
        with self._locked():
            state = self.state()
            changed = 0
            for identifier, reservation in (
                    state.get("reservations") or {}).items():
                if reservation.get("state") != "reserved":
                    continue
                if now - float(reservation.get("created_at") or now) < float(
                        max_age_seconds):
                    continue
                reservation["state"] = "abandoned"
                reservation["abandoned_at"] = now
                changed += 1
                state["history"].append({
                    "action": "abandon",
                    "outcome": "abandoned",
                    "at": now,
                    "reservation": identifier,
                    "detail": "reservation exceeded its maximum age; its "
                              "charge is retained conservatively",
                    "evidence": None,
                })
            if changed:
                del state["history"][:-MAX_HISTORY]
                self._write(state)
        return state

    # -- stop and authorized recovery -----------------------------------
    def stop(self, reason: str, detail: str = "") -> dict:
        if reason not in STOP_REASONS:
            raise CampaignRefusal("unknown_stop_reason", reason)
        with self._locked():
            state = self.state()
            state["stop"] = {"reason": reason, "detail": detail[:1000],
                             "at": time.time()}
            self._write(state)
        return state

    def admin_recover(self, *, token: str, reason: str,
                      actions=None) -> dict:
        """Operator-authorized recovery from a stop or blocked action.

        The token hash is stored at initialization; the token is never accepted
        from the model's tool arguments.  Every recovery is audited.
        """
        if not str(reason or "").strip():
            raise CampaignRefusal("recovery_reason_required")
        with self._locked():
            state = self.state()
            expected = state.get("admin_token_sha256")
            if not expected:
                raise CampaignRefusal(
                    "admin_recovery_disabled",
                    "this campaign was initialized without an administrative "
                    "token")
            if not token or _sha256_text(token) != expected:
                raise CampaignRefusal("admin_authorization_invalid")
            cleared = {"stop": state.get("stop") is not None,
                       "actions": []}
            state["stop"] = None
            blocked = state.setdefault("blocked_actions", {})
            targets = list(actions or blocked)
            for action in targets:
                if blocked.pop(action, None) is not None:
                    cleared["actions"].append(action)
            state["history"].append({
                "action": "admin_recover",
                "outcome": "authorized",
                "at": time.time(),
                "detail": reason[:500],
                "evidence": {"cleared": cleared},
            })
            self._write(state)
        return state

    def status(self) -> dict:
        state = self.state()
        budgets = state.get("budgets") or {}
        counts = state.get("counts") or {}
        remaining = {key: (None if key == "wall_clock_seconds"
                           else max(0, int(budget) - int(counts.get(key, 0))))
                     for key, budget in budgets.items()}
        open_reservations = [
            {"reservation": identifier,
             "actions": reservation.get("actions"),
             "created_at": reservation.get("created_at")}
            for identifier, reservation in
            (state.get("reservations") or {}).items()
            if reservation.get("state") == "reserved"]
        return {
            "schema": SCHEMA,
            "campaign_id": state.get("campaign_id"),
            "stop": state.get("stop"),
            "deadline_epoch": state.get("deadline_epoch"),
            "seconds_remaining": max(
                0.0, float(state.get("deadline_epoch") or 0) - time.time()),
            "budgets": budgets,
            "counts": counts,
            "remaining": remaining,
            "failure_classes": {
                key: value.get("class")
                for key, value in (state.get("failures") or {}).items()},
            "blocked_actions": state.get("blocked_actions") or {},
            "exhausted": state.get("exhausted") or [],
            "open_reservations": open_reservations,
            "reservation_count": len(state.get("reservations") or {}),
            "admin_recovery_available": bool(
                state.get("admin_token_sha256")),
            "auto_merge": state.get("auto_merge"),
            "worktree": state.get("worktree"),
            "history_count": len(state.get("history") or []),
        }


def from_env() -> Guard | None:
    """The configured campaign guard, or ``None`` when the feature is off."""
    path = os.environ.get("EU4_CAMPAIGN_STATE", "").strip()
    return Guard(path) if path else None


def require_from_env(action: str) -> Guard | None:
    """Refuse an action when the configured campaign has no allowance."""
    guard = from_env()
    if guard is not None:
        guard.require(action)
    return guard


def require_launch_reservation(reservation: str | None = None,
                               guard: "Guard | None" = None) -> None:
    """Refuse an EU4 spawn that does not hold an open launch reservation.

    ``reservation`` is the identity the trusted caller created with
    :meth:`Guard.reserve`; the capture path passes it explicitly so a stale or
    inherited process environment cannot authorize a launch.  When no explicit
    identity is supplied, the ambient token is used only as a compatibility
    fallback for direct CLI invocations, which a campaign still validates.
    Both launch choke points call this, so an alternative tool cannot spend an
    unguarded launch while a campaign budget is active.
    """
    guard = guard if guard is not None else from_env()
    if guard is None:
        return
    token = (reservation if reservation is not None
             else os.environ.get("EU4_CAMPAIGN_RESERVATION", "")).strip()
    if not guard.validate_reservation(token, "eu4_launches"):
        raise CampaignRefusal(
            "launch_reservation",
            "the campaign requires an open EU4 launch reservation; start the "
            "capture through automation.m6_frame capture")


@contextlib.contextmanager
def attempt(action: str, *, evidence=None):
    """Reserve and then record one resource-consuming action."""
    guard = from_env()
    if guard is None:
        yield None
        return
    reservation = guard.reserve({action: 1}, evidence=evidence)
    try:
        yield guard
    except BaseException as exc:  # noqa: BLE001 - record then propagate
        guard.complete(reservation, outcome="failed",
                       detail=f"{type(exc).__name__}: {exc}",
                       evidence=evidence)
        raise
    else:
        guard.complete(reservation, outcome="ok", evidence=evidence)


def _budget_pairs(values) -> dict:
    budgets = {}
    for item in values or ():
        name, _, value = str(item).partition("=")
        if not name or not _:
            raise SystemExit(f"invalid --budget {item!r}; use name=value")
        try:
            budgets[name] = int(value)
        except ValueError:
            budgets[name] = float(value)
    return budgets


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    init = sub.add_parser("init")
    init.add_argument("--state", required=True, type=Path)
    init.add_argument("--campaign-id", default="")
    init.add_argument("--budget", action="append", default=[],
                      help="override name=value (repeatable)")
    init.add_argument("--deadline-seconds", type=float, default=None)
    init.add_argument("--worktree", default=None)
    init.add_argument("--objective-digest", default=None)
    command = sub.add_parser("reserve")
    command.add_argument("--state", required=True, type=Path)
    command.add_argument("--action", action="append", required=True,
                         help="name=count (repeatable, all-or-nothing)")
    command.add_argument("--evidence", default=None)
    command = sub.add_parser("complete")
    command.add_argument("--state", required=True, type=Path)
    command.add_argument("--reservation", required=True)
    command.add_argument("--outcome", default="ok",
                         choices=("ok", "failed"))
    command.add_argument("--detail", default="")
    command = sub.add_parser("check")
    command.add_argument("--state", required=True, type=Path)
    command.add_argument("--action", required=True)
    command = sub.add_parser("status")
    command.add_argument("--state", required=True, type=Path)
    command = sub.add_parser("stop")
    command.add_argument("--state", required=True, type=Path)
    command.add_argument("--reason", required=True, choices=STOP_REASONS)
    command.add_argument("--detail", default="")
    command = sub.add_parser("abandon-stale")
    command.add_argument("--state", required=True, type=Path)
    command.add_argument("--max-age-seconds", type=float, required=True)
    command = sub.add_parser("admin-recover")
    command.add_argument("--state", required=True, type=Path)
    command.add_argument("--reason", required=True)
    command.add_argument("--action", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    guard = Guard(args.state)
    try:
        if args.operation == "init":
            value = guard.init(
                campaign_id=args.campaign_id,
                budgets=_budget_pairs(args.budget),
                deadline_seconds=args.deadline_seconds,
                worktree=args.worktree,
                objective_digest=args.objective_digest,
                admin_token=os.environ.get("EU4_CAMPAIGN_ADMIN_TOKEN") or None)
        elif args.operation == "reserve":
            identifier = guard.reserve(_actions(args.action),
                                       evidence=args.evidence)
            value = {"ok": True, "reservation": identifier,
                     "status": guard.status()}
        elif args.operation == "complete":
            value = guard.complete(args.reservation, outcome=args.outcome,
                                   detail=args.detail)
        elif args.operation == "check":
            value = guard.check(args.action)
        elif args.operation == "status":
            value = guard.status()
        elif args.operation == "abandon-stale":
            guard.abandon_stale(max_age_seconds=args.max_age_seconds)
            value = guard.status()
        elif args.operation == "admin-recover":
            value = guard.admin_recover(
                token=os.environ.get("EU4_CAMPAIGN_ADMIN_TOKEN") or "",
                reason=args.reason, actions=args.action or None)
        else:
            value = guard.stop(args.reason, args.detail)
    except CampaignRefusal as exc:
        print(json.dumps(exc.as_dict(), indent=2, sort_keys=True))
        return 2
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0 if value.get("ok", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
