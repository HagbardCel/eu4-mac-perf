import unittest

from eu4_r0_control import (
    EU4_R0_CONTROL_VERSION,
    pack_control_snapshot,
    pack_control_word,
    unpack_control_snapshot,
    unpack_control_word,
)


class R0ControlTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        payload = pack_control_snapshot(generation=7, mode=1, scenario_id=3)
        generation, mode, scenario_id, version = unpack_control_snapshot(payload)
        self.assertEqual(version, EU4_R0_CONTROL_VERSION)
        self.assertEqual((generation, mode, scenario_id), (7, 1, 3))

    def test_version_zero_invalid(self) -> None:
        _, _, _, version, valid = unpack_control_word(1)
        self.assertEqual(version, 0)
        self.assertFalse(valid)


if __name__ == "__main__":
    unittest.main()
