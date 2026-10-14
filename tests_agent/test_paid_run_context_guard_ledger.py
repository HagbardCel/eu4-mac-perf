"""Adversarial: context guard after billable request 1 leaves reservation unreconciled."""

import os
import tempfile
import unittest
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage

from reverse_engineering.graphics_agent.context_guard import ContextBudgetExceeded, check_request_context
from reverse_engineering.graphics_agent.model_budget import (
    BudgetReservationError,
    finalize_paid_run_after_failure,
    investigation_remaining_micro_usd,
    reserve_paid_run,
)
from reverse_engineering.graphics_agent.model_registry import profile_for_mode
from reverse_engineering.graphics_agent.research_store import connect


class PaidRunContextGuardLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ALLOW_MODEL_REQUESTS"] = "true"
        os.environ["EU4_AGENT_AUTHORIZE_PAID_RUN"] = "1"
        self.call_n = 0

    def test_context_guard_after_billable_request_marks_unreconciled(self) -> None:
        cfg = {
            "model_spend": {
                "investigation_budget_usd": 0.2,
                "per_run_cap_usd": 0.2,
                "max_model_requests": 3,
            }
        }

        def model_fn(messages, info: AgentInfo) -> ModelResponse:
            self.call_n += 1
            if self.call_n == 1:
                return ModelResponse(
                    parts=[ToolCallPart(tool_name="blob", args={}, tool_call_id="1")],
                    usage=RunUsage(requests=1, input_tokens=10, output_tokens=5),
                )
            return ModelResponse(parts=[TextPart("done")])

        hooks = Hooks()

        @hooks.on.before_model_request
        async def guard(_ctx, request_context):
            if self.call_n >= 1:
                check_request_context(request_context, max_bytes=8_000)
            return request_context

        toolset = FunctionToolset()

        def blob() -> str:
            return "Z" * 50_000

        toolset.add_function(blob, name="blob")
        agent = Agent(FunctionModel(model_fn), toolsets=[toolset], capabilities=[hooks])

        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "r.sqlite")
            profile = profile_for_mode("discovery")
            paid = reserve_paid_run(conn, "inv", "discovery", profile, cfg)
            remaining_before = investigation_remaining_micro_usd(conn, "inv", cfg)
            with self.assertRaises(ContextBudgetExceeded):
                try:
                    agent.run_sync("go", usage_limits=paid.usage_limits)
                except ContextBudgetExceeded as exc:
                    finalize_paid_run_after_failure(
                        conn,
                        paid.run_id,
                        exc,
                        RunUsage(requests=1, input_tokens=10, output_tokens=5),
                    )
                    raise
            row = conn.execute(
                "SELECT state FROM model_spend_ledger WHERE run_id = ?", (paid.run_id,)
            ).fetchone()
            self.assertEqual(row[0], "unreconciled")
            self.assertEqual(investigation_remaining_micro_usd(conn, "inv", cfg), 0)
            with self.assertRaises(BudgetReservationError):
                reserve_paid_run(conn, "inv", "discovery", profile, cfg)


if __name__ == "__main__":
    unittest.main()
