"""M5 acceptance semantics: live-engine qualification is not replay qualification."""

from __future__ import annotations

import importlib.util
import json
import shutil
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

    def test_modified_policy_digest_is_fail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m5-policy-") as tmp:
            root = Path(tmp)
            (root / "analysis" / "m5").mkdir(parents=True)
            shutil.copy(MANIFEST, root / "analysis" / "m5" / "evidence-manifest.json")
            policy = json.loads(POLICY.read_text())
            policy["name"] = "tampered-policy"
            (root / "analysis" / "m5" / "comparison-policy.json").write_text(
                json.dumps(policy)
            )
            gates, status = self.checker.evidence_integrity_gates(
                root, json.loads(MANIFEST.read_text()), policy
            )
            self.assertEqual(status, "fail")
            self.assertTrue(any(g["name"] == "evidence:policy" and not g["ok"]
                                for g in gates))

    def test_malformed_policy_is_fail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m5-policy-bad-") as tmp:
            root = Path(tmp)
            (root / "analysis" / "m5").mkdir(parents=True)
            shutil.copy(MANIFEST, root / "analysis" / "m5" / "evidence-manifest.json")
            (root / "analysis" / "m5" / "comparison-policy.json").write_text("{not json")
            gates, status = self.checker.evidence_integrity_gates(
                root, json.loads(MANIFEST.read_text()), {}
            )
            self.assertEqual(status, "fail")
            self.assertTrue(any(g["name"] == "evidence:policy" and not g["ok"]
                                for g in gates))

    def test_missing_local_evidence_is_inconclusive(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m5-missing-") as tmp:
            gates, status = self.checker.evidence_integrity_gates(
                Path(tmp), {}, {}
            )
            self.assertEqual(status, "inconclusive")
            self.assertTrue(any(not g["ok"] for g in gates))

    @unittest.skipUnless(LIVE_RUN.is_dir(), "local live run is absent")
    def test_live_contribution_is_recomputed_from_pixels(self) -> None:
        gates, live, composition, pixels, report = self.checker.live_engine_gates(
            ROOT, self.manifest, self.policy
        )
        self.assertTrue(live, [g for g in gates if not g["ok"]])
        self.assertTrue(composition)
        contribution = report["contribution"]
        self.assertEqual(contribution["live_recomputed"], 157254)
        self.assertEqual(contribution["gl_recomputed"], 157254)
        self.assertTrue(contribution["reported_agree"])
        self.assertEqual(contribution["mask_iou"], 1.0)

    @unittest.skipUnless(LIVE_RUN.is_dir(), "local live run is absent")
    def test_reported_contribution_disagreement_blocks_composition(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m5-contribution-") as tmp:
            evidence = json.loads(LIVE.read_text())
            evidence["contribution"]["live_differing_pixels"] = 1
            evidence_path = Path(tmp) / "live-evidence.json"
            evidence_path.write_text(json.dumps(evidence))
            manifest = dict(self.manifest)
            manifest["live_engine"] = {
                "evidence": str(evidence_path),
                "sha256": self.checker._sha(evidence_path),
            }
            gates, live, composition, pixels, _ = self.checker.live_engine_gates(
                ROOT, manifest, self.policy
            )
            self.assertTrue(live)
            self.assertFalse(composition)
            self.assertTrue(any(g["name"] == "live:contribution" and not g["ok"]
                                for g in gates))

    @unittest.skipUnless(LIVE_RUN.is_dir(), "local live run is absent")
    def test_missing_live_descriptor_is_controlled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m5-missing-live-") as tmp:
            evidence = json.loads(LIVE.read_text())
            evidence["logical"]["descriptor"] = "missing-metal-logical.json"
            evidence_path = Path(tmp) / "live-evidence.json"
            evidence_path.write_text(json.dumps(evidence))
            manifest = dict(self.manifest)
            manifest["live_engine"] = {
                "evidence": str(evidence_path),
                "sha256": self.checker._sha(evidence_path),
            }
            gates, live, composition, pixels, _ = self.checker.live_engine_gates(
                ROOT, manifest, self.policy
            )
            self.assertFalse(live)
            self.assertFalse(composition)
            self.assertTrue(any(g["name"] == "live:engine-gpu" and not g["ok"]
                                for g in gates))

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
