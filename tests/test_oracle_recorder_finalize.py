"""P4.5: offline finalization - resource identity, aliasing and semantic dedup.

The recorder counts; it does not decide equality. These tests prove the offline
classifier: occurrence metadata is excluded so the *same* behavior observed twice
deduplicates, equal arguments over a *different* pre-state do not, resource
identity follows the lifetime rather than the handle, and the capture accounting
identity is asserted rather than assumed.
"""
from __future__ import annotations

import unittest

from oracle.recorder import finalize, schema


def _payload(*, call_id="call:1", thread=0, epoch="epoch-a", scenario="scenario-a",
             function_id="gfx:0x15ec36a", pre="01", post="00", resources=None,
             state="captured", losses=0, unknown=(), effects_complete=True, gains=None):
    return {
        "record_type": "observation",
        "provenance": {
            "module_sha256": "a" * 64,
            "function_id": function_id,
            "callsite": "rva:0x15ec36a+0x2a",
            "capture_policy_digest": "b" * 64,
            "scenario": scenario,
            "epoch": epoch,
            "thread": thread,
        },
        "invocation": {
            "hook_id": "hook:gfx-setcb",
            "call_id": call_id,
            "parent_span": None,
            "sequence": 0,
        },
        "abi": {
            "calling_convention": "sysv-amd64",
            "carriers": [{"name": "rdi", "kind": "int", "bits": 64,
                          "value_hex": "0000000000000001", "confidence": "observed"}],
            "return_carrier": None,
            "confidence": "observed",
        },
        "state": {
            "pre": [{"space": "heap:ctx", "offset": 296, "value_hex": pre,
                     "valid_mask": "ff", "confidence": "observed"}],
            "post": [{"space": "heap:ctx", "offset": 296,
                      "value_hex": gains if gains is not None else post,
                      "valid_mask": "ff", "confidence": "observed"}],
            "unknown_dependencies": list(unknown),
        },
        "resources": resources if resources is not None else [
            {"resource_id": "buf:1", "generation": 0, "version": 3,
             "contents_sha256": "c" * 64, "aliases": []}],
        "effects": {"ordered": [], "complete": effects_complete},
        "completeness": {"state": state, "losses": losses, "unavailable_dependencies": []},
        "fidelity": {"mode": "faithful", "policy_version": "p4-fid-v1"},
        "raw": {"blob_sha256": None},
    }


def _record(**kwargs) -> dict:
    return schema.seal(_payload(**kwargs))


def _counters(intercepted=0, unselected=0, captured=0, incomplete=0, lost=0) -> dict:
    return {"intercepted": intercepted, "unselected": unselected, "captured": captured,
            "incomplete": incomplete, "lost": lost}


class DeduplicationTests(unittest.TestCase):
    def test_the_same_behavior_observed_twice_is_one_unique_and_one_repeat(self):
        records = [_record(call_id="call:1", thread=0, epoch="epoch-a", scenario="s"),
                   _record(call_id="call:2", thread=3, epoch="epoch-b", scenario="t")]
        ledger = finalize.classify(records, _counters(intercepted=2, captured=2))
        self.assertEqual(ledger["unique"], 1)
        self.assertEqual(ledger["verified_repeat"], 1)

    def test_equal_arguments_over_a_different_pre_state_are_not_deduplicated(self):
        first = _record(call_id="call:1", pre="01", post="00")
        second = _record(call_id="call:2", pre="ff", post="00")
        ledger = finalize.classify([first, second], _counters(intercepted=2, captured=2))
        self.assertEqual(ledger["unique"], 2)
        self.assertEqual(ledger["verified_repeat"], 0)

    def test_a_different_function_is_not_the_same_behavior(self):
        first = _record(function_id="gfx:0x1")
        second = _record(function_id="gfx:0x2")
        ledger = finalize.classify([first, second], _counters(intercepted=2, captured=2))
        self.assertEqual(ledger["unique"], 2)

    def test_an_unknown_input_disqualifies_a_record(self):
        record = _record(unknown=("dep:missing",))
        with self.assertRaises(finalize.FinalizeError):
            finalize.classify([record], _counters(intercepted=1, captured=1))


class ResourceIdentityTests(unittest.TestCase):
    def _resources(self, aliases, generation=0, version=3):
        return [{"resource_id": "buf:1", "generation": generation, "version": version,
                 "contents_sha256": "c" * 64, "aliases": aliases}]

    def test_two_handles_onto_one_resource_collapse_to_one_identity(self):
        original = _record(resources=self._resources([]))
        alias = _record(resources=self._resources(
            [{"resource_id": "buf:1", "generation": 0}]))
        observed = finalize.observed_resources([original, alias])
        self.assertEqual(list(observed), ["buf:1@0"])
        self.assertEqual(observed["buf:1@0"]["aliases"], ["buf:1@0"])

    def test_a_reused_slot_in_a_new_generation_is_a_new_resource(self):
        first = _record(resources=self._resources([], generation=0))
        second = _record(resources=self._resources([], generation=1))
        observed = finalize.observed_resources([first, second])
        self.assertEqual(sorted(observed), ["buf:1@0", "buf:1@1"])

    def test_contents_version_is_preserved_apart_from_lifetime(self):
        version_three = _record(resources=self._resources([], version=3))
        version_four = _record(resources=self._resources([], version=4))
        observed = finalize.observed_resources([version_three, version_four])
        self.assertEqual(observed["buf:1@0"]["versions"], [3, 4])


class AccountingTests(unittest.TestCase):
    def test_the_identity_holds_with_incomplete_and_lost_observations(self):
        records = [_record(state="captured"), _record(call_id="call:2", state="incomplete")]
        ledger = finalize.classify(records, _counters(intercepted=3, captured=1,
                                                      incomplete=1, lost=1))
        self.assertEqual(ledger["unique"], 1)
        self.assertEqual(ledger["counted_only"], 0)
        self.assertEqual(ledger["lost"], 1)

    def test_a_broken_identity_is_asserted_not_assumed(self):
        with self.assertRaises(finalize.FinalizeError):
            finalize.classify([_record()], _counters(intercepted=5, captured=1))

    def test_a_counted_only_record_must_not_be_materialized(self):
        with self.assertRaises(finalize.FinalizeError):
            finalize.classify([_record(state="counted-only")], _counters(intercepted=1))

    def test_sub_observation_loss_in_a_materialized_record_is_rejected(self):
        with self.assertRaises(finalize.FinalizeError):
            finalize.classify([_record(losses=1)], _counters(intercepted=1, captured=1))

    def test_missing_counters_are_rejected(self):
        with self.assertRaises(finalize.FinalizeError):
            finalize.classify([], {"intercepted": 0})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
