import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import frame_model_tier1_policy as tier1  # noqa: E402

WP8_ARCHIVE = (
    Path(__file__).resolve().parents[1]
    / "analysis/evidence/frame-model-offline-f62aa28-20261003T123219.715675Z-3c1e0db7.json"
)


class Tier1V4TrialCountTests(unittest.TestCase):
    def test_wp8_seven_pair_capture_is_unavailable_under_v4(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        replay = tier1.evaluate_wp8_steady_state_training_v4(preflight)
        self.assertEqual(replay["status"], "unavailable")
        self.assertEqual(replay["paired_trial_count"], tier1.TIER1_V4_TRIAL_COUNT)
        mesh = next(item for item in replay["training_recipes"] if item["recipe"] == "mesh")
        self.assertEqual(mesh["status"], "unavailable")
        self.assertIn("21 trials", mesh["reason"])

    def test_v4_policy_version_string(self):
        self.assertEqual(
            tier1.TIER1_CAUSAL_POLICY_V4.version,
            tier1.TIER1_CAUSAL_POLICY_VERSION_V4,
        )


if __name__ == "__main__":
    unittest.main()
