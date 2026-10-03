import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_model as model  # noqa: E402


class Wp6ReferenceCpuDecompositionTests(unittest.TestCase):
    def test_loaded_disabled_control_is_reference_without_measure_flags(self):
        mode, flags = model._offline_workload_mode_and_flags("loaded-disabled")
        self.assertEqual(mode, model.MODE["reference"])
        self.assertEqual(flags, 0)

    def test_build_metadata_uses_reference_cpu_workloads(self):
        identity = {
            "git_commit": "c" * 40,
            "git_commit_short": "c" * 7,
            "git_tree_clean": True,
            "controller_sha256": "ctrl",
            "key_source_hashes": {},
        }
        build_info = {"executable_sha256": "game", "library_sha256": "lib", "native_source_hashes": {}}
        executed = {
            "profiler_dylib_sha256": "lib",
            "test_library_sha256": "test-lib",
            "workload_harness_sha256": "harness-bin",
            "draw_manifest_sha256": "manifest",
        }
        source = model._offline_workload_source_hashes()
        preflight = {
            "purpose": model.WP6_REFERENCE_CPU_DECOMPOSITION_PURPOSE,
            "reference_cpu_workloads": {
                "reference_cpu_decomposition": True,
                "loaded_disabled_contract": model.WP6_LOADED_DISABLED_CONTRACT,
                "recipes": [
                    {
                        "recipe": {"name": "mesh", "sha256": "mesh-hash", "role": "training"},
                        "trials": [],
                    },
                ],
                **source,
            },
        }
        metadata = model.build_offline_evidence_metadata(
            preflight,
            "20261003T120000.000000Z-abc12345",
            identity_start=identity,
            identity_end=identity,
            build_info=build_info,
            executed_artifact_start=executed,
            executed_artifact_end=executed,
        )
        self.assertEqual(metadata["artifact_hashes"]["recipe_sha256"]["mesh"], "mesh-hash")
        self.assertIn("reference_cpu_decomposition", metadata["policy_versions"])
        self.assertEqual(
            metadata["policy_versions"]["loaded_disabled_contract"],
            model.WP6_LOADED_DISABLED_CONTRACT,
        )

    def test_reference_cpu_comparisons_are_diagnostic(self):
        trials = [
            {
                "stages": {
                    "bare": {"elapsed_ns": 1000, "cpu_ns": 100},
                    "loaded-disabled": {"elapsed_ns": 1100, "cpu_ns": 120},
                    "reference": {"elapsed_ns": 1300, "cpu_ns": 150},
                },
            },
        ]
        comparisons = model._reference_cpu_stage_comparisons(trials)
        self.assertEqual(comparisons["loaded-disabled_cpu_ns"]["status"], "diagnostic")
        self.assertFalse(comparisons["reference_cpu_ns"]["acceptance_gate"])
        self.assertNotIn("limit", comparisons["reference_cpu_ns"])

    def test_reference_cpu_recipe_runs_all_ladder_stages(self):
        recipe = {"name": "mesh", "path": "/tmp/mesh.recipe", "draws": 1}
        measured = {"elapsed_ns": 1, "cpu_ns": 2}
        with mock.patch.object(model, "_offline_workload_stage", return_value=(measured, None)) as stage:
            entry = model._offline_reference_cpu_recipe_evidence(Path("/tmp"), recipe)
        stages_called = [call.args[3] for call in stage.call_args_list]
        self.assertEqual(set(stages_called), set(model.REFERENCE_CPU_LADDER_STAGES))
        self.assertNotIn("path", entry["recipe"])


class ValidateLoadedDisabledTraceTests(unittest.TestCase):
    def _write_trace(self, path: Path, body: str) -> None:
        path.write_text(body)

    def test_accepts_passive_shutdown_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.csv"
            self._write_trace(log, "H,3\nX,1,2,3\nZ,0,7,0\n")
            model._validate_loaded_disabled_trace(log)

    def test_rejects_published_measurement_frames(self):
        frame_line = "F," + ",".join("0" * len(model.V2_FRAME_FIELDS))
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.csv"
            self._write_trace(log, f"H,3\n{frame_line}\nX,1,2,3\nZ,0,7,0\n")
            with self.assertRaises(model.base.BenchmarkError) as ctx:
                model._validate_loaded_disabled_trace(log)
            self.assertIn("published", str(ctx.exception))

    def test_rejects_hook_failures_and_missing_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.csv"
            self._write_trace(log, "H,3\nZ,1,7,0\n")
            with self.assertRaises(model.base.BenchmarkError):
                model._validate_loaded_disabled_trace(log)
            self._write_trace(log, "H,3\nZ,0,7,0\n")
            with self.assertRaises(model.base.BenchmarkError) as ctx:
                model._validate_loaded_disabled_trace(log)
            self.assertIn("shutdown X", str(ctx.exception))
