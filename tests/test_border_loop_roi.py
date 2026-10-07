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


def _ready_offline_authorized(commit: str = "a" * 40) -> dict:
    p = _base_payload()
    p["roi_instrumentation_status"] = "READY_OFFLINE"
    p["roi_gate"] = "PENDING_NUMERIC_EVIDENCE"
    p.pop("roi_gate_reason", None)
    p["mode0_domain_status"] = None
    p["semantic_eliminations_per_frame"] = None
    p["implementable_eliminations_per_frame"] = None
    p["structural_eliminations_per_frame"] = None
    p["estimated_implementable_eliminations_per_second"] = None
    p["ready_for_single_venice_observer_run"] = False
    p["runtime_gate_0_probe_ready"] = True
    p["runtime_detour_installer_ready"] = True
    p["self_tests"] = _all_pass_buckets()
    p["offline_verification"] = {
        "verified_commit": commit,
        "darwin_full_suite": {
            "command": "python3 -m unittest discover -s tests",
            "result": "PASS",
            "tests_run": 1,
        },
        "note": "test fixture",
    }
    p["domain_census_live"] = {
        "status": "PENDING",
        "counter_schema_version": 6,
        "offline_verified_commit": commit,
        "ready_for_single_venice_census_run": True,
    }
    return p


def _pending_census_payload() -> dict:
    p = _ready_offline_authorized()
    p["domain_census_live"]["ready_for_single_venice_census_run"] = False
    return p


def _valid_mismatch_delta_proven_payload() -> dict:
    offline = "a" * 40
    run_commit = "c" * 40
    auth = "b" * 40
    run_id = "fixture-run-submission-experiment"
    p = _base_payload()
    p["roi_gate"] = "FAIL_BELOW_THRESHOLD"
    p["roi_gate_reason"] = "MODE0_DOMAIN_EMPTY"
    p["mode0_domain_status"] = "empty"
    p["semantic_eliminations_per_frame"] = 0.0
    p["implementable_eliminations_per_frame"] = 0.0
    p["structural_eliminations_per_frame"] = 0.0
    p["estimated_implementable_eliminations_per_second"] = 0.0
    p["offline_verification"] = {
        "verified_commit": offline,
        "darwin_full_suite": {
            "command": "python3 -m unittest discover -s tests",
            "result": "PASS",
            "tests_run": 1,
        },
        "note": "test fixture",
    }
    delta = {
        "run": run_id,
        "offline_verified_commit": offline,
        "authorization_commit": auth,
        "run_git_commit": run_commit,
        "post_authorization_code_commits": [run_commit],
        "changed_files": ["benchmark/submission_experiment.py"],
        "scope": "test fixture delta",
        "measurement_code_changed": False,
        "dylib_build_inputs_changed": False,
        "counter_or_gate_semantics_changed": False,
    }
    p["domain_census_live"] = {
        "status": "PASS_VENICE_MEASURED",
        "counter_schema_version": 6,
        "offline_verified_commit": offline,
        "run_git_commit": run_commit,
        "run": run_id,
        "domain_gate": "passed",
        "ready_for_single_venice_census_run": False,
        "run_provenance_delta": copy.deepcopy(delta),
    }
    return p


class BorderLoopRoiTests(unittest.TestCase):
    def test_readiness_artifact(self) -> None:
        validate_roi_readiness_payload(_base_payload())

    def test_ready_requires_all_pass(self) -> None:
        p = _ready_offline_authorized()
        p["self_tests"]["gateway_runtime"] = "PENDING"
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_census_auth_pass_venice_measured(self) -> None:
        validate_roi_readiness_payload(_base_payload())

    def test_census_auth_pending_ready_fails(self) -> None:
        p = _pending_census_payload()
        p["roi_instrumentation_status"] = "PENDING"
        p["domain_census_live"]["ready_for_single_venice_census_run"] = True
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_census_auth_ready_offline_match_passes(self) -> None:
        validate_roi_readiness_payload(_ready_offline_authorized("c" * 40))

    def test_census_auth_ready_offline_missing_commit_fails(self) -> None:
        p = _ready_offline_authorized()
        p["offline_verification"]["verified_commit"] = None
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_census_auth_ready_offline_mismatch_fails(self) -> None:
        p = _ready_offline_authorized("d" * 40)
        p["domain_census_live"]["offline_verified_commit"] = "e" * 40
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_census_auth_ready_offline_not_ready_fails(self) -> None:
        p = _ready_offline_authorized()
        p["domain_census_live"]["ready_for_single_venice_census_run"] = False
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_pending_with_mixed_buckets(self) -> None:
        p = _pending_census_payload()
        p["roi_gate"] = "PENDING_NUMERIC_EVIDENCE"
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
        p = _pending_census_payload()
        p["roi_gate"] = "PENDING_NUMERIC_EVIDENCE"
        p["roi_instrumentation_status"] = "PENDING"
        p["ready_for_single_venice_observer_run"] = False
        p["self_tests"] = _all_pass_buckets()
        p["self_tests"]["deferred_install_lifecycle"] = "PENDING"
        p["self_tests"]["gateway_runtime"] = "PENDING"
        validate_roi_readiness_payload(p)

    def test_pending_all_pass_invalid(self) -> None:
        p = _pending_census_payload()
        p["roi_gate"] = "PENDING_NUMERIC_EVIDENCE"
        p["roi_instrumentation_status"] = "PENDING"
        p["ready_for_single_venice_observer_run"] = False
        p["self_tests"] = _all_pass_buckets()
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_failed_self_test(self) -> None:
        p = _pending_census_payload()
        p["roi_gate"] = "PENDING_NUMERIC_EVIDENCE"
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
        p = _pending_census_payload()
        p["roi_gate"] = "FAIL_BELOW_THRESHOLD"
        p["roi_gate_reason"] = "MODE0_DOMAIN_EMPTY"
        p["mode0_domain_status"] = "empty"
        p["semantic_eliminations_per_frame"] = 0.0
        p["implementable_eliminations_per_frame"] = 0.0
        p["structural_eliminations_per_frame"] = 0.0
        p["estimated_implementable_eliminations_per_second"] = 0.0
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_fail_below_threshold_minimal_pass_stub_fails(self) -> None:
        p = _base_payload()
        p["domain_census_live"] = {
            "status": "PASS_VENICE_MEASURED",
            "counter_schema_version": 6,
            "run": "20261007T120000Z-submission-experiment",
            "domain_gate": "passed",
        }
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_pass_venice_missing_offline_commit_fails(self) -> None:
        p = _base_payload()
        p["offline_verification"]["verified_commit"] = None
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_pass_venice_offline_mismatch_fails(self) -> None:
        p = _base_payload()
        p["domain_census_live"]["offline_verified_commit"] = "f" * 40
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_pass_venice_census_ready_true_fails(self) -> None:
        p = _base_payload()
        p["domain_census_live"]["ready_for_single_venice_census_run"] = True
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_pass_venice_commit_mismatch_without_delta_fails(self) -> None:
        p = _valid_mismatch_delta_proven_payload()
        del p["domain_census_live"]["run_provenance_delta"]
        with self.assertRaises(AssertionError):
            validate_roi_readiness_payload(p)

    def test_mismatch_delta_proven_fixture_passes(self) -> None:
        validate_roi_readiness_payload(_valid_mismatch_delta_proven_payload())

    def test_mismatch_delta_binding_and_shape_failures(self) -> None:
        base = _valid_mismatch_delta_proven_payload()
        mutations: list[tuple[str, object]] = [
            ("delta.run", lambda p: p["domain_census_live"]["run_provenance_delta"].update({"run": "wrong"})),
            (
                "delta.offline_verified_commit",
                lambda p: p["domain_census_live"]["run_provenance_delta"].update(
                    {"offline_verified_commit": "d" * 40}
                ),
            ),
            (
                "delta.run_git_commit",
                lambda p: p["domain_census_live"]["run_provenance_delta"].update(
                    {"run_git_commit": "e" * 40}
                ),
            ),
            (
                "empty post_authorization_code_commits",
                lambda p: p["domain_census_live"]["run_provenance_delta"].update(
                    {"post_authorization_code_commits": []}
                ),
            ),
            (
                "empty changed_files",
                lambda p: p["domain_census_live"]["run_provenance_delta"].update({"changed_files": []}),
            ),
            (
                "empty scope",
                lambda p: p["domain_census_live"]["run_provenance_delta"].update({"scope": ""}),
            ),
            (
                "measurement_code_changed=0",
                lambda p: p["domain_census_live"]["run_provenance_delta"].update(
                    {"measurement_code_changed": 0}
                ),
            ),
            (
                "missing authorization_commit",
                lambda p: p["domain_census_live"]["run_provenance_delta"].pop("authorization_commit"),
            ),
        ]
        for label, mutate in mutations:
            p = copy.deepcopy(base)
            mutate(p)
            with self.subTest(label=label):
                with self.assertRaises(AssertionError):
                    validate_roi_readiness_payload(p)


if __name__ == "__main__":
    unittest.main()
