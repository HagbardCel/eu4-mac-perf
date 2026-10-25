"""The normalized behavior key keeps roles and drops raw bytes."""
from __future__ import annotations

import copy
import unittest

from oracle.certify.policy import behavior
from oracle.function import fixture, synthetic


def _record(scenario: str = "s03") -> dict:
    descriptor = fixture.load_descriptor()
    text = synthetic.capture_text(descriptor, scenario)
    return fixture.materialize_capture(text, descriptor)["record"]


def _set_span(record: dict, phase: str, name: str, data: bytes) -> None:
    span = next(item for item in record["state"][phase]
                if item["space"] == f"heap:{name}")
    if len(data) != len(bytes.fromhex(span["value_hex"])):
        raise AssertionError("the replacement must keep the span length")
    span["value_hex"] = data.hex()


def _set_ctx_byte(record: dict, phase: str, absolute: int, value: int) -> None:
    span = next(item for item in record["state"][phase] if item["space"] == "heap:ctx")
    data = bytearray.fromhex(span["value_hex"])
    data[absolute - int(span["offset"])] = value
    span["value_hex"] = data.hex()


def _set_carrier(record: dict, name: str, value: int) -> None:
    for carrier in record["abi"]["carriers"]:
        if carrier["name"] == name:
            carrier["value_hex"] = value.to_bytes(8, "big").hex()
            return
    raise AssertionError(f"missing carrier {name}")


class BehaviorKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.descriptor = fixture.load_descriptor()
        self.original = _record()
        self.key = behavior(self.original, self.descriptor).key

    def test_canary_contents_and_irrelevant_bytes_are_excluded(self) -> None:
        changed = copy.deepcopy(self.original)
        span = next(item for item in changed["state"]["pre"]
                    if item["space"] == "heap:canary-before")
        length = len(bytes.fromhex(span["value_hex"]))
        payload = bytes((index + 1) & 0xFF for index in range(length))
        _set_span(changed, "pre", "canary-before", payload)
        _set_span(changed, "post", "canary-before", payload)
        self.assertEqual(behavior(changed, self.descriptor).key, self.key)
        self.assertNotIn(payload.hex(), "=".join(behavior(changed, self.descriptor).key))

        flipped = copy.deepcopy(self.original)
        _set_ctx_byte(flipped, "pre", 220, 0x11)
        _set_ctx_byte(flipped, "post", 220, 0x11)
        self.assertEqual(behavior(flipped, self.descriptor).key, self.key)

    def test_pointer_role_and_alias_relationship_are_preserved(self) -> None:
        other = copy.deepcopy(self.original)
        layout = self.descriptor["layout"]
        pointer_at = layout["pointer_base"] + 2 * layout["slot_stride"]
        _set_ctx_byte(other, "post", pointer_at, 0x11)
        pointer_key = behavior(other, self.descriptor).key
        self.assertIn("pointer=other", pointer_key)
        self.assertNotEqual(pointer_key, self.key)

        aliased = copy.deepcopy(self.original)
        handle = int(aliased["raw"]["addresses"]["handle"], 16)
        aliased["raw"]["addresses"]["buffer"] = hex(handle)
        alias_key = behavior(aliased, self.descriptor).key
        self.assertIn("alias=handle", alias_key)
        self.assertNotEqual(alias_key, self.key)

        moved = copy.deepcopy(self.original)
        new_address = 0x410000000
        moved["raw"]["addresses"]["buffer"] = hex(new_address)
        _set_carrier(moved, "RSI", new_address)
        span = next(item for item in moved["state"]["post"] if item["space"] == "heap:ctx")
        data = bytearray.fromhex(span["value_hex"])
        begin = pointer_at - int(span["offset"])
        data[begin:begin + 8] = new_address.to_bytes(8, "little")
        span["value_hex"] = data.hex()
        self.assertEqual(behavior(moved, self.descriptor).key, self.key)
        self.assertNotIn(hex(new_address), behavior(moved, self.descriptor).key)


if __name__ == "__main__":
    unittest.main()
