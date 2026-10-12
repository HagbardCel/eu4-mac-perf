import unittest

from cpu_delta import accumulate_thread_deltas, split_main_thread_cpu


class CpuDeltaTests(unittest.TestCase):
    def test_accumulate_mid_window_thread(self) -> None:
        polls = [
            {1: 1000, 2: 500},
            {1: 2000, 2: 800, 3: 100},
            {1: 3000, 2: 1100, 3: 400},
        ]
        accumulated, appeared, disappeared = accumulate_thread_deltas(polls)
        self.assertEqual(accumulated[1], 2000)
        self.assertEqual(accumulated[2], 600)
        self.assertEqual(accumulated[3], 300)
        self.assertEqual(appeared, 3)
        self.assertEqual(disappeared, 0)

    def test_split_main_thread_verified(self) -> None:
        accumulated = {10: 1000, 11: 500}
        main, non_main, status = split_main_thread_cpu(accumulated, 10)
        self.assertEqual(status, "verified")
        self.assertEqual(main, 1000.0)
        self.assertEqual(non_main, 500.0)


if __name__ == "__main__":
    unittest.main()
