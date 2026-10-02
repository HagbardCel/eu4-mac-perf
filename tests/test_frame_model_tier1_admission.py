import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import frame_model_gates as gates  # noqa: E402
import frame_model_tier1_policy as tier1  # noqa: E402


class Tier1AdmissionGateTests(unittest.TestCase):
    def test_calibration_only_requires_causal_not_forensic(self):
        evidence = gates.GateEvidence()
        evidence.record("format_v3", "passed", "ok")
        evidence.record("offline_causal_admission", "passed", "ok")
        evidence.record("offline_forensic_suitability", "failed", "expected forensic fail")
        evidence.require(gates.required_gates_for_report_kind("calibration_only"))

    def test_calibration_only_blocks_when_causal_fails(self):
        evidence = gates.GateEvidence()
        evidence.record("format_v3", "passed", "ok")
        evidence.record("offline_causal_admission", "failed", "causal fail")
        evidence.record("offline_forensic_suitability", "passed", "ok")
        with self.assertRaises(ValueError):
            evidence.require(gates.required_gates_for_report_kind("calibration_only"))

    def test_forensic_pass_does_not_satisfy_causal(self):
        evidence = gates.GateEvidence()
        evidence.record("format_v3", "passed", "ok")
        evidence.record("offline_forensic_suitability", "passed", "ok")
        with self.assertRaises(ValueError):
            evidence.require(gates.required_gates_for_report_kind("calibration_only"))


class Tier1ReplayTests(unittest.TestCase):
    @staticmethod
    def _load_preflight(path: Path) -> dict:
        data = json.loads(path.read_text())
        if "preflight" in data:
            return data["preflight"]
        return data

    def test_replay_legacy_archive_causal_fails(self):
        path = Path(__file__).resolve().parents[1] / (
            "analysis/evidence/frame-model-offline-legacy-373f3ff-20261001T204509Z.json"
        )
        if not path.is_file():
            self.skipTest("legacy archive missing")
        preflight = self._load_preflight(path)
        replay = tier1.replay_archive_preflight(preflight)
        self.assertEqual(replay["offline_causal_admission"]["status"], "failed")
        self.assertEqual(replay["offline_forensic_suitability"]["status"], "failed")

    def test_replay_50643ce_archive_preserves_failure(self):
        matches = list(
            (Path(__file__).resolve().parents[1] / "analysis/evidence").glob(
                "frame-model-offline-50643ce-*.json",
            ),
        )
        if not matches:
            self.skipTest("50643ce archive missing")
        preflight = self._load_preflight(matches[0])
        replay = tier1.replay_archive_preflight(preflight)
        self.assertEqual(replay["offline_causal_admission"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
