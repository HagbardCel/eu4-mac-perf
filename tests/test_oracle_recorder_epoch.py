"""P4.8: R0-M protocol compatibility, validated offline.

The Oracle decodes the R0-M control word itself; these tests prove that decoding
agrees with the R0-M mirror field by field, that the protocol's ordering rule is
enforced, that a passive epoch captures nothing, and that a multi-epoch capture is
attributed consistently - all without launching anything.
"""
from __future__ import annotations

import unittest

import eu4_r0_control as r0_control

from oracle.recorder import epoch, schema

# A corpus that spans the protocol's edges: every mode, boundary generations and
# scenario ids, and words the protocol's own reader must reject.
_WORDS = [
    {"generation": 0, "mode": r0_control.EU4_R0_MODE_PASSIVE, "scenario_id": 0},
    {"generation": 1, "mode": r0_control.EU4_R0_MODE_CENSUS, "scenario_id": 0},
    {"generation": 2, "mode": r0_control.EU4_R0_MODE_CENSUS_CAPTURE, "scenario_id": 7},
    {"generation": 0xFFFFFFFF, "mode": r0_control.EU4_R0_MODE_CENSUS, "scenario_id": 0xFFFF},
    {"generation": 0, "mode": r0_control.EU4_R0_MODE_CENSUS_CAPTURE, "scenario_id": 1},
    {"generation": 3, "mode": 3, "scenario_id": 0},        # unknown mode
    {"generation": 3, "mode": 0xFF, "scenario_id": 0},     # unknown mode
]


def _payload(epoch_token: str, scenario_token: str) -> dict:
    return {
        "record_type": "observation",
        "provenance": {
            "module_sha256": "a" * 64,
            "function_id": "gfx:0x1",
            "callsite": "rva:0x1+0x2",
            "capture_policy_digest": "b" * 64,
            "scenario": scenario_token,
            "epoch": epoch_token,
            "thread": 0,
        },
        "invocation": {"hook_id": "hook:1", "call_id": "call:1", "parent_span": None,
                       "sequence": 0},
        "abi": {"calling_convention": "sysv-amd64", "carriers": [], "return_carrier": None,
                "confidence": "observed"},
        "state": {"pre": [], "post": [], "unknown_dependencies": []},
        "resources": [],
        "effects": {"ordered": [], "complete": True},
        "completeness": {"state": "captured", "losses": 0, "unavailable_dependencies": []},
        "fidelity": {"mode": "faithful", "policy_version": "p4-fid-v1"},
        "raw": {"blob_sha256": None},
    }


class DecoderCompatibilityTests(unittest.TestCase):
    def test_the_decoder_agrees_with_the_r0m_mirror_on_every_word(self):
        for fields in _WORDS:
            word = r0_control.pack_control_word(**fields)
            generation, mode, scenario_id, version, valid = r0_control.unpack_control_word(word)
            decoded = epoch.unpack_control_word(word)
            with self.subTest(**fields):
                self.assertEqual(decoded is not None, valid)
                if valid:
                    self.assertEqual(decoded,
                                     epoch.Epoch(generation, mode, scenario_id, version))

    def test_an_invalid_version_is_rejected_by_both_readers(self):
        word = r0_control.pack_control_word(generation=1, mode=r0_control.EU4_R0_MODE_CENSUS,
                                            scenario_id=0, version=2)
        _, _, _, _, valid = r0_control.unpack_control_word(word)
        self.assertFalse(valid)
        self.assertIsNone(epoch.unpack_control_word(word))

    def test_a_passive_epoch_captures_nothing(self):
        word = r0_control.pack_control_word(generation=0, mode=r0_control.EU4_R0_MODE_PASSIVE,
                                            scenario_id=0)
        decoded = epoch.unpack_control_word(word)
        self.assertFalse(decoded.capture_enabled)
        self.assertTrue(epoch.unpack_control_word(
            r0_control.pack_control_word(generation=0, mode=r0_control.EU4_R0_MODE_CENSUS,
                                         scenario_id=0)).capture_enabled)


class SequenceTests(unittest.TestCase):
    def test_generations_must_strictly_increase(self):
        words = [r0_control.pack_control_word(generation=5, mode=1, scenario_id=0),
                 r0_control.pack_control_word(generation=5, mode=1, scenario_id=0)]
        with self.assertRaises(epoch.EpochError):
            epoch.epochs(words)

    def test_an_invalid_word_is_rejected_rather_than_attributed(self):
        with self.assertRaises(epoch.EpochError):
            epoch.epochs([0x0200000000000000])


class AttributionTests(unittest.TestCase):
    def setUp(self):
        # passive -> census -> census-capture: the multi-epoch protocol, offline.
        self.words = [
            r0_control.pack_control_word(generation=1, mode=r0_control.EU4_R0_MODE_PASSIVE,
                                         scenario_id=0),
            r0_control.pack_control_word(generation=2, mode=r0_control.EU4_R0_MODE_CENSUS,
                                         scenario_id=4),
            r0_control.pack_control_word(generation=3,
                                         mode=r0_control.EU4_R0_MODE_CENSUS_CAPTURE,
                                         scenario_id=4),
        ]
        self.census = epoch.Epoch(2, r0_control.EU4_R0_MODE_CENSUS, 4, 1)
        self.capture = epoch.Epoch(3, r0_control.EU4_R0_MODE_CENSUS_CAPTURE, 4, 1)

    def test_a_valid_multi_epoch_capture_is_attributed(self):
        records = [{"provenance": {"epoch": self.census.token, "scenario": self.census.scenario_token}},
                   {"provenance": {"epoch": self.capture.token,
                                   "scenario": self.capture.scenario_token}},
                   {"provenance": {"epoch": self.capture.token,
                                   "scenario": self.capture.scenario_token}}]
        report = epoch.check_compatibility(self.words, records)
        self.assertEqual(report["records"], 3)
        self.assertEqual(report["per_epoch"][self.census.token], 1)
        self.assertEqual(report["per_epoch"][self.capture.token], 2)
        self.assertEqual([entry["capture_enabled"] for entry in report["epochs"]],
                         [False, True, True])

    def test_a_record_in_a_passive_epoch_is_rejected(self):
        passive = epoch.Epoch(1, r0_control.EU4_R0_MODE_PASSIVE, 0, 1)
        with self.assertRaises(epoch.EpochError):
            epoch.check_compatibility(
                self.words, [{"provenance": {"epoch": passive.token,
                                             "scenario": passive.scenario_token}}])

    def test_a_record_naming_an_unknown_epoch_is_rejected(self):
        with self.assertRaises(epoch.EpochError):
            epoch.check_compatibility(
                self.words, [{"provenance": {"epoch": "r0m-v1-g99", "scenario": "r0m-s4"}}])

    def test_a_record_with_the_wrong_scenario_is_rejected(self):
        with self.assertRaises(epoch.EpochError):
            epoch.check_compatibility(
                self.words, [{"provenance": {"epoch": self.census.token,
                                             "scenario": "r0m-s9"}}])


class SchemaIntegrationTests(unittest.TestCase):
    def test_epoch_tokens_are_valid_record_provenance(self):
        word = r0_control.pack_control_word(generation=9, mode=r0_control.EU4_R0_MODE_CENSUS,
                                            scenario_id=12)
        decoded = epoch.unpack_control_word(word)
        sealed = schema.seal(_payload(decoded.token, decoded.scenario_token))
        schema.verify(sealed)  # raises unless the epoch tokens are valid provenance
        self.assertEqual(sealed["payload"]["provenance"]["epoch"], "r0m-v1-g9")
        self.assertEqual(sealed["payload"]["provenance"]["scenario"], "r0m-s12")
        report = epoch.check_compatibility([word], [sealed])
        self.assertEqual(report["per_epoch"]["r0m-v1-g9"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
