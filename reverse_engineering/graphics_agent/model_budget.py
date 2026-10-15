"""Conservative paid-run budgeting: reservations, UsageLimits, and SQLite ledger.

This module implements a bounded spending *policy* (reservations + per-run caps). It is not a
guarantee of final provider billing: provider SDK retries and post-response cost accounting
can diverge from reservations.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic_ai.usage import UsageLimits

from reverse_engineering.graphics_agent.model_registry import (
    EffectiveModelProfile,
    resolve_effective_profile,
)
from reverse_engineering.paths import ReError

AUTHORIZE_PAID_ENV = "EU4_AGENT_AUTHORIZE_PAID_RUN"
SPEND_AUTHORIZATION_ENV = "EU4_AGENT_SPEND_AUTHORIZATION_ID"
MICRO_PER_USD = 1_000_000

# Upper-bound USD per 1M tokens (conservative; missing keys fail closed in pricing lookup).
UPPER_BOUND_USD_PER_MILLION: dict[str, dict[str, float]] = {
    "deepseek:deepseek-chat": {"input": 1.0, "output": 2.0},
    "deepseek:deepseek-flash": {"input": 0.5, "output": 1.0},
    "openai:gpt-4.1": {"input": 5.0, "output": 15.0},
    "openai:gpt-4.1-mini": {"input": 1.0, "output": 4.0},
}


class SpendPolicyError(ReError):
    pass


class PaidRunNotAuthorized(SpendPolicyError):
    pass


class BudgetReservationError(SpendPolicyError):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    transport_retries: int = 2

    @property
    def attempt_multiplier(self) -> int:
        return 1 + max(0, self.transport_retries)


@dataclass(frozen=True)
class RunSpendLimits:
    max_model_requests: int
    max_input_tokens: int
    max_output_tokens: int
    per_run_cap_micro_usd: int


@dataclass(frozen=True)
class InvestigationSpendLimits:
    budget_micro_usd: int
    provider_ceiling_micro_usd: int | None = None


@dataclass(frozen=True)
class PaidRunContext:
    run_id: str
    investigation_id: str
    spend_authorization_id: str
    mode: str
    reserved_micro_usd: int
    usage_limits: UsageLimits
    effective: EffectiveModelProfile


def resolve_spend_authorization_id(
    investigation_id: str,
    config: dict[str, Any] | None = None,
) -> str:
    """Budget bucket for cumulative reservations (shared across investigations when set)."""
    env = os.environ.get(SPEND_AUTHORIZATION_ENV, "").strip()
    if env:
        return env
    cfg = (config or {}).get("model_spend") or (config or {}).get("spend") or {}
    auth = cfg.get("spend_authorization_id")
    if auth:
        return str(auth)
    return investigation_id


def paid_model_requests_allowed() -> bool:
    if os.environ.get("ALLOW_MODEL_REQUESTS", "true").lower() == "false":
        return False
    return os.environ.get(AUTHORIZE_PAID_ENV, "").strip() == "1"


def require_paid_authorization() -> None:
    if not paid_model_requests_allowed():
        raise PaidRunNotAuthorized(
            "Paid model requests require ALLOW_MODEL_REQUESTS!=false and "
            f"{AUTHORIZE_PAID_ENV}=1"
        )


def spend_limits_from_config(config: dict[str, Any] | None) -> tuple[RunSpendLimits, InvestigationSpendLimits]:
    cfg = (config or {}).get("model_spend") or (config or {}).get("spend") or {}
    per_run_micro = (
        int(cfg["per_run_cap_micro_usd"])
        if cfg.get("per_run_cap_micro_usd") is not None
        else int(float(cfg.get("per_run_cap_usd", 0.25)) * MICRO_PER_USD)
    )
    inv_budget_micro = (
        int(cfg["investigation_budget_micro_usd"])
        if cfg.get("investigation_budget_micro_usd") is not None
        else int(float(cfg.get("investigation_budget_usd", 2.0)) * MICRO_PER_USD)
    )
    run = RunSpendLimits(
        max_model_requests=int(cfg.get("max_model_requests", os.environ.get("EU4_AGENT_MAX_MODEL_REQUESTS", "12"))),
        max_input_tokens=int(cfg.get("max_input_tokens", 128_000)),
        max_output_tokens=int(cfg.get("max_output_tokens", 8_192)),
        per_run_cap_micro_usd=per_run_micro,
    )
    inv = InvestigationSpendLimits(
        budget_micro_usd=inv_budget_micro,
        provider_ceiling_micro_usd=(
            int(cfg["provider_ceiling_micro_usd"])
            if cfg.get("provider_ceiling_micro_usd") is not None
            else None
        ),
    )
    return run, inv


def pricing_upper_bound_micro_per_token(wire_model_key: str) -> tuple[int, int]:
    """Return (input_micro_per_token, output_micro_per_token) using conservative price table."""
    row = UPPER_BOUND_USD_PER_MILLION.get(wire_model_key)
    if not row:
        raise BudgetReservationError(f"no upper-bound pricing for model {wire_model_key}")
    in_micro = max(1, math.ceil(row["input"] * MICRO_PER_USD / 1_000_000))
    out_micro = max(1, math.ceil(row["output"] * MICRO_PER_USD / 1_000_000))
    if in_micro <= 0 or out_micro <= 0:
        raise BudgetReservationError(f"invalid pricing for {wire_model_key}")
    return in_micro, out_micro


def compute_reservation_micro_usd(
    effective: EffectiveModelProfile,
    limits: RunSpendLimits,
    retry_policy: RetryPolicy | None = None,
) -> int:
    retry_policy = retry_policy or RetryPolicy()
    wire_key = effective.wire_model_key
    in_pt, out_pt = pricing_upper_bound_micro_per_token(wire_key)
    attempts = limits.max_model_requests * retry_policy.attempt_multiplier
    per_attempt = limits.max_input_tokens * in_pt + limits.max_output_tokens * out_pt
    reserved = attempts * per_attempt
    if reserved <= 0:
        raise BudgetReservationError("reservation computed to zero")
    return min(reserved, limits.per_run_cap_micro_usd)


def ensure_model_spend_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS model_spend_ledger (
          run_id TEXT PRIMARY KEY,
          investigation_id TEXT NOT NULL,
          mode TEXT NOT NULL,
          state TEXT NOT NULL,
          reserved_micro_usd INTEGER NOT NULL,
          settled_micro_usd INTEGER,
          wire_model_key TEXT NOT NULL,
          usage_json TEXT,
          spend_authorization_id TEXT,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_model_spend_inv_state
          ON model_spend_ledger(investigation_id, state);
        CREATE INDEX IF NOT EXISTS idx_model_spend_auth_state
          ON model_spend_ledger(spend_authorization_id, state);
        CREATE TABLE IF NOT EXISTS spend_authorization_totals (
          spend_authorization_id TEXT PRIMARY KEY,
          spent_micro_usd INTEGER NOT NULL DEFAULT 0,
          reserved_micro_usd INTEGER NOT NULL DEFAULT 0,
          provider_ceiling_micro_usd INTEGER,
          internal_ceiling_micro_usd INTEGER NOT NULL,
          updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_spend_totals (
          investigation_id TEXT PRIMARY KEY,
          spent_micro_usd INTEGER NOT NULL DEFAULT 0,
          reserved_micro_usd INTEGER NOT NULL DEFAULT 0,
          provider_ceiling_micro_usd INTEGER,
          internal_ceiling_micro_usd INTEGER NOT NULL,
          updated_at REAL NOT NULL
        );
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(model_spend_ledger)").fetchall()}
    if "spend_authorization_id" not in cols:
        conn.execute("ALTER TABLE model_spend_ledger ADD COLUMN spend_authorization_id TEXT")


def _auth_id_from_row(row: sqlite3.Row) -> str:
    auth = row["spend_authorization_id"]
    if auth:
        return str(auth)
    return str(row["investigation_id"])


def _auth_totals(conn: sqlite3.Connection, spend_authorization_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM spend_authorization_totals WHERE spend_authorization_id = ?",
        (spend_authorization_id,),
    ).fetchone()


def _ensure_auth_ledger(
    conn: sqlite3.Connection,
    spend_authorization_id: str,
    inv_limits: InvestigationSpendLimits,
) -> None:
    row = _auth_totals(conn, spend_authorization_id)
    now = time.time()
    if not row:
        conn.execute(
            """
            INSERT INTO spend_authorization_totals (
              spend_authorization_id, spent_micro_usd, reserved_micro_usd,
              provider_ceiling_micro_usd, internal_ceiling_micro_usd, updated_at
            ) VALUES (?, 0, 0, ?, ?, ?)
            """,
            (
                spend_authorization_id,
                inv_limits.provider_ceiling_micro_usd,
                inv_limits.budget_micro_usd,
                now,
            ),
        )


def authorization_remaining_micro_usd(conn: sqlite3.Connection, spend_authorization_id: str) -> int:
    row = _auth_totals(conn, spend_authorization_id)
    if not row:
        return 0
    ceiling = int(row["internal_ceiling_micro_usd"])
    spent = int(row["spent_micro_usd"])
    reserved = int(row["reserved_micro_usd"])
    return max(0, ceiling - spent - reserved)


def investigation_remaining_micro_usd(
    conn: sqlite3.Connection,
    investigation_id: str,
    config: dict[str, Any] | None = None,
) -> int:
    auth = resolve_spend_authorization_id(investigation_id, config)
    return authorization_remaining_micro_usd(conn, auth)


def build_usage_limits(
    reserved_micro_usd: int,
    run_limits: RunSpendLimits,
) -> UsageLimits:
    cost_usd = Decimal(reserved_micro_usd) / Decimal(MICRO_PER_USD)
    return UsageLimits(
        request_limit=run_limits.max_model_requests,
        input_tokens_limit=run_limits.max_input_tokens,
        output_tokens_limit=run_limits.max_output_tokens,
        cost_limit=cost_usd,
    )


def reserve_paid_run(
    conn: sqlite3.Connection,
    investigation_id: str,
    mode: str,
    profile: dict[str, Any],
    config: dict[str, Any] | None = None,
    *,
    run_id: str | None = None,
) -> PaidRunContext:
    require_paid_authorization()
    run_limits, inv_limits = spend_limits_from_config(config)
    effective = resolve_effective_profile(profile)
    retry = RetryPolicy(transport_retries=int((config or {}).get("model_spend", {}).get("transport_retries", 2)))
    reserved = compute_reservation_micro_usd(effective, run_limits, retry)
    spend_authorization_id = resolve_spend_authorization_id(investigation_id, config)

    ensure_model_spend_tables(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        active = conn.execute(
            """
            SELECT run_id FROM model_spend_ledger
            WHERE investigation_id = ? AND state = 'reserved'
            """,
            (investigation_id,),
        ).fetchone()
        if active:
            conn.execute("ROLLBACK")
            raise BudgetReservationError("another paid run is already reserved for this investigation")
        unreconciled = conn.execute(
            """
            SELECT run_id FROM model_spend_ledger
            WHERE spend_authorization_id = ? AND state = 'unreconciled'
            """,
            (spend_authorization_id,),
        ).fetchone()
        if unreconciled:
            conn.execute("ROLLBACK")
            raise BudgetReservationError(
                "spend authorization has unreconciled paid run; settle before new reservation"
            )

        _ensure_auth_ledger(conn, spend_authorization_id, inv_limits)
        totals = _auth_totals(conn, spend_authorization_id)
        assert totals is not None
        remaining = int(totals["internal_ceiling_micro_usd"]) - int(totals["spent_micro_usd"]) - int(
            totals["reserved_micro_usd"]
        )
        if reserved > remaining:
            conn.execute("ROLLBACK")
            raise BudgetReservationError(
                f"reservation {reserved} micro-USD exceeds authorization remaining {remaining}"
            )

        rid = run_id or uuid.uuid4().hex
        now = time.time()
        conn.execute(
            """
            INSERT INTO model_spend_ledger (
              run_id, investigation_id, mode, state, reserved_micro_usd, settled_micro_usd,
              wire_model_key, usage_json, spend_authorization_id, created_at, updated_at
            ) VALUES (?, ?, ?, 'reserved', ?, NULL, ?, NULL, ?, ?, ?)
            """,
            (
                rid,
                investigation_id,
                mode,
                reserved,
                effective.wire_model_key,
                spend_authorization_id,
                now,
                now,
            ),
        )
        conn.execute(
            """
            UPDATE spend_authorization_totals SET reserved_micro_usd = reserved_micro_usd + ?, updated_at = ?
            WHERE spend_authorization_id = ?
            """,
            (reserved, now, spend_authorization_id),
        )
        active_cost_cap = min(run_limits.per_run_cap_micro_usd, reserved)
        usage_limits = build_usage_limits(active_cost_cap, run_limits)
        conn.execute("COMMIT")
        return PaidRunContext(
            run_id=rid,
            investigation_id=investigation_id,
            spend_authorization_id=spend_authorization_id,
            mode=mode,
            reserved_micro_usd=reserved,
            usage_limits=usage_limits,
            effective=effective,
        )
    except (BudgetReservationError, PaidRunNotAuthorized, SpendPolicyError):
        raise
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise


def _estimate_settled_micro_usd(usage: Any) -> int:
    """Best-effort settle amount from RunUsage; falls back to zero (unreconciled path)."""
    try:
        cost = getattr(usage, "cost", None)
        if cost is not None:
            return int(Decimal(str(cost)) * MICRO_PER_USD)
    except Exception:
        pass
    return 0


def settle_paid_run(conn: sqlite3.Connection, run_id: str, usage: Any | None = None) -> None:
    ensure_model_spend_tables(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM model_spend_ledger WHERE run_id = ?", (run_id,)).fetchone()
        if not row or row["state"] != "reserved":
            conn.execute("ROLLBACK")
            raise SpendPolicyError(f"cannot settle run {run_id} in state {row['state'] if row else 'missing'}")
        reserved = int(row["reserved_micro_usd"])
        settled = _estimate_settled_micro_usd(usage) if usage is not None else 0
        if settled <= 0 and usage is not None:
            settled = min(reserved, _usage_token_upper_micro(usage, row["wire_model_key"]))
        if settled <= 0:
            settled = reserved
        settled = min(settled, reserved)
        now = time.time()
        usage_json = json.dumps(_usage_snapshot(usage)) if usage is not None else None
        conn.execute(
            """
            UPDATE model_spend_ledger SET state = 'settled', settled_micro_usd = ?, usage_json = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (settled, usage_json, now, run_id),
        )
        auth_id = _auth_id_from_row(row)
        conn.execute(
            """
            UPDATE spend_authorization_totals SET
              reserved_micro_usd = reserved_micro_usd - ?,
              spent_micro_usd = spent_micro_usd + ?,
              updated_at = ?
            WHERE spend_authorization_id = ?
            """,
            (reserved, settled, now, auth_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def mark_unreconciled_paid_run(conn: sqlite3.Connection, run_id: str, reason: str) -> None:
    ensure_model_spend_tables(conn)
    now = time.time()
    row = conn.execute("SELECT * FROM model_spend_ledger WHERE run_id = ?", (run_id,)).fetchone()
    if not row or row["state"] != "reserved":
        return
    reserved = int(row["reserved_micro_usd"])
    conn.execute(
        """
        UPDATE model_spend_ledger SET state = 'unreconciled', usage_json = ?, updated_at = ?
        WHERE run_id = ?
        """,
        (json.dumps({"error": reason}), now, run_id),
    )
    auth_id = _auth_id_from_row(row)
    conn.execute(
        """
        UPDATE spend_authorization_totals SET reserved_micro_usd = reserved_micro_usd - ?, spent_micro_usd = spent_micro_usd + ?,
          updated_at = ?
        WHERE spend_authorization_id = ?
        """,
        (reserved, reserved, now, auth_id),
    )
    conn.commit()


def extract_run_usage_from_exception(exc: BaseException) -> Any | None:
    try:
        from pydantic_ai.exceptions import AgentRunError

        if isinstance(exc, AgentRunError):
            return exc.usage()
    except Exception:
        pass
    usage = getattr(exc, "usage", None)
    if callable(usage):
        try:
            return usage()
        except Exception:
            return None
    return usage


def finalize_paid_run_after_failure(
    conn: sqlite3.Connection,
    run_id: str,
    exc: BaseException,
    usage: Any | None = None,
) -> None:
    """Fail closed: billable partial runs stay unreconciled; otherwise release reservation."""
    from reverse_engineering.graphics_agent.context_guard import ContextBudgetExceeded

    usage = usage or extract_run_usage_from_exception(exc)
    requests = int(getattr(usage, "requests", 0) or 0) if usage is not None else 0
    billable = requests > 0
    if isinstance(exc, ContextBudgetExceeded) and billable:
        mark_unreconciled_paid_run(conn, run_id, str(exc))
        return
    if billable:
        mark_unreconciled_paid_run(conn, run_id, f"interrupted after billable usage: {exc}")
        return
    release_paid_run(conn, run_id)


def release_paid_run(conn: sqlite3.Connection, run_id: str) -> None:
    ensure_model_spend_tables(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM model_spend_ledger WHERE run_id = ?", (run_id,)).fetchone()
        if not row or row["state"] != "reserved":
            conn.execute("ROLLBACK")
            return
        reserved = int(row["reserved_micro_usd"])
        now = time.time()
        conn.execute(
            "UPDATE model_spend_ledger SET state = 'released', updated_at = ? WHERE run_id = ?",
            (now, run_id),
        )
        auth_id = _auth_id_from_row(row)
        conn.execute(
            """
            UPDATE spend_authorization_totals SET reserved_micro_usd = reserved_micro_usd - ?, updated_at = ?
            WHERE spend_authorization_id = ?
            """,
            (reserved, now, auth_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _usage_snapshot(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    fields = ("requests", "input_tokens", "output_tokens", "total_tokens", "cost")
    return {k: getattr(usage, k, None) for k in fields if getattr(usage, k, None) is not None}


def _usage_token_upper_micro(usage: Any, wire_model_key: str) -> int:
    in_pt, out_pt = pricing_upper_bound_micro_per_token(wire_model_key)
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    return inp * in_pt + out * out_pt
