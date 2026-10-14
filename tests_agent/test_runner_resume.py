import json
import os
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.graphics_agent.runner import run_investigation

GRAPH_MINI = Path(__file__).resolve().parents[1] / "tests" / "re_fixtures" / "graph_mini"


class RunnerResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ALLOW_MODEL_REQUESTS"] = "False"

    def _prepare(self, tmp: str) -> None:
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
        (agent / "graphics-agent-context.json").write_text("{}")
        os.environ["EU4_RE_EXPORT"] = tmp

    def test_second_turn_uses_message_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._prepare(tmp)
            run_investigation("rev", "pilot", "discovery", "turn one", "sess-resume", use_test_model=True)
            result = run_investigation("rev", "pilot", "discovery", "turn two", "sess-resume", use_test_model=True)
            self.assertIn("output", result)
            ckpt = Path(tmp) / "exports" / "rev" / "agent" / "sessions" / "sess-resume" / "checkpoint.sqlite"
            from reverse_engineering.graphics_agent.session_checkpoint import connect, load_message_history_json

            conn = connect(ckpt)
            hist = load_message_history_json(conn)
            self.assertIsNotNone(hist)
            self.assertIn(b"turn one", hist)
            self.assertIn(b"turn two", hist)


if __name__ == "__main__":
    unittest.main()
