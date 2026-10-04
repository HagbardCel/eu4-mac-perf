import csv
import gzip
import tempfile
import unittest
from pathlib import Path

import eu4_frame_model as model
from frame_model_evidence import seal_artifact_pair, seal_run_evidence


class FrameModelEvidenceTests(unittest.TestCase):
    def test_read_rows_gzip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "telemetry.csv.gz"
            with gzip.open(path, "wt", newline="") as handle:
                csv.writer(handle).writerow(["H", "3"])
                csv.writer(handle).writerow(["F"] + ["0"] * 30)
            rows = model.read_rows(path)
            self.assertEqual(rows[0][0], "H")

    def test_seal_writes_gzip_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            plain = run_dir / "telemetry.csv"
            plain.write_text("H,3\n", encoding="ascii")
            (run_dir / "manifest.json").write_text('{"report_kind":"intrusive_diagnostic"}', encoding="ascii")
            entry = seal_artifact_pair(run_dir, "telemetry.csv", "telemetry.csv.gz")
            self.assertIsNotNone(entry)
            self.assertTrue((run_dir / "telemetry.csv.gz").is_file())
            evidence = seal_run_evidence(run_dir, write_game_log=False)
            self.assertIn("capture_identity", evidence)
            self.assertEqual(evidence["capture_identity"]["status"], "reconstructed_incomplete")
            self.assertIn("telemetry.csv.gz", evidence["capture_inventory"])

    def test_logical_hash_survives_plain_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            plain = run_dir / "telemetry.csv"
            payload = b"row-one\nrow-two\n"
            plain.write_bytes(payload)
            first = seal_artifact_pair(run_dir, "telemetry.csv", "telemetry.csv.gz")
            self.assertIsNotNone(first)
            plain.unlink()
            second = seal_artifact_pair(run_dir, "telemetry.csv", "telemetry.csv.gz")
            self.assertIsNotNone(second)
            self.assertEqual(first["uncompressed_bytes"], second["uncompressed_bytes"])
            self.assertEqual(first["uncompressed_sha256"], second["uncompressed_sha256"])
