import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_model as model  # noqa: E402


class OfflineEvidenceArchiveTests(unittest.TestCase):
    def test_immutable_archive_and_pointer(self):
        representative = {
            "status": "passed",
            "test_library_sha256": "a",
            "harness_source_sha256": "b",
            "profiler_source_sha256": "c",
            "gpu_segments_source_sha256": "d",
            "recipes": [{
                "recipe": {"name": "mesh", "sha256": "recipe-hash"},
                "gates": {"reference_elapsed_ns": {"status": "passed"}},
                "ablations": {"writer": {"elapsed_ns": {"status": "passed"}}},
                "ablation_policy": "ablation text",
                "diagnostic_matrix": {"policy": "diag text"},
            }],
        }
        preflight = {
            "status": "ready",
            "overhead_gate": "passed",
            "representative_workloads": representative,
            "limitations": ["test"],
        }
        fixed = dt.datetime(2026, 10, 2, 12, 0, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_dir = root / "analysis/evidence"
            pointer = root / "analysis/frame-model-offline-evidence.json"
            with mock.patch.object(model, "ROOT", root), mock.patch.object(
                model, "OFFLINE_EVIDENCE_DIR", evidence_dir,
            ), mock.patch.object(model, "OFFLINE_EVIDENCE_POINTER", pointer), mock.patch.object(
                model, "_offline_environment_metadata", return_value={"platform": "darwin"},
            ), mock.patch.object(
                model, "_git_commit_metadata",
                return_value={"git_commit": "full", "git_commit_short": "942685e"},
            ):
                archive1, run_id1 = model.write_offline_evidence_archive(
                    preflight, evidence_id="20261002T120000Z", now=fixed,
                )
                model.write_offline_evidence_pointer(preflight, run_id1, archive1)
                archive2, run_id2 = model.write_offline_evidence_archive(
                    preflight, evidence_id="20261002T120001Z", now=fixed,
                )
                self.assertNotEqual(archive1, archive2)
                self.assertTrue(archive1.is_file() and archive2.is_file())
                record = json.loads(archive1.read_text())
                self.assertEqual(record["evidence_id"], "20261002T120000Z")
                self.assertEqual(record["artifact_hashes"]["recipe_sha256"]["mesh"], "recipe-hash")
                self.assertEqual(record["preflight"]["overhead_gate"], "passed")
                summary = json.loads(pointer.read_text())
                self.assertEqual(summary["archive_path"], str(archive1.relative_to(root)))
                self.assertEqual(summary["representative_workloads"]["gate_summary"][0]["recipe"], "mesh")

    def test_refuses_overwrite(self):
        preflight = {"representative_workloads": {"recipes": []}, "status": "blocked", "overhead_gate": "failed"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_dir = root / "analysis/evidence"
            with mock.patch.object(model, "ROOT", root), mock.patch.object(
                model, "OFFLINE_EVIDENCE_DIR", evidence_dir,
            ), mock.patch.object(model, "_offline_environment_metadata", return_value={}), mock.patch.object(
                model, "_git_commit_metadata", return_value={"git_commit_short": "deadbeef"},
            ):
                model.write_offline_evidence_archive(preflight, evidence_id="20261002T120000Z")
                with self.assertRaises(model.base.BenchmarkError):
                    model.write_offline_evidence_archive(preflight, evidence_id="20261002T120000Z")


if __name__ == "__main__":
    unittest.main()
