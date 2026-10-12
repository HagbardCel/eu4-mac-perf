import sys
import unittest
from unittest.mock import patch

import thread_cpu_sampler


@unittest.skipUnless(sys.platform == "darwin", "thread CPU sampling requires macOS")
class ThreadCpuSamplerTests(unittest.TestCase):
    def test_poll_window_reports_backend_and_diagnostics(self) -> None:
        thread_cpu_sampler.set_session_backend("libproc")
        snapshot = thread_cpu_sampler.ThreadSnapshot(
            samples=[
                thread_cpu_sampler.ThreadSample(thread_id=1, user_us=1000, system_us=0),
            ],
            diagnostics={
                "threads_enumerated": 1,
                "threads_sampled_ok": 1,
                "threads_skipped_esrch": 0,
            },
        )
        snapshot2 = thread_cpu_sampler.ThreadSnapshot(
            samples=[
                thread_cpu_sampler.ThreadSample(thread_id=1, user_us=2000, system_us=0),
            ],
            diagnostics={
                "threads_enumerated": 1,
                "threads_sampled_ok": 1,
                "threads_skipped_esrch": 0,
            },
        )
        with patch.object(thread_cpu_sampler, "sample_snapshot", side_effect=[snapshot, snapshot2]):
            with patch.object(thread_cpu_sampler.time, "monotonic", side_effect=[0.0, 0.0, 0.1]):
                with patch.object(thread_cpu_sampler.time, "sleep"):
                    report = thread_cpu_sampler.poll_window(42, duration_s=0.05, interval_s=0.01)
        self.assertEqual(report["thread_cpu_backend"], "libproc")
        self.assertNotIn("vm_deallocate_task", report)
        self.assertEqual(len(report["poll_diagnostics"]), 2)
        self.assertGreaterEqual(report["observed_duration_s"], 0)

    def test_invalid_backend_raises(self) -> None:
        with patch.dict("os.environ", {"EU4_THREAD_CPU_BACKEND": "invalid"}):
            thread_cpu_sampler._SELECTED_BACKEND = None  # type: ignore[attr-defined]
            with self.assertRaises(Exception):
                thread_cpu_sampler.resolve_backend()


if __name__ == "__main__":
    unittest.main()
