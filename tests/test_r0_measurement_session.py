import importlib
import unittest

import r0_measurement_session as session


class R0MeasurementSessionTests(unittest.TestCase):
    def test_module_imports(self) -> None:
        importlib.import_module("r0_measurement_session")

    def test_acceptance_gates_completed(self) -> None:
        manifest = {
            "epoch_a_thread_cpu": {
                "reconcile_ok": True,
                "main_thread_mapping_status": "verified",
            },
            "thread_cpu_preflight": {"status": "ready"},
            "event_clock_alignment": {"status": "passed"},
            "census_frame_count": 200,
            "census_frame_count_by_scenario": {"0": 150, "1": 50},
            "positive_control_status": "passed",
            "analyzer_results": {
                "stack": {"exit_code": 0},
                "frame_evolution": {"exit_code": 0},
            },
            "drawable_capability": {"status": "supported"},
        }
        session.acceptance_gates(manifest)
        self.assertEqual(manifest["status"], "completed")

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
        }
        session.acceptance_gates(manifest)
        self.assertEqual(manifest["status"], "partial_complete")

    def test_cleanup_error_not_upgraded(self) -> None:
        manifest = {
            "status": "cleanup_error",
            "epoch_a_thread_cpu": {"reconcile_ok": True, "main_thread_mapping_status": "verified"},
            "thread_cpu_preflight": {"status": "ready"},
            "event_clock_alignment": {"status": "passed"},
            "analyzer_results": {"stack": {"exit_code": 0}, "frame_evolution": {"skipped": True}},
            "drawable_capability": {"failure_class": "unsupported_drawable"},
        }
        session.acceptance_gates(manifest)
        self.assertEqual(manifest["status"], "cleanup_error")


if __name__ == "__main__":
    unittest.main()
