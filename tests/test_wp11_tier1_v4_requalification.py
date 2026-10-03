import copy
import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_model as model  # noqa: E402
import frame_model_tier1_policy as tier1  # noqa: E402
import summarize_wp1_diagnosis as wp1  # noqa: E402

_FROZEN_ARTIFACTS = {
    "test_library_sha256": "frozen-test-library",
    "workload_harness_sha256": "frozen-workload-harness",
}

WP8_ARCHIVE = (
    Path(__file__).resolve().parents[1]
    / "analysis/evidence/frame-model-offline-f62aa28-20261003T123219.715675Z-3c1e0db7.json"
)


def _expand_steady_state_diagnosis_to_v4_trials(diagnosis: dict) -> dict:
    block = copy.deepcopy(diagnosis)
    for recipe_entry in block.get("recipes") or []:
        base_trials = recipe_entry.get("trials") or []
        expanded: list[dict] = []
        for trial_id in range(tier1.TIER1_V4_TRIAL_COUNT):
            source = copy.deepcopy(base_trials[trial_id % len(base_trials)])
            source["trial"] = trial_id
            source["order"] = tier1._expected_stage_order_for_trial(trial_id)
            source["variant_order"] = tier1._expected_variant_order_for_trial(trial_id)
            expanded.append(source)
        recipe_entry["trials"] = expanded
    block["paired_trial_count"] = tier1.TIER1_V4_TRIAL_COUNT
    return block


def _wp11_archive_body_from_wp8(evidence_id: str, commit: str) -> dict | None:
    if not WP8_ARCHIVE.is_file():
        return None
    wp8 = json.loads(WP8_ARCHIVE.read_text(encoding="utf-8"))
    diagnosis = _expand_steady_state_diagnosis_to_v4_trials(
        wp8["preflight"]["steady_state_tier1_diagnosis"],
    )
    admission = tier1.evaluate_wp8_steady_state_training_v4(
        {"steady_state_tier1_diagnosis": diagnosis},
    )
    representative = {
        "status": admission["status"],
        "validation_scope": wp1.EXPECTED_SCOPE,
        "capture_kind": wp1.TIER1_V4_REQUALIFICATION_CAPTURE_KIND,
        "steady_state_tier1_diagnosis": diagnosis,
        "offline_tier1_v4_admission": admission,
        "test_library_sha256": wp8["executed_artifact_start"]["test_library_sha256"],
        "workload_harness_sha256": wp8["executed_artifact_start"]["workload_harness_sha256"],
    }
    return {
        "evidence_id": evidence_id,
        "recorded_at_utc": evidence_id,
        "git_commit": commit,
        "git_provenance_verified": True,
        "git_tree_clean": True,
        "environment": wp8.get("environment")
        or {"platform": "darwin", "arch": "arm64"},
        "identity_start": wp8["identity_start"],
        "identity_end": wp8["identity_end"],
        "executed_artifact_start": wp8["executed_artifact_start"],
        "executed_artifact_end": wp8["executed_artifact_end"],
        "policy_versions": {
            "wp11_tier1_v4_requalification": wp1.EXPECTED_WP11_TIER1_V4_REQUALIFICATION_POLICY,
            "tier1_causal_gate": wp1.EXPECTED_TIER1_V4_CAUSAL_POLICY,
            "reference_validity": wp1.EXPECTED_WP7_REFERENCE_VALIDITY,
            "cpu_measurement_variant": tier1.TIER1_V4_CPU_MEASUREMENT_VARIANT,
            "completion_wall_measurement_variant": tier1.TIER1_V4_WALL_MEASUREMENT_VARIANT,
            "paired_trial_count": tier1.TIER1_V4_TRIAL_COUNT,
            "measurement_variants": list(wp1.STEADY_STATE_MEASUREMENT_VARIANT_NAMES),
        },
        "preflight": {
            "purpose": wp1.EXPECTED_PURPOSE,
            "status": wp1.EXPECTED_REQUALIFICATION_STATUS,
            "validation_scope": wp1.EXPECTED_SCOPE,
            "representative_workloads": representative,
            "offline_tier1_v4_admission": admission,
            "overhead_gate": admission["status"],
        },
    }


class Wp11Tier1V4RequalificationTests(unittest.TestCase):
    def test_policy_versions_for_wp11_capture_kind(self):
        versions = model._offline_policy_versions(
            {"capture_kind": wp1.TIER1_V4_REQUALIFICATION_CAPTURE_KIND},
        )
        self.assertEqual(
            versions["wp11_tier1_v4_requalification"],
            model.WP11_TIER1_V4_REQUALIFICATION_POLICY,
        )
        self.assertEqual(versions["tier1_causal_gate"], tier1.TIER1_CAUSAL_POLICY_VERSION_V4)
        self.assertEqual(versions["paired_trial_count"], tier1.TIER1_V4_TRIAL_COUNT)

    def test_tier1_v4_requalification_workloads_use_21_trials(self):
        with mock.patch.object(
            model,
            "steady_state_tier1_diagnosis_workloads",
        ) as diagnosis_mock:
            diagnosis_mock.return_value = {
                "recipes": [],
                "steady_state_tier1_diagnosis": True,
                "paired_trial_count": 21,
            }
            with mock.patch.object(
                model.tier1,
                "evaluate_wp8_steady_state_training_v4",
                return_value={"status": "passed", "policy_version": tier1.TIER1_CAUSAL_POLICY_VERSION_V4},
            ):
                model.tier1_v4_requalification_workloads(executed_artifacts_start=_FROZEN_ARTIFACTS)
        diagnosis_mock.assert_called_once()
        self.assertEqual(
            diagnosis_mock.call_args.kwargs.get("trial_count"),
            tier1.TIER1_V4_TRIAL_COUNT,
        )

    def test_validate_rejects_tampered_trials_with_unchanged_admission(self):
        commit = "f62aa28eb6322fd08a8b5aeb51049c2958c89d07"
        evidence_id = "20261003T170000.000000Z-wp11tamper"
        body = _wp11_archive_body_from_wp8(evidence_id, commit)
        if body is None:
            self.skipTest("WP8 archive missing")
        body = copy.deepcopy(body)
        diagnosis = body["preflight"]["representative_workloads"]["steady_state_tier1_diagnosis"]
        variant = diagnosis["recipes"][0]["trials"][0]["variants"][tier1.TIER1_V4_CPU_MEASUREMENT_VARIANT]
        variant["stages"]["counters"]["cpu_ns"] = variant["stages"]["counters"]["cpu_ns"] * 1000
        with tempfile.TemporaryDirectory(dir=wp1.ROOT) as temporary:
            path = Path(temporary) / "wp11-tampered.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                wp1.validate_tier1_v4_requalification_archive(
                    json.loads(path.read_text(encoding="utf-8")),
                    path,
                )
            self.assertIn("replay", str(raised.exception).lower())

    def test_validate_tier1_v4_requalification_archive_accepts_wp8_derived_fixture(self):
        commit = "f62aa28eb6322fd08a8b5aeb51049c2958c89d07"
        evidence_id = "20261003T170000.000000Z-wp11test01"
        body = _wp11_archive_body_from_wp8(evidence_id, commit)
        if body is None:
            self.skipTest("WP8 archive missing")
        with tempfile.TemporaryDirectory(dir=wp1.ROOT) as temporary:
            path = Path(temporary) / "wp11-requal.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            entry = wp1.validate_tier1_v4_requalification_archive(
                json.loads(path.read_text(encoding="utf-8")),
                path,
            )
            self.assertEqual(entry["evidence_id"], evidence_id)

    def test_register_manifest_rejects_v4_requalification_complete(self):
        commit = "f62aa28eb6322fd08a8b5aeb51049c2958c89d07"
        evidence_id = "20261003T170000.000000Z-wp11reg01"
        body = _wp11_archive_body_from_wp8(evidence_id, commit)
        if body is None:
            self.skipTest("WP8 archive missing")
        with tempfile.TemporaryDirectory(dir=wp1.ROOT) as temporary:
            path = Path(temporary) / "wp11-requal.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            with self.assertRaises(ValueError):
                wp1.register_manifest(path)

    def test_manifest_wp7_bucket_rejects_wp11_capture_kind(self):
        with self.assertRaises(ValueError):
            wp1._require_manifest_capture_kind_consistent(
                "REQUALIFICATION",
                wp1.TIER1_V4_REQUALIFICATION_CAPTURE_KIND,
            )


if __name__ == "__main__":
    unittest.main()
