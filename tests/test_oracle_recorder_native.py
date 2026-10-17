"""P4.3: exact capture accounting in the native recorder core.

The recorder must account for every hook entry - intercepted, selected, captured,
incomplete, lost - with no silent drops, under disabled capture, under a bounded
buffer, and under reentrant observation. The transcript is a function of C
semantics only, so it must reproduce byte-for-byte across compilers.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from oracle.recorder import harness, policy_reference


def _counters(scenario: dict) -> dict[str, int]:
    return {key: int(value) for key, value in scenario["counters"].items()}


class NativeRecorderAccountingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="oracle-p4-native-")
        cls.exe = harness.build("recorder", Path(cls._tmp) / "build")
        cls.transcript = harness.run_transcript(cls.exe)
        cls.parsed = harness.parse_recorder_transcript(cls.transcript)
        cls.scenarios = {s["name"]: s for s in cls.parsed["scenarios"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_reproduces_the_committed_golden_transcript(self):
        self.assertEqual(self.transcript, harness.golden_transcript("recorder"))

    def test_output_is_deterministic(self):
        self.assertEqual(harness.run_transcript(self.exe), self.transcript)

    def test_transcript_is_well_formed(self):
        self.assertEqual(self.parsed["version"], "v1")
        self.assertEqual(sorted(self.scenarios), ["bounded", "disabled", "enabled", "misuse",
                                                  "policy-observe-data", "policy-observe-flow",
                                                  "reentrant"])

    def test_every_scenario_reports_the_accounting_as_holding(self):
        for scenario in self.parsed["scenarios"]:
            with self.subTest(scenario=scenario["name"]):
                self.assertEqual(scenario["accounting"], "ok")

    def test_accounting_identity_holds_independently_of_the_reported_flag(self):
        for scenario in self.parsed["scenarios"]:
            counters = _counters(scenario)
            with self.subTest(scenario=scenario["name"]):
                self.assertEqual(counters["intercepted"],
                                 counters["selected"] + counters["unselected"])
                self.assertEqual(counters["selected"],
                                 counters["captured"] + counters["incomplete"] + counters["lost"])

    def test_disabled_capture_counts_interceptions_without_selecting(self):
        counters = _counters(self.scenarios["disabled"])
        self.assertEqual(counters["intercepted"], 10)
        self.assertEqual(counters["selected"], 0)
        self.assertEqual(counters["unselected"], counters["intercepted"])
        self.assertEqual(counters["captured"], 0)
        self.assertEqual(counters["rejected"], 0)

    def test_enabled_capture_selects_everything_and_loses_nothing(self):
        counters = _counters(self.scenarios["enabled"])
        self.assertEqual(counters["selected"], counters["intercepted"])
        self.assertEqual(counters["lost"], 0)
        # Two calls operated on no live resource: a stale read and a failed create.
        self.assertEqual(counters["incomplete"], 2)
        self.assertEqual(counters["captured"], counters["selected"] - counters["incomplete"])

    def test_bounded_capture_reports_loss_exactly(self):
        counters = _counters(self.scenarios["bounded"])
        self.assertGreater(counters["lost"], 0)
        self.assertEqual(counters["captured"], 4)
        self.assertEqual(counters["captured"] + counters["incomplete"] + counters["lost"],
                         counters["selected"])

    def test_reentrant_observation_is_detected(self):
        counters = _counters(self.scenarios["reentrant"])
        self.assertEqual(counters["intercepted"], 2)
        self.assertEqual(counters["reentrant"], 1)

    def test_an_unmatched_exit_is_rejected(self):
        counters = _counters(self.scenarios["misuse"])
        self.assertEqual(counters["intercepted"], 0)
        self.assertEqual(counters["rejected"], 1)

    def test_the_subject_ran_five_full_scripts(self):
        self.assertEqual(int(self.parsed["end"]["effects"]), 35)

    # --- P4.4: the capture policy -------------------------------------------------

    def test_policy_canonical_bytes_match_the_reference_encoding(self):
        policies = {p["name"]: p for p in self.parsed["policies"]}
        self.assertEqual(sorted(policies), ["observe-data", "observe-flow"])
        for name, policy in policies.items():
            descriptor = policy_reference.DECLARED_POLICIES[name]
            with self.subTest(policy=name):
                self.assertEqual(bytes.fromhex(policy["canonical"]),
                                 policy_reference.canonical_bytes(descriptor))

    def test_policy_digest_is_order_independent(self):
        descriptor = policy_reference.DECLARED_POLICIES["observe-data"]
        reordered = dict(descriptor, rules=list(reversed(descriptor["rules"])))
        self.assertEqual(policy_reference.digest(reordered),
                         policy_reference.digest(descriptor))

    def test_policy_deep_captures_only_its_selected_hooks(self):
        data = _counters(self.scenarios["policy-observe-data"])
        # Deep: the five update/read calls. Counted only: three creates, share, destroy.
        self.assertEqual(data["selected"], 5)
        self.assertEqual(data["unselected"], 5)
        self.assertEqual(data["captured"] + data["incomplete"], data["selected"])

    def test_two_policies_qualify_the_same_behavior_differently(self):
        data = _counters(self.scenarios["policy-observe-data"])
        flow = _counters(self.scenarios["policy-observe-flow"])
        # Same behavior, different selection - and therefore different digests.
        self.assertEqual(data["intercepted"], flow["intercepted"])
        self.assertNotEqual(data["selected"], flow["selected"])
        self.assertNotEqual(policy_reference.digest(policy_reference.DECLARED_POLICIES["observe-data"]),
                            policy_reference.digest(policy_reference.DECLARED_POLICIES["observe-flow"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
