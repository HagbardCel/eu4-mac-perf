import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_model as model  # noqa: E402


class LeanReferenceValidityTests(unittest.TestCase):
    def _write(self, path: Path, body: str) -> None:
        path.write_text(body)

    def test_accepts_minimal_frame_sequence(self):
        fields = list(model.FRAME_FIELDS)
        parts = ["F"] + ["0"] * len(fields)
        parts[1 + fields.index("update_id")] = "1"
        parts[1 + fields.index("wall_ns")] = "1000"
        parts[1 + fields.index("cpu_ns")] = "500"
        parts[1 + fields.index("measurement_epoch")] = "1"
        parts[1 + fields.index("generation")] = "2"
        parts[1 + fields.index("thread_id")] = "42"
        parts[1 + fields.index("start_ns")] = "100"
        parts[1 + fields.index("end_ns")] = "900"
        f1 = ",".join(parts)
        parts[1] = "2"
        f2 = ",".join(parts)
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.csv"
            self._write(log, f"H,3\n{f1}\n{f2}\nX,1,2,3\nZ,0,7,0\n")
            model._validate_lean_reference_trace(log, frames=2)

    def test_rejects_missing_thread_id(self):
        fields = list(model.FRAME_FIELDS)
        parts = ["F"] + ["0"] * len(fields)
        parts[1 + fields.index("update_id")] = "1"
        parts[1 + fields.index("wall_ns")] = "1000"
        parts[1 + fields.index("cpu_ns")] = "500"
        parts[1 + fields.index("measurement_epoch")] = "1"
        parts[1 + fields.index("generation")] = "2"
        parts[1 + fields.index("start_ns")] = "100"
        parts[1 + fields.index("end_ns")] = "900"
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.csv"
            self._write(log, f"H,3\n{','.join(parts)}\nZ,0,7,0\n")
            with self.assertRaises(model.base.BenchmarkError):
                model._validate_lean_reference_trace(log, frames=1)

    def test_rejects_end_not_after_start(self):
        fields = list(model.FRAME_FIELDS)
        parts = ["F"] + ["0"] * len(fields)
        parts[1 + fields.index("update_id")] = "1"
        parts[1 + fields.index("wall_ns")] = "1000"
        parts[1 + fields.index("cpu_ns")] = "500"
        parts[1 + fields.index("measurement_epoch")] = "1"
        parts[1 + fields.index("generation")] = "2"
        parts[1 + fields.index("thread_id")] = "1"
        parts[1 + fields.index("start_ns")] = "900"
        parts[1 + fields.index("end_ns")] = "100"
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.csv"
            self._write(log, f"H,3\n{','.join(parts)}\nZ,0,7,0\n")
            with self.assertRaises(model.base.BenchmarkError):
                model._validate_lean_reference_trace(log, frames=1)

    def test_rejects_zero_cpu(self):
        fields = list(model.FRAME_FIELDS)
        parts = ["F"] + ["0"] * len(fields)
        parts[1 + fields.index("update_id")] = "1"
        parts[1 + fields.index("wall_ns")] = "1000"
        parts[1 + fields.index("measurement_epoch")] = "1"
        parts[1 + fields.index("generation")] = "2"
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.csv"
            self._write(log, f"H,3\n{','.join(parts)}\nZ,0,7,0\n")
            with self.assertRaises(model.base.BenchmarkError):
                model._validate_lean_reference_trace(log, frames=1)

    @unittest.skipUnless(sys.platform == "darwin", "native writer harness requires macOS")
    def test_lean_reference_writer_csv_decodes(self):
        model.build()
        with tempfile.TemporaryDirectory() as temporary:
            control = Path(temporary) / "control.bin"
            log = Path(temporary) / "trace.csv"
            control.write_bytes(
                model.CONTROL.pack(
                    model.FORMAT_VERSION,
                    2,
                    1,
                    1,
                    3,
                    0,
                    0,
                    0,
                    1,
                    7,
                    0,
                    0,
                    0,
                    0,
                )
                + bytes(model.CONTROL_SIZE - model.CONTROL.size),
            )
            env = {
                **os.environ,
                "DYLD_INSERT_LIBRARIES": str(model.TEST_LIBRARY.resolve()),
                "EU4_FRAME_MODEL_CONTROL": str(control),
                "EU4_FRAME_MODEL_LOG": str(log),
            }
            run = subprocess.run(
                [str(model.LEAN_REFERENCE_WRITER_HARNESS), str(model.TEST_LIBRARY.resolve())],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr or run.stdout)
            frames = model.decode_frames(model.read_rows(log))
            self.assertEqual(len(frames), 2)
            self.assertEqual(len(frames[0]), len(model.FRAME_FIELDS))
            self.assertEqual(frames[0]["update_id"], 1)
            self.assertEqual(frames[1]["update_id"], 2)
            self.assertEqual(frames[0]["start_ns"], 1000)
            self.assertEqual(frames[0]["end_ns"], 1500)
            self.assertGreater(frames[0]["thread_id"], 0)
            model._validate_lean_reference_trace(log, frames=2)
