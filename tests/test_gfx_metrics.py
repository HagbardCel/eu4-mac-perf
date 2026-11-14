"""PR 5: efficiency metrics are descriptive and never hide inconclusive work."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_metrics, launch_ledger  # noqa: E402


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self):
        self._temporary.cleanup()

    def session(self, name: str, *, result: str, compared: int, failed: int,
                not_compared: int, duration: float, bytes_: int) -> Path:
        run = self.root / name
        run.mkdir(parents=True)
        (run / "session.json").write_text(json.dumps({"session_id": name}))
        (run / "result.json").write_text(json.dumps({
            "session_id": name,
            "result": result,
            "duration_seconds": duration,
            "scenario": {"mode": "lockstep", "comparison_policy": "tolerant"},
            "readiness": {"elapsed_seconds": 1.5},
            "frames": {
                "observed_reference": 10,
                "observed_candidate": 10,
                "paired": 8,
                "aligned": 8,
                "comparison": {"compared": compared, "failed": failed,
                               "not_compared": not_compared},
            },
            "health": {"verdict": "unknown"},
            "artifacts": {"bytes": bytes_, "dropped": 0},
        }))
        return run

    def test_metrics_separate_inconclusive_and_failed(self):
        self.session("a", result="completed", compared=4, failed=0,
                     not_compared=4, duration=30.0, bytes_=100)
        self.session("b", result="stopped", compared=1, failed=1,
                     not_compared=7, duration=45.0, bytes_=200)
        value = gfx_metrics.summarize(self.root)
        self.assertEqual(value["schema"], gfx_metrics.SCHEMA)
        self.assertEqual(value["sessions"], 2)
        self.assertEqual(value["frames"]["paired"], 16)
        self.assertEqual(value["frames"]["compared"], 5)
        self.assertEqual(value["frames"]["failed"], 1)
        self.assertEqual(value["inconclusive_comparisons"], 11)
        self.assertEqual(value["artifact_bytes"], 300)
        self.assertEqual(value["per_session"][1]["first_discrepancy_seconds"], 45.0)
        self.assertIsNone(value["per_session"][0]["first_discrepancy_seconds"])

    def test_ledger_launches_are_reported_from_the_ledger(self):
        ledger = self.root / "ledger.json"
        launch_ledger.create_ledger(
            ledger, workflow_id="w", limit=4, deadline_seconds=60.0,
            executable={"path": "/bin/true", "sha256": "a" * 64},
            output_root=str(self.root),
        )
        launch_ledger.claim(ledger, campaign_id="c", run_root=self.root,
                            output=self.root / "a", targets=["frame"],
                            requested_timeout=10.0, executable_sha256="a" * 64,
                            executable_path="/bin/true", require_pinned=True)
        value = gfx_metrics.summarize(self.root, ledger=ledger)
        self.assertEqual(value["launches"]["used"], 1)
        self.assertEqual(value["launches"]["statuses"]["reserved"], 1)

    def test_cli_writes_json(self):
        self.session("a", result="completed", compared=1, failed=0,
                     not_compared=0, duration=1.0, bytes_=1)
        out = self.root / "metrics.json"
        code = gfx_metrics.main(["report", "--root", str(self.root),
                                 "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.read_text())["schema"], gfx_metrics.SCHEMA)


if __name__ == "__main__":
    unittest.main()
