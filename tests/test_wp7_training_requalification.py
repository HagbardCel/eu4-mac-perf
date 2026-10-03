import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_model as model  # noqa: E402
import summarize_wp1_diagnosis as wp1  # noqa: E402

_FROZEN_ARTIFACTS = {
    "test_library_sha256": "frozen-test-library",
    "workload_harness_sha256": "frozen-workload-harness",
}


def _causal_trial(trial: int) -> dict:
    return {
        "trial": trial,
        "order": ["bare", "reference", "counters"],
        "stages": {
            stage: {"elapsed_ns": 1, "cpu_ns": 1}
            for stage in wp1.CAUSAL_REQUALIFICATION_STAGES
        },
    }


def _training_recipe(name: str) -> dict:
    return {
        "recipe": {"name": name, "role": "training"},
        "gates": {key: {"status": "passed"} for key in wp1.CAUSAL_REQUALIFICATION_GATE_KEYS},
        "trials": [_causal_trial(trial) for trial in range(wp1.CAUSAL_REQUALIFICATION_TRIAL_COUNT)],
    }


def _validate_fixture(path: Path) -> None:
    wp1.validate_requalification_archive(json.loads(path.read_text(encoding="utf-8")), path)


def _requalification_archive_body(evidence_id: str, commit: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "recorded_at_utc": evidence_id,
        "git_commit": commit,
        "git_provenance_verified": True,
        "git_tree_clean": True,
        "identity_start": {"git_commit": commit, "git_tree_clean": True},
        "identity_end": {"git_commit": commit, "git_tree_clean": True},
        "executed_artifact_start": {"test_library_sha256": "a"},
        "executed_artifact_end": {"test_library_sha256": "a"},
        "policy_versions": {
            "wp7_causal_training_requalification": wp1.EXPECTED_WP7_CAUSAL_REQUALIFICATION_POLICY,
            "tier1_causal_gate": wp1.EXPECTED_TIER1_CAUSAL_POLICY,
            "reference_validity": wp1.EXPECTED_WP7_REFERENCE_VALIDITY,
            "seven_pair_gate": model.OFFLINE_SEVEN_PAIR_POLICY_VERSION,
        },
        "preflight": {
            "purpose": wp1.EXPECTED_PURPOSE,
            "status": wp1.EXPECTED_REQUALIFICATION_STATUS,
            "validation_scope": wp1.EXPECTED_SCOPE,
            "representative_workloads": {
                "capture_kind": wp1.CAUSAL_REQUALIFICATION_CAPTURE_KIND,
                "recipes": [_training_recipe(name) for name in wp1.TRAINING_RECIPES],
            },
        },
    }


class Wp7TrainingRequalificationTests(unittest.TestCase):
    def test_offline_workloads_causal_only_runs_causal_stages(self):
        stages: list[str] = []

        def fake_stage(root, recipe, trial, stage):
            stages.append(stage)
            return {"elapsed_ns": 1, "cpu_ns": 1}, {}

        recipes = [
            {"name": name, "role": "training", "path": Path("/tmp")}
            for name in wp1.TRAINING_RECIPES
        ]
        with mock.patch.object(model.workload, "recipes", return_value=recipes), mock.patch.object(
            model,
            "_offline_workload_stage",
            side_effect=fake_stage,
        ), mock.patch.object(
            model.tier1,
            "summarize_admission",
            return_value={"status": "passed", "validation_scope": model.TRAINING_ONLY_VALIDATION_SCOPE},
        ):
            result = model.offline_workloads_causal_only(
                executed_artifacts_start=_FROZEN_ARTIFACTS,
            )
        self.assertEqual(set(stages), {"bare", "reference", "counters"})
        self.assertEqual(len(stages), 7 * 3 * len(wp1.TRAINING_RECIPES))
        self.assertEqual(result["capture_kind"], wp1.CAUSAL_REQUALIFICATION_CAPTURE_KIND)
        self.assertEqual(
            result["offline_causal_admission"]["validation_scope"],
            model.TRAINING_ONLY_VALIDATION_SCOPE,
        )

    def test_policy_versions_for_causal_capture_kind(self):
        versions = model._offline_policy_versions(
            {"capture_kind": wp1.CAUSAL_REQUALIFICATION_CAPTURE_KIND},
        )
        self.assertEqual(
            versions["wp7_causal_training_requalification"],
            model.WP7_CAUSAL_TRAINING_REQUALIFICATION_POLICY,
        )
        self.assertIn("tier1_causal_gate", versions)
        self.assertNotIn("diagnostic_matrix", versions)
        self.assertNotIn("ablation_gate", versions)

    def test_validate_requalification_archive_accepts_causal_capture(self):
        commit = "cafebabe" * 5
        evidence_id = "20261003T120000.000000Z-deadbeef"
        with tempfile.TemporaryDirectory(dir=wp1.ROOT) as temporary:
            path = Path(temporary) / "wp7-requal.json"
            path.write_text(json.dumps(_requalification_archive_body(evidence_id, commit)), encoding="utf-8")
            entry = wp1.validate_requalification_archive(json.loads(path.read_text()), path)
            self.assertEqual(entry["evidence_id"], evidence_id)

    def test_validate_requalification_archive_rejects_wrong_policy_versions(self):
        commit = "cafebabe" * 5
        evidence_id = "20261003T120000.000000Z-deadbeef"
        body = _requalification_archive_body(evidence_id, commit)
        body["policy_versions"]["tier1_causal_gate"] = "wrong_policy"
        with tempfile.TemporaryDirectory(dir=wp1.ROOT) as temporary:
            path = Path(temporary) / "wp7-bad-policy.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            with self.assertRaises(ValueError):
                wp1.validate_requalification_archive(json.loads(path.read_text()), path)

    def _reject_fixture(self, body: dict, filename: str) -> None:
        with tempfile.TemporaryDirectory(dir=wp1.ROOT) as temporary:
            path = Path(temporary) / filename
            path.write_text(json.dumps(body), encoding="utf-8")
            with self.assertRaises(ValueError):
                _validate_fixture(path)

    def test_validate_requalification_archive_rejects_held_out_recipe(self):
        body = _requalification_archive_body("20261003T120000.000000Z-deadbeef", "cafebabe" * 5)
        body["preflight"]["representative_workloads"]["recipes"].append(
            {"recipe": {"name": "held_out", "role": "held_out"}, "gates": {}, "trials": []},
        )
        self._reject_fixture(body, "wp7-held-out.json")

    def test_validate_requalification_archive_rejects_extra_recipe(self):
        body = _requalification_archive_body("20261003T120000.000000Z-deadbeef", "cafebabe" * 5)
        body["preflight"]["representative_workloads"]["recipes"].append(_training_recipe("mesh"))
        self._reject_fixture(body, "wp7-extra-recipe.json")

    def test_validate_requalification_archive_rejects_wrong_role(self):
        body = _requalification_archive_body("20261003T120000.000000Z-deadbeef", "cafebabe" * 5)
        body["preflight"]["representative_workloads"]["recipes"][0]["recipe"]["role"] = "held_out"
        self._reject_fixture(body, "wp7-wrong-role.json")

    def test_validate_requalification_archive_rejects_wrong_trial_count(self):
        body = _requalification_archive_body("20261003T120000.000000Z-deadbeef", "cafebabe" * 5)
        body["preflight"]["representative_workloads"]["recipes"][0]["trials"] = [_causal_trial(0)]
        self._reject_fixture(body, "wp7-wrong-trial-count.json")

    def test_validate_requalification_archive_rejects_extra_trial_stage(self):
        body = _requalification_archive_body("20261003T120000.000000Z-deadbeef", "cafebabe" * 5)
        body["preflight"]["representative_workloads"]["recipes"][0]["trials"][0]["stages"]["sampled"] = {
            "elapsed_ns": 1,
            "cpu_ns": 1,
        }
        self._reject_fixture(body, "wp7-extra-stage.json")

    def test_validate_requalification_archive_rejects_diagnostic_matrix(self):
        commit = "cafebabe" * 5
        evidence_id = "20261003T120000.000000Z-deadbeef"
        body = _requalification_archive_body(evidence_id, commit)
        body["preflight"]["representative_workloads"]["recipes"][0]["diagnostic_matrix"] = {"policy_version": "x"}
        with tempfile.TemporaryDirectory(dir=wp1.ROOT) as temporary:
            path = Path(temporary) / "wp7-bad.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            with self.assertRaises(ValueError):
                wp1.validate_requalification_archive(json.loads(path.read_text()), path)

    def test_register_manifest_rejects_requalification_complete(self):
        commit = "cafebabe" * 5
        evidence_id = "20261003T120000.000000Z-deadbeef"
        with tempfile.TemporaryDirectory(dir=wp1.ROOT) as temporary:
            path = Path(temporary) / "wp7-requal.json"
            path.write_text(json.dumps(_requalification_archive_body(evidence_id, commit)), encoding="utf-8")
            with self.assertRaises(ValueError):
                wp1.register_manifest(path)

    def test_discover_lists_manifest_wp7_requalification(self):
        commit = "cafebabe" * 5
        evidence_id = "20261003T130000.000000Z-wp7test01"
        archive_body = _requalification_archive_body(evidence_id, commit)
        rel = f"analysis/evidence/frame-model-offline-{commit[:7]}-{evidence_id}.json"
        archive_path = wp1.ROOT / rel
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(json.dumps(archive_body), encoding="utf-8")
        manifest = wp1._load_manifest()
        manifest.setdefault("requalification_archives", []).append(
            {
                "work_package": "WP7",
                "evidence_id": evidence_id,
                "archive_path": rel,
            },
        )
        try:
            with mock.patch.object(wp1, "_load_manifest", return_value=manifest):
                discoveries = wp1.discover_wp1_archives()
            match = [item for item in discoveries if item.evidence_id == evidence_id]
            self.assertEqual(len(match), 1)
            self.assertEqual(match[0].role, "REQUALIFICATION")
        finally:
            archive_path.unlink(missing_ok=True)
            manifest["requalification_archives"] = [
                item
                for item in manifest.get("requalification_archives") or []
                if item.get("evidence_id") != evidence_id
            ]


if __name__ == "__main__":
    unittest.main()
