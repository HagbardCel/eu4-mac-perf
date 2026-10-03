import json
import sys
import tempfile
import unittest
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


def _wp9_archive_body_from_wp8(evidence_id: str, commit: str) -> dict | None:
    if not WP8_ARCHIVE.is_file():
        return None
    wp8 = json.loads(WP8_ARCHIVE.read_text(encoding="utf-8"))
    diagnosis = wp8["preflight"]["steady_state_tier1_diagnosis"]
    admission = tier1.evaluate_wp8_steady_state_training_v3(
        {"steady_state_tier1_diagnosis": diagnosis},
    )
    representative = {
        "status": admission["status"],
        "validation_scope": wp1.EXPECTED_SCOPE,
        "capture_kind": wp1.TIER1_V3_REQUALIFICATION_CAPTURE_KIND,
        "steady_state_tier1_diagnosis": diagnosis,
        "offline_tier1_v3_admission": admission,
        "test_library_sha256": wp8["executed_artifact_start"]["test_library_sha256"],
        "workload_harness_sha256": wp8["executed_artifact_start"]["workload_harness_sha256"],
    }
    return {
        "evidence_id": evidence_id,
        "recorded_at_utc": evidence_id,
        "git_commit": commit,
        "git_provenance_verified": True,
        "git_tree_clean": True,
        "identity_start": wp8["identity_start"],
        "identity_end": wp8["identity_end"],
        "executed_artifact_start": wp8["executed_artifact_start"],
        "executed_artifact_end": wp8["executed_artifact_end"],
        "policy_versions": {
            "wp9_tier1_v3_requalification": wp1.EXPECTED_WP9_TIER1_V3_REQUALIFICATION_POLICY,
            "tier1_causal_gate": wp1.EXPECTED_TIER1_V3_CAUSAL_POLICY,
            "reference_validity": wp1.EXPECTED_WP7_REFERENCE_VALIDITY,
            "cpu_measurement_variant": tier1.TIER1_V3_CPU_MEASUREMENT_VARIANT,
            "completion_wall_measurement_variant": tier1.TIER1_V3_WALL_MEASUREMENT_VARIANT,
            "measurement_variants": list(wp1.STEADY_STATE_MEASUREMENT_VARIANT_NAMES),
        },
        "preflight": {
            "purpose": wp1.EXPECTED_PURPOSE,
            "status": wp1.EXPECTED_REQUALIFICATION_STATUS,
            "validation_scope": wp1.EXPECTED_SCOPE,
            "representative_workloads": representative,
            "offline_tier1_v3_admission": admission,
            "overhead_gate": admission["status"],
        },
    }


class Wp9Tier1V3RequalificationTests(unittest.TestCase):
    def test_policy_versions_for_wp9_capture_kind(self):
        versions = model._offline_policy_versions(
            {"capture_kind": wp1.TIER1_V3_REQUALIFICATION_CAPTURE_KIND},
        )
        self.assertEqual(
            versions["wp9_tier1_v3_requalification"],
            model.WP9_TIER1_V3_REQUALIFICATION_POLICY,
        )
        self.assertEqual(versions["tier1_causal_gate"], tier1.TIER1_CAUSAL_POLICY_VERSION_V3)
        self.assertIn("cpu_measurement_variant", versions)
        self.assertNotIn("diagnostic_matrix", versions)

    def test_tier1_v3_requalification_workloads_embeds_admission(self):
        fake_diagnosis = {
            "steady_state_tier1_diagnosis": True,
            "recipes": [],
            "status": "complete",
            "validation_scope": model.TRAINING_ONLY_VALIDATION_SCOPE,
        }
        fake_admission = {
            "status": "unavailable",
            "policy_version": tier1.TIER1_CAUSAL_POLICY_VERSION_V3,
            "training_recipes": [{"recipe": "mesh", "status": "passed"}],
        }
        with mock.patch.object(
            model,
            "steady_state_tier1_diagnosis_workloads",
            return_value=fake_diagnosis,
        ), mock.patch.object(
            model.tier1,
            "evaluate_wp8_steady_state_training_v3",
            return_value=fake_admission,
        ):
            result = model.tier1_v3_requalification_workloads(
                executed_artifacts_start=_FROZEN_ARTIFACTS,
            )
        self.assertEqual(result["capture_kind"], wp1.TIER1_V3_REQUALIFICATION_CAPTURE_KIND)
        self.assertEqual(result["offline_tier1_v3_admission"], fake_admission)
        self.assertEqual(result["steady_state_tier1_diagnosis"], fake_diagnosis)

    def test_validate_tier1_v3_requalification_archive_accepts_wp8_derived_fixture(self):
        commit = "f62aa28eb6322fd08a8b5aeb51049c2958c89d07"
        evidence_id = "20261003T130000.000000Z-wp9test01"
        body = _wp9_archive_body_from_wp8(evidence_id, commit)
        if body is None:
            self.skipTest("WP8 archive missing")
        with tempfile.TemporaryDirectory(dir=wp1.ROOT) as temporary:
            path = Path(temporary) / "wp9-requal.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            entry = wp1.validate_tier1_v3_requalification_archive(
                json.loads(path.read_text(encoding="utf-8")),
                path,
            )
            self.assertEqual(entry["evidence_id"], evidence_id)

    def test_validate_tier1_v3_requalification_archive_rejects_wp7_capture_kind(self):
        commit = "cafebabe" * 5
        evidence_id = "20261003T120000.000000Z-deadbeef"
        body = {
            "preflight": {
                "purpose": wp1.EXPECTED_PURPOSE,
                "status": wp1.EXPECTED_REQUALIFICATION_STATUS,
                "validation_scope": wp1.EXPECTED_SCOPE,
                "representative_workloads": {
                    "capture_kind": wp1.CAUSAL_REQUALIFICATION_CAPTURE_KIND,
                    "recipes": [],
                },
            },
            "policy_versions": {},
            "identity_start": {"git_commit": commit, "git_tree_clean": True},
            "identity_end": {"git_commit": commit, "git_tree_clean": True},
            "executed_artifact_start": {"test_library_sha256": "a"},
            "executed_artifact_end": {"test_library_sha256": "a"},
            "evidence_id": evidence_id,
        }
        with tempfile.TemporaryDirectory(dir=wp1.ROOT) as temporary:
            path = Path(temporary) / "wp7-not-wp9.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            with self.assertRaises(ValueError):
                wp1.validate_tier1_v3_requalification_archive(
                    json.loads(path.read_text(encoding="utf-8")),
                    path,
                )

    def test_register_manifest_rejects_v3_requalification_complete(self):
        commit = "f62aa28eb6322fd08a8b5aeb51049c2958c89d07"
        evidence_id = "20261003T130000.000000Z-wp9reg01"
        body = _wp9_archive_body_from_wp8(evidence_id, commit)
        if body is None:
            self.skipTest("WP8 archive missing")
        with tempfile.TemporaryDirectory(dir=wp1.ROOT) as temporary:
            path = Path(temporary) / "wp9-requal.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            with self.assertRaises(ValueError):
                wp1.register_manifest(path)

    def test_discover_lists_manifest_wp9_requalification(self):
        commit = "f62aa28eb6322fd08a8b5aeb51049c2958c89d07"
        evidence_id = "20261003T140000.000000Z-wp9disc01"
        body = _wp9_archive_body_from_wp8(evidence_id, commit)
        if body is None:
            self.skipTest("WP8 archive missing")
        rel = f"analysis/evidence/frame-model-offline-{commit[:7]}-{evidence_id}.json"
        archive_path = wp1.ROOT / rel
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(json.dumps(body), encoding="utf-8")
        manifest = wp1._load_manifest()
        manifest.setdefault("tier1_v3_requalification_archives", []).append(
            {
                "work_package": "WP9",
                "evidence_id": evidence_id,
                "archive_path": rel,
            },
        )
        try:
            with mock.patch.object(wp1, "_load_manifest", return_value=manifest):
                discoveries = wp1.discover_wp1_archives()
            match = [item for item in discoveries if item.evidence_id == evidence_id]
            self.assertEqual(len(match), 1)
            self.assertEqual(match[0].role, "V3_REQUALIFICATION")
        finally:
            archive_path.unlink(missing_ok=True)
            manifest["tier1_v3_requalification_archives"] = [
                item
                for item in manifest.get("tier1_v3_requalification_archives") or []
                if item.get("evidence_id") != evidence_id
            ]


if __name__ == "__main__":
    unittest.main()
