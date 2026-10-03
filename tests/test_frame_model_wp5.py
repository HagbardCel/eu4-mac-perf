import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_model as model  # noqa: E402


class Wp5ReferenceFastPathTests(unittest.TestCase):
    def test_offline_workload_keeps_measurement_off_until_arm(self):
        mode, flags = model._offline_workload_mode_and_flags("reference")
        self.assertEqual(mode, model.MODE["reference"])
        self.assertEqual(flags, 1)
        loaded_mode, loaded_flags = model._offline_workload_mode_and_flags("loaded-disabled")
        self.assertEqual(loaded_flags, 0)
        self.assertIn("EU4_TEST_LOADED_DISABLED", model._offline_workload_private_env())

    @unittest.skipUnless(sys.platform == "darwin", "native GL harness requires macOS")
    def test_warmup_boundary_harness(self):
        model.build()
        with tempfile.TemporaryDirectory() as temporary:
            control = Path(temporary) / "control.bin"
            log = Path(temporary) / "trace.csv"
            control.write_bytes(
                model.CONTROL.pack(model.FORMAT_VERSION, 2, 1, 1, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0)
                + bytes(model.CONTROL_SIZE - model.CONTROL.size),
            )
            env = {
                **os.environ,
                "DYLD_INSERT_LIBRARIES": str(model.TEST_LIBRARY.resolve()),
                "EU4_FRAME_MODEL_CONTROL": str(control),
                "EU4_FRAME_MODEL_LOG": str(log),
            }
            run = subprocess.run(
                [str(model.WARMUP_BOUNDARY_HARNESS), str(model.TEST_LIBRARY.resolve())],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if run.returncode == 6:
                self.skipTest("no accelerated GL pixel format on this host")
            self.assertEqual(run.returncode, 0, run.stderr or run.stdout)

    @unittest.skipUnless(sys.platform == "darwin", "native GL harness requires macOS")
    def test_loaded_disabled_harness(self):
        model.build()
        with tempfile.TemporaryDirectory() as temporary:
            control = Path(temporary) / "control.bin"
            log = Path(temporary) / "trace.csv"
            control.write_bytes(
                model.CONTROL.pack(model.FORMAT_VERSION, 2, model.MODE["reference"], 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0)
                + bytes(model.CONTROL_SIZE - model.CONTROL.size),
            )
            env = {
                **os.environ,
                "DYLD_INSERT_LIBRARIES": str(model.TEST_LIBRARY.resolve()),
                "EU4_FRAME_MODEL_CONTROL": str(control),
                "EU4_FRAME_MODEL_LOG": str(log),
                "EU4_TEST_LOADED_DISABLED": "1",
            }
            run = subprocess.run(
                [str(model.LOADED_DISABLED_HARNESS), str(model.TEST_LIBRARY.resolve())],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if run.returncode == 6:
                self.skipTest("no accelerated GL pixel format on this host")
            self.assertEqual(run.returncode, 0, run.stderr or run.stdout)

    @unittest.skipUnless(sys.platform == "darwin", "native GL harness requires macOS")
    def test_reference_transition_harness(self):
        model.build()
        with tempfile.TemporaryDirectory() as temporary:
            control = Path(temporary) / "control.bin"
            log = Path(temporary) / "trace.csv"
            control.write_bytes(
                model.CONTROL.pack(model.FORMAT_VERSION, 2, 1, 1, 3, 0, 0, 0, 1, 0, 0, 0, 0, 0)
                + bytes(model.CONTROL_SIZE - model.CONTROL.size),
            )
            env = {
                **os.environ,
                "DYLD_INSERT_LIBRARIES": str(model.TEST_LIBRARY.resolve()),
                "EU4_FRAME_MODEL_CONTROL": str(control),
                "EU4_FRAME_MODEL_LOG": str(log),
            }
            run = subprocess.run(
                [str(model.REFERENCE_TRANSITION_HARNESS), str(model.TEST_LIBRARY.resolve())],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if run.returncode == 6:
                self.skipTest("no accelerated GL pixel format on this host")
            self.assertEqual(run.returncode, 0, run.stderr or run.stdout)


if __name__ == "__main__":
    unittest.main()
