import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from mcp import Client

from reverse_engineering.graphics_agent.mcp_server import build_server
from reverse_engineering.graphics_agent.tools import EvidenceService

GRAPH_MINI = Path(__file__).resolve().parents[1] / "tests" / "re_fixtures" / "graph_mini"


def _prepare_export(tmp: str) -> EvidenceService:
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
    (exp / "agent").mkdir(parents=True)
    os.environ["EU4_RE_EXPORT"] = tmp
    os.environ["EU4_AGENT_FIXTURE_MODE"] = "1"
    return EvidenceService.open("rev", "mcp-inv")


class McpSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_list_and_call_find_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = _prepare_export(tmp)
            server = build_server(service)
            async with Client(server) as client:
                listed = await client.list_tools()
                tools = listed.tools if hasattr(listed, "tools") else listed
                names = {getattr(t, "name", t[0] if isinstance(t, tuple) else str(t)) for t in tools}
                self.assertIn("find_paths", names)
                self.assertIn("get_disassembly", names)
                result = await client.call_tool(
                    "find_paths",
                    {"source_rva": "0x5000", "target_rva": "ext:_glDrawElements"},
                )
                payload = getattr(result, "structured_content", None) or {}
                if not payload and result.content:
                    payload = json.loads(result.content[0].text)
                self.assertTrue(payload.get("found"))


if __name__ == "__main__":
    unittest.main()
