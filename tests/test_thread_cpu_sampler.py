import unittest
from unittest.mock import patch

import thread_cpu_sampler


class ThreadCpuSamplerTests(unittest.TestCase):
    def test_poll_window_reports_self_task_dealloc(self) -> None:
        samples = [
            thread_cpu_sampler.ThreadSample(thread_id=1, user_us=1000, system_us=0),
            thread_cpu_sampler.ThreadSample(thread_id=1, user_us=2000, system_us=0),
        ]
        with patch.object(thread_cpu_sampler, "sample_threads", side_effect=[samples[:1], samples[1:]]):
            with patch.object(thread_cpu_sampler.time, "monotonic", side_effect=[0.0, 0.0, 0.1]):
                with patch.object(thread_cpu_sampler.time, "sleep"):
                    report = thread_cpu_sampler.poll_window(42, duration_s=0.05, interval_s=0.01)
        self.assertEqual(report["vm_deallocate_task"], "mach_task_self_")
        self.assertGreaterEqual(report["observed_duration_s"], 0)


if __name__ == "__main__":
    unittest.main()
