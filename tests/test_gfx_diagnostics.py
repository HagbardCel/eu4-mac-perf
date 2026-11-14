"""PR 1 acceptance: capture validity and the GPU execution-health gate.

These tests are native/synthetic.  They encode the historical failures that the
health gate must catch on the first relevant diagnostic execution:

* a rejected draw whose command buffer would otherwise look fine;
* a shader-cache/configuration failure classified as instrumentation, not as a
  renderer defect;
* repeated validation messages collapsed into one bounded entry;
* command-buffer completion must not be reported as pixel correctness.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_diagnostics as diag  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value) + "\n")


def write_checkpoint(directory: Path, *, width: int = 4, height: int = 3,
                     status: dict | None = None, gpu: dict | None = None,
                     payload: bytes | None = None, name: str = "checkpoint-after-draw-1") -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    payload = payload if payload is not None else bytes(width * height * 4)
    raw_name = name + ".bgra"
    (directory / raw_name).write_bytes(payload)
    record = {
        "schema": "gfx-checkpoint/v1",
        "checkpoint": "after-draw",
        "caller": {"rva": "0xedfcd1"},
        "target": {"width": width, "height": height, "row_stride": width * 4,
                   "pixel_format": "BGRA8", "origin": "top_left"},
        "gpu": gpu or {"command_status": 4, "completed": True},
        "capture": {"raw_file": raw_name, "raw_bytes": len(payload),
                    "raw_sha256": _sha(payload)},
        "status": status or {"draw_reached": 1, "draw_encoded": 1,
                             "gpu_completed": 1, "pixels_captured": 1},
    }
    write_json(directory / (name + ".json"), record)
    return record


class CaptureValidityTests(unittest.TestCase):
    def test_valid_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_checkpoint(root)
            record = diag._read_json(root / "checkpoint-after-draw-1.json")
            result = diag.assess_checkpoint(record, base=root)
            self.assertEqual(result["status"], diag.VALID)
            self.assertEqual(result["reasons"], [])

    def test_truncated_payload_is_invalid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = write_checkpoint(root)
            (root / record["capture"]["raw_file"]).write_bytes(b"\x00" * 4)
            result = diag.assess_checkpoint(record, base=root)
            self.assertEqual(result["status"], diag.INVALID)
            self.assertTrue(any(reason.startswith("payload_matches:false") for reason in result["reasons"]))

    def test_digest_mismatch_is_invalid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = write_checkpoint(root)
            payload = bytearray((root / record["capture"]["raw_file"]).read_bytes())
            payload[0] ^= 0xFF
            (root / record["capture"]["raw_file"]).write_bytes(bytes(payload))
            result = diag.assess_checkpoint(record, base=root)
            self.assertEqual(result["status"], diag.INVALID)
            self.assertTrue(any("digest" in reason for reason in result["reasons"]))

    def test_missing_declaration_is_unverified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = write_checkpoint(root)
            record.pop("capture")
            result = diag.assess_checkpoint(record, base=root)
            self.assertEqual(result["status"], diag.UNVERIFIED)

    def test_explicit_gpu_failure_is_invalid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_checkpoint(root, gpu={"command_status": 5, "completed": False})
            record = diag._read_json(root / "checkpoint-after-draw-1.json")
            result = diag.assess_checkpoint(record, base=root)
            self.assertEqual(result["status"], diag.INVALID)
            self.assertIn("gpu_completed:false", result["reasons"])

    def test_captured_probe_without_artifact_is_invalid(self):
        result = diag.assess_probe({
            "overall": "captured",
            "status": {"draw_reached": True, "draw_encoded": True,
                       "gpu_completed": True, "pixels_captured": True},
            "artifacts": {},
        })
        self.assertEqual(result["status"], diag.INVALID)

    def test_pixels_claim_without_checkpoint_is_invalid(self):
        result = diag.assess_probe({
            "overall": "draw_reached",
            "status": {"draw_reached": True, "draw_encoded": True,
                       "gpu_completed": True, "pixels_captured": True},
            "artifacts": {},
        })
        self.assertEqual(result["status"], diag.INVALID)
        self.assertTrue(any("pixels" in reason for reason in result["reasons"]))

    def test_presented_probe_without_checkpoint_is_unverified(self):
        result = diag.assess_probe({
            "overall": "presented",
            "status": {"draw_reached": True, "draw_encoded": True,
                       "gpu_completed": True, "pixels_captured": False},
            "artifacts": {"presentation": {"presented": True}},
        })
        self.assertEqual(result["status"], diag.UNVERIFIED)

    def test_replay_success_is_valid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = bytes(48)
            (root / "metal-frame.bgra").write_bytes(payload)
            result = diag.assess_replay({
                "status": "ok", "events": 2, "encoded": 2, "failed": 0,
                "command_status": 4,
                "frame": {"width": 4, "height": 3, "raw_file": "metal-frame.bgra",
                          "raw_bytes": len(payload), "raw_sha256": _sha(payload)},
            }, base=root)
            self.assertEqual(result["status"], diag.VALID)

    def test_frame_observation_requires_hash_and_extent(self):
        result = diag.assess_frame_observation({
            "frame_index": 3, "width": 1728, "height": 1117,
            "pixel_format": "BGRA8", "sha256": "a" * 64, "gpu_completed": True,
        })
        self.assertEqual(result["status"], diag.VALID)
        result = diag.assess_frame_observation({"frame_index": 3, "width": 0})
        self.assertEqual(result["status"], diag.INVALID)

    def test_unknown_validity_name_is_rejected(self):
        with self.assertRaises(ValueError):
            diag.validity("probably-fine")


class HealthGateTests(unittest.TestCase):
    def test_rejected_draw_fails_immediately_and_is_aggregated(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            write_json(run / "draw-refused-edfcd1.json", {
                "schema": "gfx-draw-refused/v1",
                "route": 1, "variant": 1, "caller_rva": "0xedfcd1",
                "reason": "current shader is not linked", "pid": 7,
            })
            write_json(run / "stop.json", {
                "phase": "after_init", "symbol": "_GfxDraw",
                "reason": "unsupported", "rva": "0xedfcd1", "pid": 7,
            })
            summary = diag.scan_run(run)
            self.assertEqual(summary["verdict"], diag.FAILED)
            self.assertEqual(summary["first_failure"]["kind"], "draw_refused")
            self.assertIn("current shader is not linked", summary["first_failure"]["message"])
            refused = [row for row in summary["aggregated"] if row["kind"] == "draw_refused"]
            self.assertEqual(len(refused), 2)  # refusal record + stop record
            gate = diag.health_gate(summary)
            self.assertFalse(gate["allow"])
            self.assertEqual(gate["status"], diag.FAILED)

    def test_thousands_of_validation_messages_collapse_to_one_signature(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            with (run / "game.stderr").open("w", encoding="utf-8") as target:
                for index in range(5000):
                    target.write(
                        f"Metal Validation Error: buffer(0x{index:08x}) bind failed "
                        f"at index {index} \"pipeline-{index}\"\n"
                    )
            summary = diag.scan_run(run)
            self.assertEqual(summary["verdict"], diag.FAILED)
            validation = [row for row in summary["aggregated"] if row["kind"] == "validation"]
            self.assertEqual(len(validation), 1)
            self.assertEqual(validation[0]["count"], 5000)
            self.assertEqual(summary["total_diagnostics"], 5000)

    def test_command_completion_is_not_pixel_correctness(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            write_json(run / "metadata.json", {"status": "deadline"})
            summary = diag.scan_run(run)
            gate = diag.health_gate(summary, result_requires_pixels=True)
            self.assertFalse(gate["allow"])
            self.assertEqual(gate["status"], diag.UNVERIFIED)
            self.assertIn("not pixel correctness", gate["reason"])

    def test_shader_cache_failure_is_instrumentation_not_renderer(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            write_json(run / "stop.json", {
                "phase": "after_init", "symbol": "_GfxInit",
                "reason": "shader-cache", "pid": 7,
            })
            summary = diag.scan_run(run)
            self.assertEqual(summary["verdict"], diag.DEGRADED)
            self.assertIsNone(summary["first_failure"])
            self.assertTrue(summary["instrumentation_failures"])
            gate = diag.health_gate(summary)
            self.assertFalse(gate["allow"])
            self.assertEqual(gate["status"], diag.INVALID)

    def test_resolve_failures_are_execution_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            (run / "resolve-trace.jsonl").write_text(
                '{"seq":1,"draw_seq":737,"status":"scaled_failed"}\n'
                '{"seq":2,"draw_seq":738,"status":"command_failed"}\n'
                '{"seq":3,"draw_seq":739,"status":"scaled_ok"}\n',
                encoding="utf-8",
            )
            summary = diag.scan_run(run)
            self.assertEqual(summary["verdict"], diag.FAILED)
            self.assertEqual(summary["first_failure"]["kind"], "resolve")
            self.assertEqual(summary["total_diagnostics"], 2)

    def test_pipeline_error_is_structured(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            write_json(run / "generic-pipeline-error.json", {
                "error": "fragment function not found", "color_format": 80,
            })
            summary = diag.scan_run(run)
            self.assertEqual(summary["first_failure"]["kind"], "shader_pipeline")

    def test_healthy_run_stays_unknown_without_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            write_json(run / "draw-ok-edfcd1.json", {"caller_rva": "0xedfcd1"})
            summary = diag.scan_run(run)
            self.assertEqual(summary["verdict"], diag.UNKNOWN)
            self.assertTrue(diag.health_gate(summary)["allow"])
            self.assertFalse(diag.health_gate(summary, result_requires_pixels=True)["allow"])

    def test_missing_run_directory_is_instrumentation_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            summary = diag.scan_run(Path(temporary) / "absent")
            self.assertEqual(summary["verdict"], diag.DEGRADED)
            self.assertTrue(summary["instrumentation_failures"])

    def test_normalize_message_collapses_addresses(self):
        self.assertEqual(
            diag.normalize_message("buffer(0x1234) failed at 17 for \"x\""),
            diag.normalize_message("buffer(0xdead) failed at 99 for \"y\""),
        )

    def test_lockstep_timeout_is_an_instrumentation_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            (run / "lockstep-timeout.jsonl").write_text(
                '{"schema":"gfx-lockstep-timeout/v1","backend":"opengl",'
                '"frame_index":12,"pid":7}\n', encoding="utf-8")
            summary = diag.scan_run(run)
            self.assertEqual(summary["verdict"], diag.DEGRADED)
            self.assertTrue(summary["instrumentation_failures"])
            self.assertFalse(diag.health_gate(summary)["allow"])

    def test_scan_real_spike_rejection_if_present(self):
        # The feasibility spike produced this exact artifact; when it is present
        # locally the scanner must classify it without any special-casing.
        run = ROOT / ".local" / "infra" / "spike" / "run-5" / "candidate"
        if not (run / "draw-refused-edfcd1.json").is_file():
            self.skipTest("local spike artifact is absent")
        summary = diag.scan_run(run)
        self.assertEqual(summary["verdict"], diag.FAILED)
        self.assertEqual(summary["first_failure"]["kind"], "draw_refused")
        self.assertIn("shader", summary["first_failure"]["message"])


if __name__ == "__main__":
    unittest.main()
