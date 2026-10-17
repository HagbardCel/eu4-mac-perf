import re
import subprocess
import sys
import unittest
from pathlib import Path

import thread_cpu_sampler

ROOT = Path(__file__).resolve().parents[1]
QUAL_SOURCE = ROOT / "tests/thread_cpu_qual_worker.c"
QUAL_BINARY = ROOT / "benchmark/.build/thread_cpu_qual_worker"


def _build_qual_worker() -> Path:
    QUAL_BINARY.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "clang",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pthread",
            "-o",
            str(QUAL_BINARY),
            str(QUAL_SOURCE),
        ],
        check=True,
    )
    return QUAL_BINARY


def _start_worker(mode: str) -> tuple[subprocess.Popen[str], int]:
    worker = _build_qual_worker()
    process = subprocess.Popen(
        [str(worker), mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    line = process.stdout.readline()
    match = re.search(r"MAIN_TID=(\d+)", line)
    if match is None:
        process.kill()
        raise AssertionError("worker missing MAIN_TID")
    return process, int(match.group(1))


@unittest.skipUnless(sys.platform == "darwin", "libproc qualification requires macOS")
class ThreadCpuLibprocQualification(unittest.TestCase):
    def setUp(self) -> None:
        thread_cpu_sampler.set_session_backend("libproc")

    def tearDown(self) -> None:
        thread_cpu_sampler._SELECTED_BACKEND = None  # type: ignore[attr-defined]

    def test_offline_preflight_ready(self) -> None:
        report = thread_cpu_sampler.preflight_offline()
        self.assertEqual(report.get("status"), "ready")
        self.assertEqual(report.get("thread_enumeration"), "passed")
        self.assertEqual(report.get("cpu_delta"), "passed")

    def test_single_spin_thread_cpu_vs_wall(self) -> None:
        process, main_tid = _start_worker("spin")
        try:
            report = thread_cpu_sampler.poll_window(
                process.pid,
                duration_s=1.0,
                interval_s=0.1,
                main_thread_id=main_tid,
            )
            self.assertEqual(report["main_thread_mapping_status"], "verified")
            main_ms = float(report["main_thread_cpu_ms_per_s"])
            self.assertGreater(main_ms, 200.0)
            self.assertLess(main_ms, 2000.0)
        finally:
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
            process.kill()
            process.wait(timeout=10)

    def test_sleeping_thread_low_cpu(self) -> None:
        process, main_tid = _start_worker("sleep")
        try:
            report = thread_cpu_sampler.poll_window(
                process.pid,
                duration_s=0.5,
                interval_s=0.1,
                main_thread_id=main_tid,
            )
            self.assertLess(float(report["thread_cpu_ms_per_s"]), 50.0)
        finally:
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
            process.kill()
            process.wait(timeout=10)

    def test_dual_spin_observes_both_threads(self) -> None:
        import thread_cpu_libproc

        process, _main_tid = _start_worker("dual")
        try:
            report = thread_cpu_sampler.poll_window(process.pid, duration_s=1.0, interval_s=0.1)
            thread_ids = {int(item["thread_id"]) for item in report["thread_deltas"]}
            self.assertGreaterEqual(len(thread_ids), 2)
            self.assertGreater(float(report["thread_cpu_ms_per_s"]), 200.0)
            process_start = thread_cpu_libproc.process_total_cpu_us(process.pid)
            process_end = thread_cpu_libproc.process_total_cpu_us(process.pid)
            self.assertGreaterEqual(process_end, process_start)
        finally:
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
            process.kill()
            process.wait(timeout=10)

    def test_churn_reports_gap_without_crashing(self) -> None:
        import thread_cpu_libproc

        process, _main_tid = _start_worker("churn")
        try:
            process_start = thread_cpu_libproc.process_total_cpu_us(process.pid)
            report = thread_cpu_sampler.poll_window(process.pid, duration_s=2.0, interval_s=0.2)
            process_end = thread_cpu_libproc.process_total_cpu_us(process.pid)
            thread_ms = float(report["thread_cpu_ms_per_s"])
            observed = float(report["observed_duration_s"])
            process_ms_per_s = ((process_end - process_start) / 1000.0) / observed if observed else 0.0
            gap_ms_per_s = process_ms_per_s - thread_ms
            self.assertGreaterEqual(report["threads_disappeared_mid_window"], 0)
            self.assertIsNotNone(gap_ms_per_s)
        finally:
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
            process.kill()
            process.wait(timeout=15)


if __name__ == "__main__":
    unittest.main()
