import os
import unittest

from reverse_engineering.graphics_agent.context_guard import ContextBudgetExceeded, check_request_context


class _FakeParams:
    def __init__(self, instruction_parts, function_tools=None):
        self.instruction_parts = instruction_parts
        self.function_tools = function_tools or []
        self.builtin_tools = []
        self.output_tools = []


class _FakeRequestContext:
    def __init__(self, messages, params):
        self.messages = messages
        self.model_request_parameters = params
        self.model_settings = None


class ContextGuardTests(unittest.TestCase):
    def test_blocks_oversized_history(self) -> None:
        big = "x" * 400_000
        ctx = _FakeRequestContext([{"role": "user", "content": big}], _FakeParams(["sys"]))
        with self.assertRaises(ContextBudgetExceeded):
            check_request_context(ctx, max_bytes=50_000)

    def test_mid_run_growth_scenario(self) -> None:
        """Simulates second request after tool return enlarged history."""
        first = _FakeRequestContext([{"role": "user", "content": "ok"}], _FakeParams(["sys"]))
        check_request_context(first, max_bytes=50_000)
        tool_blob = {"tool": "get_disassembly", "content": "y" * 120_000}
        second = _FakeRequestContext(
            [{"role": "user", "content": "ok"}, {"role": "tool", "content": tool_blob}],
            _FakeParams(["sys"]),
        )
        with self.assertRaises(ContextBudgetExceeded):
            check_request_context(second, max_bytes=50_000)


if __name__ == "__main__":
    unittest.main()
