import json
import os
import tempfile
import unittest
from pathlib import Path

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from reverse_engineering.graphics_agent.runner import _build_agent, _inject_briefing, _system_prompt, run_investigation
from reverse_engineering.graphics_agent.session_checkpoint import connect, load_message_history_json
from reverse_engineering.graphics_agent.tools import EvidenceService

GRAPH_MINI = Path(__file__).resolve().parents[1] / "tests" / "re_fixtures" / "graph_mini"


class RunnerInvestigationTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ALLOW_MODEL_REQUESTS"] = "false"

    def _prepare(self, tmp: str) -> EvidenceService:
        inv_src = GRAPH_MINI / "inventory"
        exp = Path(tmp) / "exports" / "rev"
        inv = exp / "inventory"
        inv.mkdir(parents=True)
        for name in ("functions.jsonl", "call_edges.jsonl", "coverage.json"):
            (inv / name).write_text((inv_src / name).read_text())
        from reverse_engineering.manifest import inventory_bundle_digest

        (exp / "provenance.json").write_text(
            json.dumps(
                {
                    "inventory_bundle_digest": inventory_bundle_digest(inv_src),
                    "baseline_revision": "rev",
                }
            )
            + "\n"
        )
        from reverse_engineering import index_db

        index_db.rebuild_index(exp / "index.sqlite", inv, exp / "decompiled", manifest={"batches": {}})
        agent = exp / "agent"
        agent.mkdir(parents=True)
        (agent / "graphics-agent-context.json").write_text(
            json.dumps(
                {
                    "mission": "Map draw path",
                    "research_questions": ["How does indexed drawing reach OpenGL?"],
                    "evidence_rules": ["Cite call-edge or ghidra evidence"],
                }
            )
        )
        os.environ["EU4_RE_EXPORT"] = tmp
        return EvidenceService.open("rev", "inv-fn")

    def test_function_model_calls_tools_and_persists_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = self._prepare(tmp)
            calls = {"n": 0}

            def model_fn(messages, info):
                calls["n"] += 1
                if calls["n"] == 1:
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                tool_name="find_paths",
                                args={"source_rva": "0x5000", "target_rva": "ext:_glDrawElements"},
                                tool_call_id="t1",
                            )
                        ]
                    )
                if calls["n"] == 2:
                    path = service.find_paths("0x5000", "ext:_glDrawElements")
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                tool_name="submit_findings",
                                args={
                                    "claim_id": "fn-model-claim",
                                    "statement": "Draw path reaches OpenGL",
                                    "evidence_ids": path["evidence_ids"],
                                },
                                tool_call_id="t2",
                            )
                        ]
                    )
                return ModelResponse(parts=[TextPart("investigation complete")])

            system = _inject_briefing(service, _system_prompt("discovery"))
            agent = _build_agent("discovery", service, system, model_override=FunctionModel(function=model_fn))
            result = agent.run_sync("Investigate draw path to OpenGL")
            self.assertIn("investigation", str(result.output).lower())
            row = service.research_conn.execute(
                "SELECT claim_id FROM claims WHERE claim_id = ?", ("fn-model-claim",)
            ).fetchone()
            self.assertIsNotNone(row)

    def test_resume_turn_depends_on_tool_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = self._prepare(tmp)
            calls = {"n": 0}

            def model_fn(messages, info):
                calls["n"] += 1
                if calls["n"] == 1:
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                tool_name="find_paths",
                                args={"source_rva": "0x5000", "target_rva": "ext:_glDrawElements"},
                                tool_call_id="r1",
                            )
                        ]
                    )
                return ModelResponse(parts=[TextPart("resume ok")])

            system = _inject_briefing(service, _system_prompt("discovery"))
            agent = _build_agent("discovery", service, system, model_override=FunctionModel(function=model_fn))
            from reverse_engineering.graphics_agent.session_checkpoint import (
                connect as ckpt_connect,
                persist_run_messages,
            )

            session_dir = service.agent_dir() / "sessions" / "sess-tools"
            session_dir.mkdir(parents=True, exist_ok=True)
            ckpt = ckpt_connect(session_dir / "checkpoint.sqlite")
            r1 = agent.run_sync("turn one")
            persist_run_messages(ckpt, r1)
            r2 = agent.run_sync("turn two", message_history=r1.all_messages())
            persist_run_messages(ckpt, r2)
            hist = load_message_history_json(ckpt)
            self.assertIsNotNone(hist)
            self.assertIn(b"find_paths", hist or b"")
            self.assertIn(b"turn two", hist or b"")


if __name__ == "__main__":
    unittest.main()
