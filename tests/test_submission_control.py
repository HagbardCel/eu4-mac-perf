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
            test_dylib = root / "libeu4_submission_cap2.dylib"
            control.build_test_dylib(test_dylib, compiled_capability=2)
            control_path = root / "submission_control.bin"
            control_path.write_bytes(bytes(control.CONTROL_SIZE))
            probe_log = root / "probe.csv"
            env = {
                **control.dylib_env(control_path, auto.PROBE),
                "DYLD_INSERT_LIBRARIES": f"{auto.PROBE}:{test_dylib}",
            }
            env["EU4_SUBMISSION_TEST_STUB_HOOK"] = "1"
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
            self.assertIn("control_ticks=", result.stdout)
            snap = control.read_snapshot(control_path)
            self.assertEqual(snap["protocol_version"], control.PROTOCOL_VERSION)
            self.assertEqual(snap["advertised_capability_id"], 2)
            self.assertGreater(snap["control_ticks"], 0)
            self.assertGreater(snap["candidate_site_entries"], 0)
            self.assertGreater(snap["candidate_effective_actions"], 0)
            self.assertGreaterEqual(snap["ack_generation"], 1)
            self.assertEqual(snap["ack_mode"], control.MODE_CANDIDATE)
            probe_text = probe_log.read_text(encoding="utf-8", errors="replace")
            self.assertIn("S,", probe_text)

    def test_deferred_install_lifecycle(self):
        auto.build()
        control.build()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_dylib = root / "libeu4_submission_cap2.dylib"
            control.build_test_dylib(test_dylib, compiled_capability=2)
            control_path = root / "submission_control.bin"
            control_path.write_bytes(bytes(control.CONTROL_SIZE))
            env = {
                **control.dylib_env(control_path, auto.PROBE),
                "DYLD_INSERT_LIBRARIES": f"{auto.PROBE}:{test_dylib}",
                "EU4_SUBMISSION_TEST_STUB_HOOK": "1",
            }
            result = subprocess.run(
                [str(control.DEFERRED_INSTALL_HARNESS)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            self.assertIn("deferred_install_ok", result.stdout)
