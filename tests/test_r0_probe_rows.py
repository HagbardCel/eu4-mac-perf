import tempfile
import unittest
from pathlib import Path

import r0_probe_rows


class R0ProbeRowsTests(unittest.TestCase):
    def test_seven_field_swap_record(self) -> None:
        anchor = {"monotonic_ns": 1_000_000_000, "wall_ns": 2_000_000_000}
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("S,100,200,1,1000000000,60,58\n")
            path = Path(handle.name)
        rows = r0_probe_rows.r0_probe_rows(path, anchor)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["swaps"], 60)
        self.assertEqual(rows[0]["paused_swaps"], 58)
        self.assertAlmostEqual(rows[0]["swaps_s"], 60.0)
        path.unlink()


if __name__ == "__main__":
    unittest.main()
