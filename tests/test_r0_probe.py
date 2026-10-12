import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import r0_probe


class R0ProbeTests(unittest.TestCase):
    def test_parse_log_round_trip(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("ACK,2,10,1000\n")
            handle.write("F,10,1,0,1,1,0,64,64,0,1,99,1,5000,0,1,0,42,3,0\n")
            path = Path(handle.name)
        parsed = r0_probe.parse_log(path)
        self.assertEqual(parsed["acks"][0]["generation"], 2)
        self.assertEqual(parsed["frames"][0]["crc64"], 99)
        path.unlink()

    def test_infer_main_thread_id(self) -> None:
        frames = [
            {"flush_is_main_thread": 1, "pthread_tid": 99},
            {"flush_is_main_thread": 1, "pthread_tid": 99},
            {"flush_is_main_thread": 0, "pthread_tid": 1},
        ]
        self.assertEqual(r0_probe.infer_main_thread_id(frames), 99)

    @unittest.skipUnless(sys.platform == "darwin", "R0 harness requires macOS")
    def test_buffer_grow_failure_does_not_crash_harness(self) -> None:
        r0_probe.build()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = root / "control.bin"
            log = root / "probe.log"
            r0_probe.write_control(control, generation=1, mode=1, scenario_id=0)
            env = os.environ.copy()
            env.update(r0_probe.probe_env(control, log))
            env["EU4_R0_TEST_CENSUS"] = "1"
            env["EU4_R0_TEST_BUFFER_GROW_FAIL"] = "1"
            result = subprocess.run(
                [str(r0_probe.HARNESS_EXE)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0)
            parsed = r0_probe.parse_log(log)
            self.assertTrue(parsed["frames"])
            self.assertFalse(any(frame.get("census_ok") for frame in parsed["frames"]))


if __name__ == "__main__":
    unittest.main()
