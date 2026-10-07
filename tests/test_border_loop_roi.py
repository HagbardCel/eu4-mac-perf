#!/usr/bin/env python3
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis" / "tools"))

from border_loop_roi import (  # noqa: E402
    MANDATORY_SELF_TEST_BUCKETS,
    estimated_production_eliminations_per_second,
    eliminations_per_frame,
    validate_roi_readiness_payload,
)


def _base_payload() -> dict:
    path = ROOT / "analysis" / "evidence" / "border-loop-head-roi-readiness-20261005.json"
    return json.loads(path.read_text())


def _all_pass_buckets() -> dict:
    return {k: "PASS" for k in MANDATORY_SELF_TEST_BUCKETS}


class BorderLoopRoiTests(unittest.TestCase):
    def test_readiness_artifact(self) -> None:
        validate_roi_readiness_payload(_base_payload())

    def test_ready_requires_all_pass(self) -> None:
        p = _base_payload()
        p["roi_instrumentation_status"] = "READY_OFFLINE"
        p["ready_for_single_venice_observer_run"] = False
        p["runtime_gate_0_probe_ready"] = True
        p["runtime_detour_installer_ready"] = True
        p["self_tests"] = _all_pass_buckets()
        p["self_tests"]["gateway_runtime"] = "PENDING"
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_pending_with_mixed_buckets(self) -> None:
        p = _base_payload()
        p["roi_instrumentation_status"] = "PENDING"
        p["ready_for_single_venice_observer_run"] = False
        p["self_tests"] = {
            "portable_suite": "PASS",
            "dual_suppression_replay": "PASS",
            "classifier_python_c_parity": "PENDING",
            "border_detour_installer": "NOT_RUN",
            **{k: "NOT_RUN" for k in MANDATORY_SELF_TEST_BUCKETS if k not in (
                "portable_suite",
                "dual_suppression_replay",
                "classifier_python_c_parity",
                "border_detour_installer",
            )},
        }
        validate_roi_readiness_payload(p)

    def test_pending_deferred_install_bucket(self) -> None:
        p = _base_payload()
        p["roi_instrumentation_status"] = "PENDING"
        p["ready_for_single_venice_observer_run"] = False
        p["self_tests"] = _all_pass_buckets()
        p["self_tests"]["deferred_install_lifecycle"] = "PENDING"
        p["self_tests"]["gateway_runtime"] = "PENDING"
        validate_roi_readiness_payload(p)

    def test_pending_all_pass_invalid(self) -> None:
        p = _base_payload()
        p["roi_instrumentation_status"] = "PENDING"
        p["ready_for_single_venice_observer_run"] = False
        p["self_tests"] = _all_pass_buckets()
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_failed_self_test(self) -> None:
        p = _base_payload()
        p["roi_instrumentation_status"] = "FAILED_SELF_TEST"
        p["self_tests"] = copy.deepcopy(_base_payload()["self_tests"])
        p["self_tests"]["gateway_runtime"] = "FAIL"
        validate_roi_readiness_payload(p)

    def test_per_frame_rate(self) -> None:
        self.assertEqual(eliminations_per_frame(400, 100), 4.0)
        self.assertIsNone(eliminations_per_frame(1, 0))

    def test_production_rate_uses_baseline_fps(self) -> None:
        est = estimated_production_eliminations_per_second(4.0, fps_baseline=55.0)
        self.assertEqual(est, 220.0)

    def test_fail_below_threshold_requires_proven_census(self) -> None:
        p = _base_payload()
        p["roi_gate"] = "FAIL_BELOW_THRESHOLD"
        p["mode0_domain_status"] = "empty"
        p["semantic_eliminations_per_frame"] = 0.0
        p["implementable_eliminations_per_frame"] = 0.0
        p["structural_eliminations_per_frame"] = 0.0
        p["estimated_implementable_eliminations_per_second"] = 0.0
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_fail_below_threshold_after_proven_census(self) -> None:
        p = _base_payload()
        p["roi_gate"] = "FAIL_BELOW_THRESHOLD"
        p["mode0_domain_status"] = "empty"
        p["semantic_eliminations_per_frame"] = 0.0
        p["implementable_eliminations_per_frame"] = 0.0
        p["structural_eliminations_per_frame"] = 0.0
        p["estimated_implementable_eliminations_per_second"] = 0.0
        p["domain_census_live"] = {
            "status": "PASS_VENICE_MEASURED",
            "counter_schema_version": 6,
            "run": "20261007T120000Z-submission-experiment",
            "domain_gate": "passed",
        }
        validate_roi_readiness_payload(p)


if __name__ == "__main__":
    unittest.main()
