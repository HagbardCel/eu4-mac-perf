"""P4.1: the observation-record schema, its canonicalization and its two digests.

Offline and adversarial: these tests build records by hand and prove the three
rules P4.1 exists to enforce, before any native recorder exists.

* A `semantic_digest` is defined only for a *fully qualified* observation; an
  incomplete or counted-only record may be integrity-checked but never semantically
  deduplicated.
* An unknown byte is masked, never zero-filled: a known zero and an unknown byte
  are different observations, and the unknown byte's contents never leak into
  semantics.
* A resource's lifetime (`resource_id@generation`) is distinct from its content
  version, and both are preserved.
"""
from __future__ import annotations

import copy
import unittest

from oracle.recorder import schema


def _payload() -> dict:
    """A minimal well-formed, fully qualified observation."""
    return {
        "record_type": "observation",
        "provenance": {
            "module_sha256": "a" * 64,
            "function_id": "gfx:0x15ec36a",
            "callsite": "rva:0x15ec36a+0x2a",
            "capture_policy_digest": "b" * 64,
            "scenario": "venice-paused",
            "epoch": "epoch-a",
            "thread": 0,
        },
        "invocation": {
            "hook_id": "hook:gfx-setcb",
            "call_id": "call:1",
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
            "pre": [{"space": "heap:ctx", "offset": 296, "value_hex": "01",
                     "valid_mask": "ff", "confidence": "observed"}],
            "post": [{"space": "heap:ctx", "offset": 296, "value_hex": "00",
                      "valid_mask": "ff", "confidence": "observed"}],
            "unknown_dependencies": [],
        },
        "resources": [{"resource_id": "buf:1", "generation": 0, "version": 3,
                       "contents_sha256": "c" * 64, "aliases": []}],
        "effects": {"ordered": [{"op": "glBindBuffer", "args": ["8892", "01"]}],
                    "complete": True},
        "completeness": {"state": "captured", "losses": 0, "unavailable_dependencies": []},
        "fidelity": {"mode": "faithful", "policy_version": "p4-fid-v1"},
        "raw": {"blob_sha256": None},
    }


class CanonicalizationTests(unittest.TestCase):
    def test_valid_payload_seals_and_verifies(self):
        sealed = schema.seal(_payload())
        schema.verify(sealed)
        self.assertEqual(sealed["schema_version"], schema.RECORD_SCHEMA_VERSION)
        self.assertEqual(sealed["record_sha256"], schema.record_digest(_payload()))

    def test_canonical_bytes_is_key_order_independent(self):
        first = {"b": 1, "a": {"y": 2, "x": 3}}
        second = {"a": {"x": 3, "y": 2}, "b": 1}
        self.assertEqual(schema.canonical_bytes(first), schema.canonical_bytes(second))

    def test_canonical_bytes_rejects_non_finite(self):
        with self.assertRaises(schema.SchemaError):
            schema.canonical_bytes({"x": float("nan")})

    def test_rejects_unsupported_schema_version(self):
        sealed = schema.seal(_payload())
        sealed["schema_version"] = "eu4-oracle-record/zzz"
        with self.assertRaises(schema.SchemaError):
            schema.verify(sealed)

    def test_seal_verify_detects_payload_tamper(self):
        sealed = schema.seal(_payload())
        sealed["payload"]["fidelity"]["mode"] = "gl-optimization"
        with self.assertRaises(schema.SchemaError):
            schema.verify(sealed)


class ValidationTests(unittest.TestCase):
    def test_rejects_unknown_fidelity_mode(self):
        payload = _payload()
        payload["fidelity"]["mode"] = "best-effort"
        with self.assertRaises(schema.SchemaError):
            schema.validate_payload(payload)

    def test_rejects_unknown_capture_state(self):
        payload = _payload()
        payload["completeness"]["state"] = "partial"
        with self.assertRaises(schema.SchemaError):
            schema.validate_payload(payload)

    def test_rejects_mask_length_mismatch(self):
        payload = _payload()
        payload["state"]["pre"][0]["valid_mask"] = "ffff"
        with self.assertRaises(schema.SchemaError):
            schema.validate_payload(payload)

    def test_rejects_odd_length_hex(self):
        payload = _payload()
        payload["state"]["pre"][0]["value_hex"] = "0"
        with self.assertRaises(schema.SchemaError):
            schema.validate_payload(payload)

    def test_rejects_boolean_where_integer_expected(self):
        payload = _payload()
        payload["provenance"]["thread"] = True
        with self.assertRaises(schema.SchemaError):
            schema.validate_payload(payload)


class SemanticQualificationTests(unittest.TestCase):
    """Rule 1: only a fully qualified observation may be semantically digested."""

    def _disqualifiers(self) -> list[tuple[str, dict]]:
        cases = []
        for state in ("incomplete", "counted-only"):
            payload = _payload()
            payload["completeness"]["state"] = state
            cases.append((f"completeness.state={state}", payload))
        payload = _payload()
        payload["completeness"]["losses"] = 1
        cases.append(("completeness.losses=1", payload))
        payload = _payload()
        payload["completeness"]["unavailable_dependencies"] = ["global:frame-clock"]
        cases.append(("unavailable_dependencies", payload))
        payload = _payload()
        payload["state"]["unknown_dependencies"] = ["global:GfxContext"]
        cases.append(("state.unknown_dependencies", payload))
        payload = _payload()
        payload["effects"]["complete"] = False
        cases.append(("effects.complete=False", payload))
        return cases

    def test_unqualified_records_have_no_semantic_digest(self):
        for label, payload in self._disqualifiers():
            with self.subTest(label=label):
                self.assertFalse(schema.qualifies_for_semantic(payload))
                with self.assertRaises(schema.SchemaError):
                    schema.semantic_digest(payload)

    def test_unqualified_records_still_have_an_integrity_digest(self):
        for label, payload in self._disqualifiers():
            with self.subTest(label=label):
                self.assertEqual(len(schema.record_digest(payload)), 64)

    def test_qualified_record_has_both_digests(self):
        payload = _payload()
        self.assertTrue(schema.qualifies_for_semantic(payload))
        self.assertNotEqual(schema.record_digest(payload), schema.semantic_digest(payload))


class UnknownByteTests(unittest.TestCase):
    """Rule 2: an unknown byte is masked, never zero-filled."""

    def _with_span(self, value_hex: str, valid_mask: str) -> dict:
        payload = _payload()
        payload["state"]["pre"][0] = {"space": "heap:ctx", "offset": 296,
                                      "value_hex": value_hex, "valid_mask": valid_mask,
                                      "confidence": "observed"}
        return payload

    def test_known_zero_and_unknown_byte_differ(self):
        known_zero = self._with_span("00", "ff")
        unknown = self._with_span("00", "00")
        self.assertNotEqual(schema.semantic_digest(known_zero), schema.semantic_digest(unknown))

    def test_projection_preserves_the_unknown_as_a_mask(self):
        projection = schema.semantic_projection(self._with_span("00", "00"))
        span = projection["state"]["pre"][0]
        self.assertEqual(span["valid_mask"], "00")
        self.assertEqual(span["known_hex"], "")
        self.assertNotIn("value_hex", span)

    def test_unknown_byte_contents_do_not_reach_semantics(self):
        unreadable_a = self._with_span("00", "00")
        unreadable_b = self._with_span("ff", "00")
        self.assertEqual(schema.semantic_digest(unreadable_a), schema.semantic_digest(unreadable_b))

    def test_partial_mask_semantics(self):
        # Two known bytes, one unknown: only the known bytes contribute to semantics.
        a = self._with_span("aabbcc", "ffff00")
        b = self._with_span("aabbdd", "ffff00")
        self.assertEqual(schema.semantic_digest(a), schema.semantic_digest(b))
        c = self._with_span("aabfcc", "ffffff")
        self.assertNotEqual(schema.semantic_digest(a), schema.semantic_digest(c))


class ResourceIdentityTests(unittest.TestCase):
    """Rule 3: lifetime (`id@generation`) is distinct from content version."""

    def test_version_distinguishes_state_of_one_lifetime(self):
        a = _payload()
        b = copy.deepcopy(a)
        b["resources"][0]["version"] = 4
        self.assertNotEqual(schema.semantic_digest(a), schema.semantic_digest(b))

    def test_generation_distinguishes_lifetimes(self):
        a = _payload()
        b = copy.deepcopy(a)
        b["resources"][0]["generation"] = 1
        self.assertNotEqual(schema.semantic_digest(a), schema.semantic_digest(b))

    def test_identical_records_are_semantically_equal(self):
        self.assertEqual(schema.semantic_digest(_payload()), schema.semantic_digest(_payload()))


class OccurrenceMetadataTests(unittest.TestCase):
    """Occurrence metadata is excluded from semantics; observation scope is not."""

    def test_occurrence_metadata_does_not_change_semantics(self):
        base = _payload()
        moved = copy.deepcopy(base)
        moved["provenance"]["callsite"] = "rva:0x15ec36a+0x40"
        moved["provenance"]["scenario"] = "venice-running"
        moved["provenance"]["epoch"] = "epoch-b"
        moved["provenance"]["thread"] = 3
        moved["invocation"] = {"hook_id": "hook:alt", "call_id": "call:999",
                               "parent_span": "span:7", "sequence": 42}
        moved["private"] = {"raw_addresses": ["0x7ffee000", "0x7ffee010"]}
        self.assertEqual(schema.semantic_digest(base), schema.semantic_digest(moved))
        self.assertNotEqual(schema.record_digest(base), schema.record_digest(moved))

    def test_projection_omits_occurrence_and_private_fields(self):
        projection = schema.semantic_projection(_payload())
        self.assertNotIn("invocation", projection)
        self.assertNotIn("private", projection)
        self.assertEqual(set(projection["provenance"]),
                         {"module_sha256", "function_id", "capture_policy_digest"})

    def test_observation_scope_does_change_semantics(self):
        base = _payload()
        for mutate in (
            lambda p: p["provenance"].__setitem__("module_sha256", "d" * 64),
            lambda p: p["provenance"].__setitem__("capture_policy_digest", "e" * 64),
            lambda p: p["provenance"].__setitem__("function_id", "gfx:0xdeadbeef"),
            lambda p: p["fidelity"].__setitem__("mode", "backend-redesign"),
        ):
            with self.subTest():
                other = copy.deepcopy(base)
                mutate(other)
                self.assertNotEqual(schema.semantic_digest(base), schema.semantic_digest(other))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
