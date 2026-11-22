"""The lightweight experiment rationale: structure, evidence, record."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import experiment_rationale


def rationale(evidence):
    return {
        "question": "does the sampler explain event 15?",
        "evidence": list(evidence),
        "gap": "effective sampler state was not isolated",
        "hypothesis": "Metal sampler state differs",
        "action": "targeted replay",
        "expected_information": "confirm or refute",
        "cheaper_alternatives_checked": True,
    }


class RationaleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.replay = self.root / "replay"
        self.replay.mkdir()
        (self.replay / "replay.json").write_text("{}")

    def test_a_complete_rationale_with_resolving_evidence_passes(self):
        value = rationale(["replay:replay"])
        problems = (experiment_rationale.validate(value)
                    or experiment_rationale.evidence_problems(
                        value, run_root=self.root))
        self.assertEqual(problems, [])

    def test_missing_fields_and_cheap_alternatives_are_rejected(self):
        value = rationale(["replay:replay"])
        value.pop("hypothesis")
        value["cheaper_alternatives_checked"] = False
        problems = experiment_rationale.validate(value)
        self.assertIn("hypothesis", " ".join(problems))
        self.assertIn("cheaper_alternatives_checked", " ".join(problems))

    def test_unresolved_evidence_is_rejected(self):
        problems = experiment_rationale.evidence_problems(
            rationale(["missing-record"]), run_root=self.root)
        self.assertTrue(problems)

    def test_a_replay_reference_must_name_a_result_directory(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.assertTrue(experiment_rationale.evidence_problems(
            rationale([f"replay:{empty}"]), run_root=self.root))

    def test_the_record_is_never_authoritative(self):
        value = experiment_rationale.record(
            self.root / "out", rationale(["replay:replay"]),
            result={"status": "confirmed_rendering_difference"})
        self.assertFalse(value["authoritative"])
        stored = json.loads(
            (self.root / "out" / experiment_rationale.RECORD_NAME).read_text())
        self.assertEqual(stored["rationale"]["action"], "targeted replay")


if __name__ == "__main__":
    unittest.main()
