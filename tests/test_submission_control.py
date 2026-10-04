import platform
import subprocess
import tempfile
import unittest
from pathlib import Path

import autonomous_runner as auto
import submission_control as control


@unittest.skipUnless(platform.system() == "Darwin", "submission dylib harness requires macOS")
class SubmissionControlHarnessTests(unittest.TestCase):
    def test_dual_dylib_ack_and_counters(self):
        auto.build()
        control.build()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_path = root / "submission_control.bin"
            control_path.write_bytes(bytes(control.CONTROL_SIZE))
            probe_log = root / "probe.csv"
            env = control.dylib_env(control_path, auto.PROBE, active_capability_id=42)
            env["EU4_AUTO_PROBE_LOG"] = str(probe_log)
            result = subprocess.run(
                [str(control.HARNESS)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("hook_attempts=", result.stdout)
            snap = control.read_snapshot(control_path)
            self.assertGreater(snap["candidate_hook_attempts"], 0)
            self.assertGreater(snap["candidate_effective_actions"], 0)
            self.assertGreaterEqual(snap["ack_generation"], 1)
            probe_text = probe_log.read_text(encoding="utf-8", errors="replace")
            self.assertIn("S,", probe_text)
