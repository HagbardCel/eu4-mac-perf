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
        f1 = ",".join(parts)
        parts[1] = "2"
        f2 = ",".join(parts)
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.csv"
            self._write(log, f"H,3\n{f1}\n{f2}\nX,1,2,3\nZ,0,7,0\n")
            model._validate_lean_reference_trace(log, frames=2)

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
