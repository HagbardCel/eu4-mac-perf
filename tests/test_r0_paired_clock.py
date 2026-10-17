import unittest

from analysis.tools.frame_evolution_analyze import clock_alignment_interval


class PairedClockTests(unittest.TestCase):
    def test_interval_containment_passes(self) -> None:
        result = clock_alignment_interval(0, 60_000_000, 120_000_000, tolerance_ns=50_000_000)
        self.assertEqual(result["status"], "passed")

    def test_interval_containment_fails_outside(self) -> None:
        result = clock_alignment_interval(0, 200_000_000, 120_000_000, tolerance_ns=50_000_000)
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
