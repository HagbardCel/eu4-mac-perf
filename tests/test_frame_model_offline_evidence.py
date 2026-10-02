import datetime as dt
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_model as model  # noqa: E402


def _representative_preflight():
    representative = {
        "status": "passed",
        "test_library_sha256": "test-lib",
        "workload_harness_sha256": "workload-bin",
        "harness_source_sha256": "harness-src",
        "profiler_source_sha256": "profiler-src",
        "gpu_segments_source_sha256": "gpu-seg",
        "recipes": [{
            "recipe": {"name": "mesh", "sha256": "recipe-hash"},
            "gates": {"reference_elapsed_ns": {"status": "passed"}},
            "ablations": {"writer": {"elapsed_ns": {"status": "passed"}}},
            "ablation_policy": "ablation text",
            "diagnostic_matrix": {"policy": "diag text"},
        }],
    }
    return {
        "status": "ready",
        "overhead_gate": "passed",
        "representative_workloads": representative,
        "limitations": ["test"],
    }


def _verified_identity(**overrides):
    base = {
        "git_commit": "full",
        "git_commit_short": "942685e",
        "git_tree_clean": True,
        "controller_sha256": "controller",
        "key_source_hashes": {"eu4_frame_model.c": "c-src"},
    }
    base.update(overrides)
    return base


def _build_metadata(preflight, evidence_id, identity_start, identity_end, build_info):
    executed = {
        "profiler_dylib_sha256": build_info.get("library_sha256"),
        "test_library_sha256": "test-lib",
        "workload_harness_sha256": "workload-bin",
        "draw_manifest_sha256": build_info.get("draw_manifest_sha256", "manifest"),
    }
    return model.build_offline_evidence_metadata(
        preflight,
        evidence_id,
        identity_start=identity_start,
        identity_end=identity_end,
        build_info=build_info,
        executed_artifact_start=executed,
        executed_artifact_end=executed,
    )


class OfflineEvidenceArchiveTests(unittest.TestCase):
    def test_immutable_archive_and_pointer(self):
        preflight = _representative_preflight()
        build_info = {
            "executable_sha256": "game-exe",
            "library_sha256": "profiler-dylib",
            "draw_manifest_sha256": "draw-manifest",
            "native_source_hashes": {
                "eu4_draw_observers.h": "draw-obs",
                "eu4_frame_model_sites.h": "sites",
            },
        }
        identity = _verified_identity(controller_sha256=model._sha256_optional(model.CONTROLLER))
        evidence_id = "20261002T120000.123456Z-deadbeef"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_dir = root / "analysis/evidence"
            pointer = root / "analysis/frame-model-offline-evidence.json"
            metadata = _build_metadata(preflight, evidence_id, identity, identity, build_info)
            with mock.patch.object(model, "ROOT", root), mock.patch.object(
                model, "OFFLINE_EVIDENCE_DIR", evidence_dir,
            ), mock.patch.object(model, "OFFLINE_EVIDENCE_POINTER", pointer), mock.patch.object(
                model, "_offline_environment_metadata", return_value={"platform": "darwin"},
            ):
                archive1, run_id1, body1 = model.write_offline_evidence_archive(preflight, metadata)
                model.write_offline_evidence_pointer(preflight, metadata, archive1, body1)
                metadata2 = _build_metadata(
                    preflight, "20261002T120001.000000Z-cafebabe", identity, identity, build_info,
                )
                archive2, run_id2, _ = model.write_offline_evidence_archive(preflight, metadata2)
                self.assertNotEqual(archive1, archive2)
                self.assertTrue(archive1.is_file() and archive2.is_file())
                record = json.loads(archive1.read_text())
                self.assertEqual(record["evidence_id"], evidence_id)
                self.assertEqual(record["git_commit"], "full")
                self.assertTrue(record["git_provenance_verified"])
                hashes = record["artifact_hashes"]
                self.assertEqual(hashes["profiler_dylib_sha256"], "profiler-dylib")
                self.assertEqual(hashes["workload_harness_sha256"], "workload-bin")
                self.assertEqual(hashes["draw_observers_sha256"], "draw-obs")
                self.assertEqual(hashes["controller_sha256"], model._sha256_optional(model.CONTROLLER))
                self.assertEqual(hashes["draw_manifest_sha256"], "draw-manifest")
                self.assertEqual(hashes["recipe_sha256"]["mesh"], "recipe-hash")
                summary = json.loads(pointer.read_text())
                self.assertEqual(summary["archive_path"], str(archive1.relative_to(root)))
                self.assertEqual(summary["archive_sha256"], hashlib.sha256(body1).hexdigest())
                self.assertEqual(summary["evidence_id"], run_id1)
                self.assertEqual(summary["artifact_hashes"]["profiler_dylib_sha256"], "profiler-dylib")
                self.assertEqual(summary["representative_workloads"]["gate_summary"][0]["recipe"], "mesh")

    def test_refuses_overwrite_exclusive_create(self):
        preflight = {"representative_workloads": {"recipes": []}, "status": "blocked", "overhead_gate": "failed"}
        identity = _verified_identity()
        metadata = _build_metadata(
            preflight, "20261002T120000.000000Z-deadbeef", identity, identity, {},
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_dir = root / "analysis/evidence"
            with mock.patch.object(model, "ROOT", root), mock.patch.object(
                model, "OFFLINE_EVIDENCE_DIR", evidence_dir,
            ), mock.patch.object(model, "_offline_environment_metadata", return_value={}):
                model.write_offline_evidence_archive(preflight, metadata)
                with self.assertRaises(model.base.BenchmarkError):
                    model.write_offline_evidence_archive(preflight, metadata)

    def test_identity_mismatch_omits_git_commit(self):
        preflight = _representative_preflight()
        start = _verified_identity(git_commit="aaa")
        end = _verified_identity(git_commit="bbb")
        metadata = _build_metadata(
            preflight, "20261002T120000.000000Z-deadbeef", start, end, {"library_sha256": "lib"},
        )
        self.assertIsNone(metadata["git_commit"])
        self.assertFalse(metadata["git_provenance_verified"])

    def test_require_git_identity_stable_head_mismatch(self):
        start = _verified_identity(git_commit="aaa")
        end = _verified_identity(git_commit="bbb")
        with self.assertRaises(model.base.BenchmarkError):
            model._require_git_identity_stable(start, end)

    def test_require_git_identity_stable_dirty_end(self):
        start = _verified_identity()
        end = _verified_identity(git_tree_clean=False)
        with self.assertRaises(model.base.BenchmarkError):
            model._require_git_identity_stable(start, end)

    def test_git_tree_clean_excludes_evidence_outputs(self):
        porcelain = "\n".join([
            " M benchmark/eu4_frame_model.py",
            "?? analysis/evidence/frame-model-offline-deadbeef-20261002T120000.000000Z-abcd1234.json",
            " M analysis/frame-model-offline-evidence.json",
        ])
        with mock.patch.object(
            model.subprocess,
            "run",
            return_value=mock.Mock(stdout=porcelain, returncode=0),
        ):
            self.assertFalse(model._git_tree_clean_for_evidence())
        clean_outputs = "\n".join([
            "?? analysis/evidence/frame-model-offline-deadbeef-20261002T120000.000000Z-abcd1234.json",
            " M analysis/frame-model-offline-evidence.json",
        ])
        with mock.patch.object(
            model.subprocess,
            "run",
            return_value=mock.Mock(stdout=clean_outputs, returncode=0),
        ):
            self.assertTrue(model._git_tree_clean_for_evidence())

    def test_git_tree_clean_reports_violation_paths(self):
        porcelain = "?? internal/notes.md"
        with mock.patch.object(
            model.subprocess,
            "run",
            return_value=mock.Mock(stdout=porcelain, returncode=0),
        ):
            violations = model._git_tree_clean_violations_for_evidence()
        self.assertEqual(violations, ["internal/notes.md"])
        self.assertFalse(model._git_tree_clean_for_evidence())

    def test_git_tree_clean_flags_tracked_evidence_archive_edits(self):
        porcelain = (
            " M analysis/evidence/frame-model-offline-legacy-373f3ff-20261001T204509Z.json"
        )
        with mock.patch.object(
            model.subprocess,
            "run",
            return_value=mock.Mock(stdout=porcelain, returncode=0),
        ):
            self.assertFalse(model._git_tree_clean_for_evidence())

    def test_require_build_executed_artifacts_match(self):
        build_info = {"library_sha256": "expected-lib", "draw_manifest_sha256": "expected-manifest"}
        snapshot = {
            "profiler_dylib_sha256": "expected-lib",
            "draw_manifest_sha256": "wrong-manifest",
        }
        with self.assertRaises(model.base.BenchmarkError):
            model._require_build_executed_artifacts_match(build_info, snapshot)
        model._require_build_executed_artifacts_match(build_info, {
            "profiler_dylib_sha256": "expected-lib",
            "draw_manifest_sha256": "expected-manifest",
        })

    def test_executed_artifact_snapshot_hashes_disk_paths(self):
        with mock.patch.object(model, "_sha256_optional", side_effect=["p", "t", "w", "m"]) as sha:
            result = model._offline_executed_artifact_snapshot()
        self.assertEqual(result["profiler_dylib_sha256"], "p")
        self.assertEqual(sha.call_args_list[0][0][0], model.LIBRARY)
        self.assertEqual(sha.call_args_list[3][0][0], model.draw_api.OUTPUT)

    def test_offline_evidence_id_has_subsecond_and_suffix(self):
        fixed = dt.datetime(2026, 10, 2, 12, 0, 0, 42, tzinfo=dt.timezone.utc)
        with mock.patch.object(model.uuid, "uuid4", return_value=mock.Mock(hex="abcd1234ef567890")):
            run_id = model._offline_evidence_id(fixed)
        self.assertEqual(run_id, "20261002T120000.000042Z-abcd1234")

    def test_require_executed_artifacts_stable_mismatch(self):
        start = {"test_library_sha256": "a", "workload_harness_sha256": "b"}
        end = {"test_library_sha256": "a", "workload_harness_sha256": "c"}
        with self.assertRaises(model.base.BenchmarkError):
            model._require_executed_artifacts_stable(start, end)

    def test_offline_workloads_uses_frozen_executed_hashes(self):
        frozen = {
            "profiler_dylib_sha256": "profiler",
            "test_library_sha256": "frozen-test",
            "workload_harness_sha256": "frozen-workload",
            "draw_manifest_sha256": "manifest",
        }
        with mock.patch.object(model, "workload") as workload_mock:
            workload_mock.recipes.return_value = []
            result = model.offline_workloads(executed_artifacts_start=frozen)
        self.assertEqual(result["test_library_sha256"], "frozen-test")
        self.assertEqual(result["workload_harness_sha256"], "frozen-workload")

    def test_pointer_archive_hash_matches_bytes(self):
        preflight = _representative_preflight()
        identity = _verified_identity()
        metadata = _build_metadata(
            preflight, "20261002T120000.000000Z-deadbeef", identity, identity, {},
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_dir = root / "analysis/evidence"
            pointer = root / "analysis/frame-model-offline-evidence.json"
            with mock.patch.object(model, "ROOT", root), mock.patch.object(
                model, "OFFLINE_EVIDENCE_DIR", evidence_dir,
            ), mock.patch.object(model, "OFFLINE_EVIDENCE_POINTER", pointer), mock.patch.object(
                model, "_offline_environment_metadata", return_value={},
            ):
                archive, _, body = model.write_offline_evidence_archive(preflight, metadata)
                model.write_offline_evidence_pointer(preflight, metadata, archive, body)
                summary = json.loads(pointer.read_text())
                self.assertEqual(summary["archive_sha256"], hashlib.sha256(body).hexdigest())
                self.assertNotEqual(summary["archive_sha256"], hashlib.sha256(body + b"x").hexdigest())


if __name__ == "__main__":
    unittest.main()
