import unittest

from eu4_r0_control import EU4_R0_CONTROL_MAGIC, pack_control_snapshot, unpack_control_snapshot


class R0ControlTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        payload = pack_control_snapshot(generation=7, mode=1, scenario_id=3)
        generation, mode, scenario_id, magic = unpack_control_snapshot(payload)
        self.assertEqual(magic, EU4_R0_CONTROL_MAGIC)
        self.assertEqual((generation, mode, scenario_id), (7, 1, 3))


if __name__ == "__main__":
    unittest.main()
