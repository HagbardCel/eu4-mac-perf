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

from oracle.recorder import finalize, harness, policy_reference


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
        self.assertEqual(sorted(self.scenarios),
                         ["bounded", "concurrent-ample", "concurrent-bounded",
                          "concurrent-mixed", "disabled", "enabled", "misuse",
                          "policy-observe-data", "policy-observe-flow", "reentrant"])

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

    def test_the_subject_effects_total_is_exact(self):
        # Five single-threaded scripts (7 effects each) plus three concurrent runs of
        # four threads (7 effects per thread): 35 + 3*28.
        self.assertEqual(int(self.parsed["end"]["effects"]), 119)

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

    # --- P4.7: concurrency and bounded capture -----------------------------------

    def test_concurrent_accounting_is_exact(self):
        ample = _counters(self.scenarios["concurrent-ample"])
        self.assertEqual(ample["intercepted"], 32)
        self.assertEqual(ample["selected"], 32)
        self.assertEqual(ample["captured"], 32)
        self.assertEqual(ample["lost"], 0)
        self.assertEqual(ample["rejected"], 0)

    def test_bounded_concurrent_capture_reports_exact_loss(self):
        bounded = _counters(self.scenarios["concurrent-bounded"])
        self.assertEqual(bounded["intercepted"], 32)
        self.assertEqual(bounded["captured"] + bounded["incomplete"] + bounded["lost"],
                         bounded["selected"])
        self.assertEqual(bounded["captured"], 12)
        self.assertEqual(bounded["lost"], 20)

    def test_concurrent_capture_marks_incomplete_observations(self):
        mixed = _counters(self.scenarios["concurrent-mixed"])
        self.assertEqual(mixed["intercepted"], 40)
        self.assertEqual(mixed["captured"], 32)
        self.assertEqual(mixed["incomplete"], 8)

    def test_every_captured_event_is_a_distinct_thread_sequence(self):
        for name in ("concurrent-ample", "concurrent-mixed"):
            events = self.scenarios[name]["events"]
            with self.subTest(scenario=name):
                self.assertEqual(len(events), self.scenarios[name]["event_count"])
                self.assertEqual(len({(e["thread_id"], e["sequence"]) for e in events}),
                                 len(events))
                self.assertEqual({e["thread_id"] for e in events}, {0, 1, 2, 3})

    def test_every_thread_has_a_contiguous_sequence(self):
        for name in ("concurrent-ample", "concurrent-mixed"):
            by_thread: dict[int, list[int]] = {}
            for event in self.scenarios[name]["events"]:
                by_thread.setdefault(event["thread_id"], []).append(event["sequence"])
            with self.subTest(scenario=name):
                for thread, sequences in sorted(by_thread.items()):
                    self.assertEqual(sequences, list(range(1, len(sequences) + 1)),
                                     msg=f"thread {thread}")

    def test_the_declared_sync_edge_orders_the_two_phases(self):
        events = self.scenarios["concurrent-ample"]["events"]
        preds = finalize.partial_order(events, self.scenarios["concurrent-ample"]["syncs"])
        self.assertTrue(finalize.is_acyclic(preds))
        phase0 = [i for i, e in enumerate(events) if e["phase"] == 0]
        phase1 = [i for i, e in enumerate(events) if e["phase"] == 1]
        for later in phase1:
            for earlier in phase0:
                self.assertIn(earlier, preds[later])

    def test_the_reconstructed_order_is_partial_not_total(self):
        events = self.scenarios["concurrent-ample"]["events"]
        preds = finalize.partial_order(events, self.scenarios["concurrent-ample"]["syncs"])
        first = next(i for i, e in enumerate(events) if e["phase"] == 0 and e["thread_id"] == 0)
        other = next(i for i, e in enumerate(events) if e["phase"] == 0 and e["thread_id"] == 1)
        # Two same-phase events on different threads are genuinely unordered.
        self.assertNotIn(other, preds[first])
        self.assertNotIn(first, preds[other])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
