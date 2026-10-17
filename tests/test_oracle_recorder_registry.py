"""P4.6: the offline resource registry and closure verifier.

A resource is named by its lifetime, never by a bare handle. These tests prove the
rule the phase exists for - a missing resource yields ``INCOMPLETE_ORACLE`` instead
of being silently attributed to whatever now occupies its slot - first on hand-built
references, then end-to-end over the recorder's own native capture.
"""
from __future__ import annotations

import unittest

from oracle.recorder import harness, registry


class ReferenceTests(unittest.TestCase):
    def test_a_live_reference_resolves_to_its_lifetime(self):
        reference = registry.reference_from_context([1, 2, 5, 1, 5, 9])
        self.assertTrue(reference.resolved)
        self.assertEqual(reference.named, "slot:2@5")
        self.assertEqual(registry.lifetime_of(reference), "slot:2@5")

    def test_a_reference_to_a_destroyed_lifetime_is_incomplete_oracle(self):
        reference = registry.reference_from_context([1, 0, 0, 0, 1, 2])
        self.assertFalse(reference.resolved)
        self.assertEqual(reference.named, "slot:0@0")
        self.assertEqual(registry.lifetime_of(reference), registry.INCOMPLETE_ORACLE)

    def test_a_reused_slot_does_not_resolve_a_stale_generation(self):
        # The slot is live again, but in a new generation: the old handle names a
        # resource that is gone, not the one that now occupies the slot.
        reference = registry.reference_from_context([1, 0, 0, 1, 1, 0])
        self.assertFalse(reference.resolved)
        self.assertEqual(registry.lifetime_of(reference), registry.INCOMPLETE_ORACLE)

    def test_an_invalid_reference_names_nothing_at_all(self):
        reference = registry.reference_from_context([0, 0, 0, 0, 0, 0])
        self.assertFalse(reference.resolved)
        self.assertFalse(reference.valid)

    def test_two_aliases_of_one_resource_share_a_lifetime(self):
        original = registry.reference_from_context([1, 0, 0, 1, 0, 1])
        alias = registry.reference_from_context([1, 0, 0, 1, 0, 1])
        self.assertEqual(registry.lifetime_of(original), registry.lifetime_of(alias))

    def test_a_context_of_the_wrong_width_is_rejected(self):
        with self.assertRaises(registry.RegistryError):
            registry.reference_from_context([1, 2, 3])


class ClosureTests(unittest.TestCase):
    def test_a_gone_resource_yields_incomplete_oracle(self):
        events = [{"complete": 1, "context": [1, 0, 0, 1, 0, 1]},
                  {"complete": 0, "context": [1, 0, 0, 0, 1, 2]}]
        summary = registry.verify_closure(events)
        self.assertEqual(summary["complete"], 1)
        self.assertEqual(summary["incomplete"], 1)
        self.assertEqual(summary["lifetimes"], ["slot:0@0"])
        self.assertEqual(summary["unavailable_dependencies"], ["slot:0@0"])
        self.assertEqual(summary["incomplete_oracle"], ["slot:0@0"])

    def test_an_unreferenced_observation_is_incomplete_without_a_dependency(self):
        summary = registry.verify_closure([{"complete": 0, "context": [0, 0, 0, 0, 0, 0]}])
        self.assertEqual(summary["incomplete"], 1)
        self.assertEqual(summary["unreferenced"], 1)
        self.assertEqual(summary["unavailable_dependencies"], [])

    def test_a_complete_observation_must_address_a_live_resource(self):
        with self.assertRaises(registry.RegistryError):
            registry.verify_closure([{"complete": 1, "context": [1, 0, 0, 0, 1, 2]}])

    def test_an_incomplete_observation_may_still_address_a_live_resource(self):
        # A bad argument on a live resource: it resolves, but the call still failed.
        summary = registry.verify_closure([{"complete": 0, "context": [1, 0, 0, 1, 0, 2]}])
        self.assertEqual(summary["lifetimes"], ["slot:0@0"])
        self.assertEqual(summary["incomplete"], 1)
        self.assertEqual(summary["unavailable_dependencies"], [])

    def test_verdicts_name_the_dependency_of_each_unresolved_observation(self):
        verdicts = registry.verdicts([{"context": [1, 0, 0, 0, 1, 2]},
                                      {"context": [0, 0, 0, 0, 0, 0]}])
        self.assertEqual(verdicts[0]["unavailable_dependency"], "slot:0@0")
        self.assertIsNone(verdicts[1]["unavailable_dependency"])
        self.assertEqual(verdicts[1]["lifetime"], registry.INCOMPLETE_ORACLE)


class NativeClosureTests(unittest.TestCase):
    """The registry, run over the recorder's own committed golden capture."""

    @classmethod
    def setUpClass(cls):
        parsed = harness.parse_recorder_transcript(harness.reproduce("recorder"))
        cls.scenarios = {scenario["name"]: scenario for scenario in parsed["scenarios"]}

    def test_a_fully_captured_concurrent_scenario_is_closed(self):
        summary = registry.verify_closure(self.scenarios["concurrent-ample"]["events"])
        self.assertEqual(summary["complete"], 32)
        self.assertEqual(summary["incomplete"], 0)
        self.assertEqual(summary["lifetimes"], ["slot:0@0"])

    def test_a_capture_with_a_gone_resource_marks_incomplete_oracle(self):
        summary = registry.verify_closure(self.scenarios["concurrent-mixed"]["events"])
        self.assertEqual(summary["complete"], 32)
        self.assertEqual(summary["incomplete"], 8)
        self.assertEqual(summary["unreferenced"], 4)
        self.assertEqual(summary["unavailable_dependencies"], ["slot:0@0"])
        # A slot reused in a new generation is a *different* lifetime, and it closes.
        self.assertEqual(summary["lifetimes"], ["slot:0@0", "slot:0@1"])

    def test_every_incomplete_observation_in_the_capture_is_explained(self):
        for name in ("concurrent-ample", "concurrent-mixed"):
            events = self.scenarios[name]["events"]
            verdicts = registry.verdicts(events)
            with self.subTest(scenario=name):
                self.assertEqual(len(verdicts), len(events))
                for event, verdict in zip(events, verdicts):
                    if event["complete"]:
                        self.assertNotEqual(verdict["lifetime"], registry.INCOMPLETE_ORACLE)
                    if verdict["lifetime"] == registry.INCOMPLETE_ORACLE:
                        self.assertEqual(event["complete"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
