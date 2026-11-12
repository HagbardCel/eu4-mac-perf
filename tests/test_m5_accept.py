"""M5 acceptance semantics: live-engine qualification is not replay qualification."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "campaigns" / "eu4-m5" / "accept.py"
POLICY = ROOT / "analysis" / "m5" / "comparison-policy.json"
MANIFEST = ROOT / "analysis" / "m5" / "evidence-manifest.json"
LIVE = ROOT / "analysis" / "m5" / "live-evidence.json"
try:
    _MANIFEST_VALUE = json.loads(MANIFEST.read_text())
except (OSError, ValueError):
    _MANIFEST_VALUE = {}
LIVE_RUN = ROOT / str(
    ((_MANIFEST_VALUE.get("live_engine") or {}).get("run") or "")
)


def _load_checker():
    spec = importlib.util.spec_from_file_location("m5_accept", CHECKER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class M5AcceptanceSemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.checker = _load_checker()
        cls.policy = json.loads(POLICY.read_text())
        cls.manifest = json.loads(MANIFEST.read_text())

    def test_missing_live_evidence_is_not_qualified(self) -> None:
        manifest = dict(self.manifest)
        manifest.pop("live_engine", None)
        gates, live, composition, pixels, _ = self.checker.live_engine_gates(
            ROOT, manifest, self.policy
        )
        self.assertFalse(live)
        self.assertFalse(composition)
        self.assertFalse(pixels)
        self.assertTrue(any(g["name"] == "live:evidence" and not g["ok"] for g in gates))

    def test_synthetic_replay_descriptor_cannot_satisfy_live_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m5-live-negative-") as tmp:
            evidence = Path(tmp) / "live-evidence.json"
            fake_run = Path(tmp) / "synthetic-run"
            fake_run.mkdir()
            evidence.write_text(json.dumps({
                "schema": "eu4-m5-live-evidence/v1",
                "run": str(fake_run),
                "engine_reached": True,
                "checkpoint": {
                    "descriptor": "checkpoint-after-draw-1.json",
                    "bgra": "checkpoint-after-draw-1.bgra",
                },
                "logical": {
                    "descriptor": "metal-logical.json",
                    "bgra": "metal-logical.bgra",
                },
                "prefix_pre": {
                    "descriptor": "metal-prefix-pre.json",
                    "bgra": "metal-prefix-pre.bgra",
                },
            }))
            manifest = dict(self.manifest)
            manifest["live_engine"] = {
                "evidence": str(evidence),
                "sha256": self.checker._sha(evidence),
            }
            gates, live, composition, pixels, _ = self.checker.live_engine_gates(
                ROOT, manifest, self.policy
            )
            self.assertFalse(live)
            self.assertFalse(composition)
            self.assertFalse(pixels)
            self.assertTrue(any(g["name"].startswith("live:") and not g["ok"] for g in gates))

    @unittest.skipUnless(LIVE_RUN.is_dir(), "local live run is absent")
    def test_recorded_live_evidence_satisfies_live_gate(self) -> None:
        gates, live, composition, pixels, _ = self.checker.live_engine_gates(
            ROOT, self.manifest, self.policy
        )
        self.assertTrue(live, [g for g in gates if not g["ok"]])
        self.assertTrue(composition)
        # The live pixel comparison is deliberately separate; it may be false
        # because the loading-screen background is not reconstructed.
        self.assertIsInstance(pixels, bool)


if __name__ == "__main__":
    unittest.main()
