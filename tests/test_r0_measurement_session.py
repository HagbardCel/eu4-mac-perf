import importlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import r0_measurement_session as session

_TESTS = Path(__file__).resolve().parent
_E2E = importlib.util.spec_from_file_location(
    "test_frame_evolution_cli_e2e", _TESTS / "test_frame_evolution_cli_e2e.py"
)
assert _E2E and _E2E.loader
_e2e = importlib.util.module_from_spec(_E2E)
sys.modules[_E2E.name] = _e2e
_E2E.loader.exec_module(_e2e)
ROI_FIXTURE = _e2e.ROI_FIXTURE
build_success_fixture = _e2e.build_success_fixture


def _qualified_window(
    *,
    frame_count: int = 240,
    duration_seconds: float = 2.5,
    control_generation: int = 100,
) -> dict:
    return {
        "qualified": True,
        "frame_count": frame_count,
        "duration_seconds": duration_seconds,
        "continuity": {"status": "passed"},
        "control_generation": control_generation,
    }


def _gate_ready_manifest(
    frame_report: dict,
    *,
    acquisition: dict | None = None,
) -> dict:
    return {
        "schema": "r0_measurement_session_v1",
        "epoch_a_thread_cpu": {
            "reconcile_ok": True,
            "main_thread_mapping_status": "verified",
        },
        "thread_cpu_preflight": {"status": "ready"},
        "event_clock_alignment": {"status": "passed"},
        "analyzer_results": {
            "stack": {"exit_code": 0},
            "frame_evolution": {"exit_code": 0},
        },
        "drawable_capability": {"status": "supported"},
        "frame_evolution_report": frame_report,
        "stack_report": {
            "functional_caller": {"ingame_render": 0.5},
            "attributed_sample_weight": 10,
            "root_inclusive": 10,
        },
        "acquisition_generations": acquisition
        or {"baseline": 100, "scenario_1_census": 104},
    }


class R0MeasurementSessionTests(unittest.TestCase):
    def test_module_imports(self) -> None:
        importlib.import_module("r0_measurement_session")

    def test_acceptance_gates_completed(self) -> None:
        frame_report = {
            "qualified_windows": {
                "0": _qualified_window(control_generation=100),
                "1": _qualified_window(frame_count=55, duration_seconds=1.2, control_generation=104),
            },
            "positive_control": {"status": "passed"},
            "roi_config": {"verified": True},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_success_fixture(root)
            manifest = _gate_ready_manifest(frame_report)
            manifest["probe_log_path"] = str(root / "r0_probe.log")
            session.acceptance_gates(manifest, root)
            self.assertEqual(manifest["status"], "completed")

    def test_unverified_roi_blocks_completed(self) -> None:
        frame_report = {
            "qualified_windows": {
                "0": _qualified_window(control_generation=100),
                "1": _qualified_window(frame_count=55, duration_seconds=1.2, control_generation=104),
            },
            "positive_control": {
                "status": "insufficient_evidence",
                "provisional_roi_check": "passed",
            },
            "roi_config": {"verified": False},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_success_fixture(root)
            manifest = _gate_ready_manifest(frame_report)
            manifest["roi_pending_verification"] = True
            manifest["probe_log_path"] = str(root / "r0_probe.log")
            session.acceptance_gates(manifest, root)
            self.assertEqual(manifest["status"], "incomplete_gates")
            self.assertFalse(manifest["acceptance_gates"]["roi_verified"])

    def test_acceptance_gates_v1_without_output_dir_fails_flush_gate(self) -> None:
        frame_report = {
            "qualified_windows": {
                "0": _qualified_window(control_generation=100),
                "1": _qualified_window(frame_count=55, duration_seconds=1.2, control_generation=104),
            },
            "positive_control": {"status": "passed"},
            "roi_config": {"verified": True},
        }
        manifest = _gate_ready_manifest(frame_report)
        session.acceptance_gates(manifest)
        self.assertFalse(manifest["acceptance_gates"]["flush_single_owner"])
        self.assertNotEqual(manifest["status"], "completed")

    def test_acceptance_gates_v1_empty_output_dir_missing_log_fails_flush(self) -> None:
        frame_report = {
            "qualified_windows": {
                "0": _qualified_window(control_generation=100),
                "1": _qualified_window(frame_count=55, duration_seconds=1.2, control_generation=104),
            },
            "positive_control": {"status": "passed"},
            "roi_config": {"verified": True},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _gate_ready_manifest(frame_report)
            session.acceptance_gates(manifest, root)
            self.assertFalse(manifest["acceptance_gates"]["flush_single_owner"])
            self.assertNotEqual(manifest["status"], "completed")

    def test_stale_positive_control_overwritten_on_reanalyze(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gens = build_success_fixture(root)
            manifest = {
                "schema": "r0_measurement_session_v1",
                "positive_control_status": "passed",
                "status": "completed",
                "captures": {
                    "pre": {"generation": gens["pre_generation"]},
                    "post": {"generation": gens["post_generation"]},
                },
                "roi_config_path": str(ROI_FIXTURE),
                "frame_evolution_status": "run",
            }
            (root / "manifest.json").write_text(json.dumps(manifest))
            rgba_path = root / "captures" / f"capture_{gens['pre_generation']}.rgba"
            data = bytearray(rgba_path.read_bytes())
            data[0] = 255
            rgba_path.write_bytes(bytes(data))
            sidecar_path = root / "captures" / f"capture_{gens['pre_generation']}.json"
            sidecar = json.loads(sidecar_path.read_text())
            sidecar["crc64"] = 0
            sidecar_path.write_text(json.dumps(sidecar))
            session.requalify_session(root, roi_config=ROI_FIXTURE)
            updated = json.loads((root / "manifest.json").read_text())
            self.assertNotEqual(updated.get("status"), "completed")
            self.assertEqual(updated.get("positive_control_status"), "failed")

    def test_roi_requalify_promote_and_demote(self) -> None:
        unverified_roi = Path(tempfile.mkdtemp()) / "roi.json"
        unverified_roi.write_text(
            json.dumps(
                {
                    "verified": False,
                    "panel_roi_opengl_bottom_left": [0.5, 0.5, 0.12, 0.12],
                }
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gens = build_success_fixture(root)
            sample_snippet = _TESTS / "fixtures" / "r0_epoch_b_sample_snippet.txt"
            (root / "epoch_b.sample.txt").write_text(sample_snippet.read_text())
            manifest = _gate_ready_manifest(
                {},
                acquisition={"baseline": 100, "scenario_1_census": 104},
            )
            manifest["captures"] = {
                "pre": {"generation": gens["pre_generation"]},
                "post": {"generation": gens["post_generation"]},
            }
            manifest["roi_pending_verification"] = True
            manifest["roi_config_path"] = str(unverified_roi)
            manifest["frame_evolution_status"] = "run"
            manifest["probe_log_path"] = str(root / "r0_probe.log")
            (root / "manifest.json").write_text(json.dumps(manifest))
            session.run_offline_analyzers(root, manifest)
            session.acceptance_gates(manifest)
            self.assertEqual(manifest["status"], "incomplete_gates")
            session.requalify_session(root, roi_config=ROI_FIXTURE)
            promoted = json.loads((root / "manifest.json").read_text())
            self.assertEqual(promoted["status"], "completed")
            rgba_path = root / "captures" / f"capture_{gens['post_generation']}.rgba"
            data = bytearray(rgba_path.read_bytes())
            data[-1] = 255
            rgba_path.write_bytes(bytes(data))
            session.requalify_session(root, roi_config=ROI_FIXTURE)
            demoted = json.loads((root / "manifest.json").read_text())
            self.assertNotEqual(demoted.get("status"), "completed")

    def test_partial_complete_requires_stack(self) -> None:
        manifest = {
            "epoch_a_thread_cpu": {
                "reconcile_ok": True,
                "main_thread_mapping_status": "verified",
            },
            "thread_cpu_preflight": {"status": "ready"},
            "event_clock_alignment": {"status": "passed"},
            "analyzer_results": {
                "stack": {"exit_code": 0},
                "frame_evolution": {"skipped": True},
            },
            "drawable_capability": {"failure_class": "unsupported_drawable"},
            "stack_report": {
                "functional_caller": {"ingame_render": 0.5},
                "attributed_sample_weight": 10,
                "root_inclusive": 10,
            },
        }
        session.acceptance_gates(manifest)
        self.assertEqual(manifest["status"], "partial_complete")

    def test_requalify_missing_probe_log_clears_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gens = build_success_fixture(root)
            sample_snippet = _TESTS / "fixtures" / "r0_epoch_b_sample_snippet.txt"
            (root / "epoch_b.sample.txt").write_text(sample_snippet.read_text())
            frame_report = {
                "qualified_windows": {
                    "0": _qualified_window(control_generation=100),
                    "1": _qualified_window(frame_count=55, duration_seconds=1.2, control_generation=104),
                },
                "positive_control": {"status": "passed"},
                "roi_config": {"verified": True},
            }
            manifest = _gate_ready_manifest(frame_report)
            manifest["status"] = "completed"
            manifest["probe_log_path"] = str(root / "r0_probe.log")
            manifest["captures"] = {
                "pre": {"generation": gens["pre_generation"]},
                "post": {"generation": gens["post_generation"]},
            }
            manifest["roi_config_path"] = str(ROI_FIXTURE)
            manifest["frame_evolution_status"] = "run"
            (root / "manifest.json").write_text(json.dumps(manifest))
            (root / "frame_evolution_report.json").write_text(json.dumps(frame_report))
            (root / "stack_report.json").write_text(json.dumps(manifest["stack_report"]))
            (root / "r0_probe.log").unlink()
            session.requalify_session(root, roi_config=ROI_FIXTURE)
            updated = json.loads((root / "manifest.json").read_text())
            self.assertNotEqual(updated.get("status"), "completed")
            self.assertEqual(updated.get("positive_control_status"), "failed")
            self.assertNotIn("frame_evolution_report", updated)
            self.assertFalse((root / "frame_evolution_report.json").exists())

    def test_cleanup_error_not_upgraded(self) -> None:
        manifest = {
            "status": "cleanup_error",
            "epoch_a_thread_cpu": {"reconcile_ok": True, "main_thread_mapping_status": "verified"},
            "thread_cpu_preflight": {"status": "ready"},
            "event_clock_alignment": {"status": "passed"},
            "analyzer_results": {"stack": {"exit_code": 0}, "frame_evolution": {"skipped": True}},
            "drawable_capability": {"failure_class": "unsupported_drawable"},
            "stack_report": {
                "functional_caller": {"ingame_render": 0.5},
                "attributed_sample_weight": 10,
                "root_inclusive": 10,
            },
        }
        session.acceptance_gates(manifest)
        self.assertEqual(manifest["status"], "cleanup_error")


if __name__ == "__main__":
    unittest.main()
