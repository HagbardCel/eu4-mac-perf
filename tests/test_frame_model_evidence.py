import csv
import gzip
import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import eu4_frame_model as model
from frame_model_evidence import (
    _finalize_readiness_log,
    _identity_capture_consistent,
    persist_capture_time_readiness_log,
    reconstructed_capture_identity,
    seal_artifact_pair,
    seal_run_evidence,
    verify_run_evidence,
)


class FrameModelEvidenceTests(unittest.TestCase):
    def test_read_rows_gzip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "telemetry.csv.gz"
            with gzip.open(path, "wt", newline="") as handle:
                csv.writer(handle).writerow(["H", "3"])
                csv.writer(handle).writerow(["F"] + ["0"] * 30)
            rows = model.read_rows(path)
            self.assertEqual(rows[0][0], "H")

    def test_seal_writes_gzip_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            plain = run_dir / "telemetry.csv"
            plain.write_text("H,3\n", encoding="ascii")
            (run_dir / "manifest.json").write_text('{"report_kind":"intrusive_diagnostic"}', encoding="ascii")
            entry = seal_artifact_pair(run_dir, "telemetry.csv", "telemetry.csv.gz")
            self.assertIsNotNone(entry)
            self.assertTrue((run_dir / "telemetry.csv.gz").is_file())
            evidence = seal_run_evidence(run_dir, write_game_log=False)
            self.assertIn("capture_identity", evidence)
            self.assertEqual(evidence["capture_identity"]["status"], "reconstructed_incomplete")
            self.assertIn("telemetry.csv.gz", evidence["capture_inventory"])

    def test_logical_hash_survives_plain_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            plain = run_dir / "telemetry.csv"
            payload = b"row-one\nrow-two\n"
            plain.write_bytes(payload)
            first = seal_artifact_pair(run_dir, "telemetry.csv", "telemetry.csv.gz")
            self.assertIsNotNone(first)
            plain.unlink()
            second = seal_artifact_pair(run_dir, "telemetry.csv", "telemetry.csv.gz")
            self.assertIsNotNone(second)
            self.assertEqual(first["uncompressed_bytes"], second["uncompressed_bytes"])
            self.assertEqual(first["uncompressed_sha256"], second["uncompressed_sha256"])

    def test_verify_profile_assertion(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text('{"report_kind":"intrusive_diagnostic"}', encoding="ascii")
            evidence = seal_run_evidence(run_dir, write_game_log=False, capsule_profile="intrusive_capture_only_v1")
            self.assertEqual(evidence["capsule_profile"], "intrusive_capture_only_v1")
            with self.assertRaises(ValueError):
                verify_run_evidence(run_dir, profile="intrusive_complete_v1")

    def _identity_payload(self, **overrides):
        base = {
            "identity_start": {
                "git_commit": "abc123",
                "git_commit_short": "abc123",
                "controller_sha256": "ctrl",
                "git_tree_clean": True,
            },
            "identity_end": {
                "git_commit": "abc123",
                "git_commit_short": "abc123",
                "controller_sha256": "ctrl",
                "git_tree_clean": True,
            },
            "artifact_start": {"telemetry.csv.gz": "deadbeef"},
            "artifact_end": {"telemetry.csv.gz": "deadbeef"},
        }
        base.update(overrides)
        return base

    def test_identity_direct_when_snapshots_match(self):
        payload = self._identity_payload()
        self.assertEqual(_identity_capture_consistent(payload), "direct")
        identity = reconstructed_capture_identity({}, identity_at_capture=payload)
        self.assertEqual(identity["policy"], "recorded_direct")

    def test_identity_partial_without_artifacts(self):
        payload = self._identity_payload(artifact_start={}, artifact_end={})
        self.assertEqual(_identity_capture_consistent(payload), "partial")
        identity = reconstructed_capture_identity({}, identity_at_capture=payload)
        self.assertEqual(identity["policy"], "recorded_partial")

    def test_identity_partial_without_controller_sha(self):
        start = {
            "git_commit": "abc123",
            "controller_sha256": None,
            "git_tree_clean": True,
        }
        payload = self._identity_payload(
            identity_start=start,
            identity_end={**start, "controller_sha256": None},
        )
        self.assertEqual(_identity_capture_consistent(payload), "partial")

    def test_verify_requires_artifact_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(
                '{"report_kind":"intrusive_diagnostic"}', encoding="ascii"
            )
            for name in (
                "events.jsonl",
                "auto-probe.csv",
                "control.bin",
                "powermetrics.stderr",
                "ready-scene.png",
                "game-ready.log",
            ):
                (run_dir / name).write_bytes(b"x")
            for plain, payload in (
                ("telemetry.csv", b"H\n"),
                ("power.samples.json", b"[]\n"),
                ("powermetrics.pliststream", b"\0"),
            ):
                (run_dir / plain).write_bytes(payload)
            seal_run_evidence(run_dir, write_game_log=False, capsule_profile="intrusive_capture_only_v1")
            raw_path = run_dir / "raw_evidence.json"
            sealed = json.loads(raw_path.read_text(encoding="utf-8"))
            sealed["artifacts"] = {}
            raw_path.write_text(json.dumps(sealed, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing sealed artifact metadata"):
                verify_run_evidence(run_dir, profile="intrusive_capture_only_v1")

    def test_finalize_readiness_enriches_incomplete_prior_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            log = run_dir / "game-ready.log"
            log.write_text("Launching SINGLEPLAYER-game\n", encoding="utf-8")
            wall_ns = 1_700_000_000_000_000_000
            manifest = {"clock_anchor": {"wall_ns": wall_ns}}
            readiness = {
                "status": "present_on_disk",
                "committed_filename": "game-ready.log",
            }
            prior = {
                "origin": {
                    "status": "reconstructed_posthoc_anchored",
                    "extraction_method": None,
                    "phase_c_wall_window_utc": None,
                },
                "file": {"sha256": __import__("hashlib").sha256(log.read_bytes()).hexdigest()},
            }
            merged = _finalize_readiness_log(run_dir, readiness, manifest, prior_readiness=prior)
            origin = merged["origin"]
            self.assertEqual(origin["extraction_method"], "committed_capture_time_log_with_manifest_window")
            self.assertIsNotNone(origin["phase_c_wall_window_utc"])

    @patch("autonomous_runner.fresh_game_log", return_value="Launching SINGLEPLAYER-game\n")
    def test_capture_time_readiness_origin_survives_finalize(self, _fresh):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            game_log = run_dir / "game.log"
            game_log.write_text("old\n", encoding="utf-8")
            manifest_readiness = persist_capture_time_readiness_log(run_dir, game_log, b"old\n")
            manifest = {"readiness_log": manifest_readiness}
            readiness = {
                "status": "present_on_disk",
                "committed_filename": "game-ready.log",
            }
            merged = _finalize_readiness_log(run_dir, readiness, manifest)
            self.assertIsInstance(merged["origin"], dict)
            self.assertEqual(merged["origin"]["status"], "captured_at_readiness")
            self.assertEqual(merged["origin"]["extraction_method"], "fresh_game_log_delta")

    def test_seal_rejects_plain_gzip_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            plain = run_dir / "telemetry.csv"
            plain.write_bytes(b"plain-version\n")
            with gzip.open(run_dir / "telemetry.csv.gz", "wb") as handle:
                handle.write(b"different-version\n")
            with self.assertRaisesRegex(ValueError, "disagrees with committed"):
                seal_artifact_pair(run_dir, "telemetry.csv", "telemetry.csv.gz")

    def test_seal_plain_only_records_power_samples_after_gzip(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text('{"power":{"samples":3}}', encoding="ascii")
            (run_dir / "power.samples.json").write_text("[1,2,3]", encoding="ascii")
            item = {"timestamp": "2026-01-01T00:00:00Z"}
            data = plistlib.dumps(item)
            with gzip.open(run_dir / "powermetrics.pliststream.gz", "wb") as handle:
                handle.write(data + b"\0")
            evidence = seal_run_evidence(run_dir, write_game_log=False)
            interpretation = evidence["powermetrics_plist_interpretation"]
            self.assertEqual(interpretation["power_samples_json_count"], 3)

    def test_verify_accepts_reconstructed_readiness_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(
                '{"report_kind":"intrusive_diagnostic"}', encoding="ascii"
            )
            for name in (
                "events.jsonl",
                "auto-probe.csv",
                "control.bin",
                "powermetrics.stderr",
                "ready-scene.png",
                "game-ready.reconstructed.log",
            ):
                (run_dir / name).write_bytes(b"x")
            for plain, payload in (
                ("telemetry.csv", b"H\n"),
                ("power.samples.json", b"[]\n"),
                ("powermetrics.pliststream", b"\0"),
            ):
                (run_dir / plain).write_bytes(payload)
            seal_run_evidence(run_dir, write_game_log=False, capsule_profile="intrusive_capture_only_v1")
            verify_run_evidence(run_dir, profile="intrusive_capture_only_v1")
