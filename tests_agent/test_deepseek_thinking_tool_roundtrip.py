"""Offline: thinking + tool-call history must survive the second model request (and resume)."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets.function import FunctionToolset

from reverse_engineering.graphics_agent.session_checkpoint import (
    connect as checkpoint_connect,
    parse_message_history,
    persist_run_messages,
)


def _messages_contain_thinking_and_tool(messages: list[ModelMessage]) -> bool:
    has_thinking = False
    has_tool_return = False
    for msg in messages:
        parts = getattr(msg, "parts", None) or []
        for part in parts:
            if isinstance(part, ThinkingPart):
                has_thinking = True
            if isinstance(part, ToolReturnPart):
                has_tool_return = True
    return has_thinking and has_tool_return


class DeepSeekThinkingToolRoundtripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.captured: list[list[ModelMessage]] = []
        self.call_n = 0

        def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.captured.append(list(messages))
            self.call_n += 1
            if self.call_n == 1:
                return ModelResponse(
                    parts=[
                        ThinkingPart(content="internal reasoning trace"),
                        ToolCallPart(tool_name="probe", args={"q": "draw"}, tool_call_id="tc1"),
                    ]
                )
            return ModelResponse(parts=[TextPart("final answer")])

        self.model = FunctionModel(model_fn)

    def _agent(self) -> Agent:
        toolset = FunctionToolset()

        def probe(q: str) -> str:
            return f"evidence:{q}"

        toolset.add_function(probe, name="probe")
        return Agent(self.model, toolsets=[toolset])

    def test_second_request_preserves_thinking_and_tool_result(self) -> None:
        agent = self._agent()
        agent.run_sync("investigate draw path")
        self.assertGreaterEqual(len(self.captured), 2)
        self.assertTrue(_messages_contain_thinking_and_tool(self.captured[1]))

    def test_resumed_session_preserves_thinking_on_second_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = checkpoint_connect(Path(tmp) / "checkpoint.sqlite")
            agent = self._agent()
            result1 = agent.run_sync("turn one")
            persist_run_messages(ckpt, result1)
            history = parse_message_history(ckpt)
            self.call_n = 1
            self.captured.clear()
            agent.run_sync("turn two", message_history=history)
            hist_json = result1.all_messages_json().decode("utf-8")
            self.assertIn("internal reasoning", hist_json)
            self.assertGreaterEqual(len(self.captured), 1)
            if len(self.captured) >= 1:
                self.assertTrue(_messages_contain_thinking_and_tool(self.captured[0]))


if __name__ == "__main__":
    unittest.main()
