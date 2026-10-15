import json
import os
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.graphics_agent.tools import EvidenceService
from reverse_engineering.manifest import inventory_bundle_digest
from reverse_engineering.paths import ReError

GRAPH_MINI = Path(__file__).resolve().parent / "re_fixtures" / "graph_mini"


class ProvenanceTests(unittest.TestCase):
    def _export(self, tmp: str, provenance: dict) -> Path:
        inv_src = GRAPH_MINI / "inventory"
        exp = Path(tmp) / "exports" / "rev"
        inv = exp / "inventory"
        inv.mkdir(parents=True)
        for name in ("functions.jsonl", "call_edges.jsonl", "coverage.json"):
            (inv / name).write_text((inv_src / name).read_text())
        (exp / "provenance.json").write_text(json.dumps(provenance) + "\n")
        from reverse_engineering import index_db

        index_db.rebuild_index(exp / "index.sqlite", inv, exp / "decompiled", manifest={"batches": {}})
        (exp / "agent").mkdir(parents=True)
        return exp

    def test_digest_drift_fails_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inv_src = GRAPH_MINI / "inventory"
            digest = inventory_bundle_digest(inv_src)
            exp = self._export(tmp, {"inventory_bundle_digest": "0" * 64, "baseline_revision": "rev"})
            os.environ["EU4_RE_EXPORT"] = tmp
            with self.assertRaises(ReError):
                EvidenceService.open("rev", "inv")

    def test_get_measurement_provenance_blocks_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            digest = inventory_bundle_digest(GRAPH_MINI / "inventory")
            self._export(tmp, {"inventory_bundle_digest": digest, "baseline_revision": "rev"})
            os.environ["EU4_RE_EXPORT"] = tmp
            svc = EvidenceService.open("rev", "inv")
            out = svc.get_measurement_provenance("../../etc/passwd")
            self.assertEqual(out.get("error"), "path_not_allowed")


if __name__ == "__main__":
    unittest.main()
