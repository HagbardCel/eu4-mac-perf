"""Agent-facing experiment surface: authorization, probe refusal, PNG preview."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import live_gfx, launch_ledger

ROOT = Path(__file__).resolve().parents[1]


class ProbeSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.exe = self.root / "eu4"
        self.exe.write_bytes(b"pinned stub")
        self.run_root = self.root / "run"
        self.run_root.mkdir()
        self.output_root = self.run_root / "experiments"
        self.ledger_dir = self.root / "authority"

    def test_probe_refuses_without_operator_authorization(self) -> None:
        output = self.output_root / "one"
        with patch.dict(os.environ, {"EU4_LAUNCH_AUTHORITY_DIR": str(self.ledger_dir)}):
            code = live_gfx.main([
                "probe", "--backend", "metal", "--checkpoint", "after-draw",
                "--output", str(output),
            ])
        self.assertEqual(code, live_gfx.EXIT_INCOMPLETE)
        self.assertFalse(output.exists())

    def test_authorize_pins_the_executable_and_output_root(self) -> None:
        with patch.dict(os.environ, {"EU4_LAUNCH_AUTHORITY_DIR": str(self.ledger_dir)}):
            code = live_gfx.main([
                "authorize",
                "--run-root", str(self.run_root),
                "--output-root", str(self.output_root),
                "--executable", str(self.exe),
                "--limit", "2",
                "--deadline-seconds", "90",
                "--workflow-id", "wf-probe",
            ])
        self.assertEqual(code, live_gfx.EXIT_COMPLETE)
        pointer = launch_ledger.read_pointer(self.run_root)
        self.assertIsNotNone(pointer)
        value = launch_ledger.load_ledger(Path(pointer["ledger"]))
        self.assertEqual(value["limit"], 2)
        self.assertEqual(value["deadline_seconds"], 90)
        self.assertEqual(value["executable"]["path"], str(self.exe.resolve()))
        self.assertEqual(value["output_root"], str(self.output_root.resolve()))

    def test_png_preview_is_a_real_png(self) -> None:
        width = height = 2
        bgra = bytes([0, 0, 255, 255] * width * height)
        path = self.root / "preview.png"
        live_gfx._write_png(path, bgra, width, height)
        payload = path.read_bytes()
        self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR", payload[:32])

    def test_status_levels_are_not_collapsed(self) -> None:
        output = self.root / "experiment"
        output.mkdir()
        raw = output / "checkpoint-after-draw-1.bgra"
        raw.write_bytes(b"\x00" * (4 * 4 * 4))
        checkpoint = {
            "checkpoint": "after-draw",
            "target": {"width": 4, "height": 4},
            "capture": {"raw_file": raw.name},
            "status": {
                "draw_reached": 1,
                "draw_encoded": 1,
                "gpu_completed": 1,
                "pixels_captured": 1,
                "presented": 0,
            },
        }
        (output / "checkpoint-after-draw-1.json").write_text(json.dumps(checkpoint))
        (output / "draw-route.json").write_text(json.dumps({"route": "live"}))
        status = live_gfx._read_status(output)
        self.assertTrue(status["draw_reached"])
        self.assertTrue(status["draw_encoded"])
        self.assertTrue(status["gpu_completed"])
        self.assertTrue(status["pixels_captured"])
        self.assertFalse(status["presented"])
        inspected = live_gfx._inspect(output)
        self.assertEqual(inspected["checkpoints"][0]["checkpoint"], "after-draw")


class PinnedProbeClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.exe = self.root / "eu4"
        self.exe.write_bytes(b"pinned stub")
        self.sha = hashlib.sha256(self.exe.read_bytes()).hexdigest()
        self.run_root = self.root / "run"
        self.run_root.mkdir()
        self.output_root = self.run_root / "experiments"
        self.ledger = self.root / "authority.json"

    def _pinned(self, *, limit: int = 2, deadline: float = 180.0,
                used: int = 0, pins: bool = True) -> None:
        kwargs = {}
        if pins:
            kwargs = {
                "executable": {"path": str(self.exe.resolve()), "sha256": self.sha},
                "output_root": self.output_root,
            }
        launch_ledger.create_ledger(
            self.ledger, workflow_id="wf", limit=limit,
            deadline_seconds=deadline if pins else None, **kwargs,
        )
        for _ in range(used):
            launch_ledger.claim(
                self.ledger, campaign_id="wf", run_root=self.run_root,
                output=self.output_root / "prior", targets=["_GfxDraw"],
                requested_timeout=30.0, executable_sha256=self.sha,
                executable_path=str(self.exe.resolve()), require_pinned=pins,
            )
        launch_ledger.write_pointer(
            self.run_root, workflow_id="wf", ledger_path=self.ledger, limit=limit
        )

    def _claim(self, *, output: Path | None = None, sha: str | None = None,
               path: str | None = None) -> str:
        return live_gfx._claim_launch(
            self.run_root, output or (self.output_root / "one"), ["_GfxDraw"],
            requested_timeout=30.0,
            executable_sha256=self.sha if sha is None else sha,
            executable_path=str(self.exe.resolve()) if path is None else path,
            require_pinned=True,
        )

    def test_missing_pointer_is_refused(self) -> None:
        with self.assertRaises(live_gfx.LaunchLimit) as caught:
            self._claim()
        self.assertEqual(caught.exception.kind, "unauthorized")

    def test_legacy_counter_without_pointer_is_refused(self) -> None:
        (self.run_root / "live-launch.json").write_text(json.dumps({
            "schema": live_gfx.LAUNCH_SCHEMA, "limit": 1, "used": 0,
            "launches": [],
        }))
        with self.assertRaises(live_gfx.LaunchLimit) as caught:
            self._claim()
        self.assertEqual(caught.exception.kind, "unauthorized")
        value = json.loads((self.run_root / "live-launch.json").read_text())
        self.assertEqual(value["used"], 0)

    def test_unpinned_ledger_is_refused(self) -> None:
        self._pinned(pins=False)
        with self.assertRaises(live_gfx.LaunchLimit) as caught:
            self._claim()
        self.assertEqual(caught.exception.kind, "unpinned")

    def test_partial_pins_are_refused(self) -> None:
        cases = [
            ({"executable": {"path": str(self.exe.resolve()), "sha256": self.sha}},
             "executable only"),
            ({"output_root": self.output_root}, "output root only"),
            ({"executable": {"path": str(self.exe.resolve()), "sha256": self.sha},
              "output_root": self.output_root, "deadline_seconds": None},
             "no deadline"),
        ]
        for index, (kwargs, _label) in enumerate(cases):
            ledger = self.root / f"partial-{index}.json"
            launch_ledger.create_ledger(
                ledger, workflow_id="wf", limit=2,
                deadline_seconds=kwargs.pop("deadline_seconds", 180.0), **kwargs,
            )
            launch_ledger.write_pointer(
                self.run_root, workflow_id="wf", ledger_path=ledger, limit=2
            )
            with self.assertRaises(live_gfx.LaunchLimit) as caught:
                live_gfx._claim_launch(
                    self.run_root, self.output_root / "one", ["_GfxDraw"],
                    requested_timeout=30.0, executable_sha256=self.sha,
                    executable_path=str(self.exe.resolve()), require_pinned=True,
                )
            self.assertEqual(caught.exception.kind, "unpinned")

    def test_different_executable_sha_is_refused(self) -> None:
        self._pinned()
        with self.assertRaises(live_gfx.LaunchLimit) as caught:
            self._claim(sha="different")
        self.assertEqual(caught.exception.kind, "executable")

    def test_different_executable_path_is_refused(self) -> None:
        self._pinned()
        with self.assertRaises(live_gfx.LaunchLimit) as caught:
            self._claim(path=str(self.root / "other-eu4"))
        self.assertEqual(caught.exception.kind, "executable_path")

    def test_output_outside_root_is_refused(self) -> None:
        self._pinned()
        with self.assertRaises(live_gfx.LaunchLimit) as caught:
            self._claim(output=self.root / "elsewhere")
        self.assertEqual(caught.exception.kind, "output_root")

    def test_exhausted_authorization_is_refused(self) -> None:
        self._pinned(limit=1, used=1)
        with self.assertRaises(live_gfx.LaunchLimit) as caught:
            self._claim()
        self.assertEqual(caught.exception.kind, "exhausted")

    def test_probe_rejects_invalid_occurrence(self) -> None:
        for value in ("0", "-1", "2147483648"):
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                code = live_gfx.main([
                    "probe", "--backend", "metal", "--checkpoint", "after-draw",
                    "--occurrence", value, "--output", str(self.output_root / "bad"),
                    "--campaign-run-root", str(self.run_root),
                ])
            self.assertEqual(code, live_gfx.EXIT_INCOMPLETE)
            self.assertIn("--occurrence", stderr.getvalue())

    def test_valid_pinned_claim_advances_to_the_mocked_launcher(self) -> None:
        self._pinned(limit=2)
        identity = {"executable": str(self.exe), "executable_sha256": self.sha}
        library = self.root / "libeu4_metal_boot.dylib"
        library.write_bytes(b"library")
        launched = []
        envs = []

        def fake_launch(output, **kwargs):
            launched.append(output)
            envs.append(dict(kwargs.get("env_overrides") or {}))
            output.mkdir(parents=True, exist_ok=True)
            raw = output / "checkpoint-after-draw-1.bgra"
            raw.write_bytes(b"\x00\x00\x00\xff")
            (output / "checkpoint-after-draw-1.json").write_text(json.dumps({
                "schema": "gfx-checkpoint/v1",
                "checkpoint": "after-draw",
                "target": {"width": 1, "height": 1},
                "capture": {"raw_file": raw.name},
                "status": {
                    "draw_reached": 1, "draw_encoded": 1, "gpu_completed": 1,
                    "pixels_captured": 1, "presented": 0,
                },
            }))
            return {"pid": 1234}

        import types
        bench = types.SimpleNamespace(
            gog_identity=lambda: identity, sha256=lambda path: "library-sha"
        )
        import sys
        benchmark = self.root / "benchmark"
        benchmark.mkdir()
        with patch.dict(sys.modules, {
            "eu4_benchmark": bench,
            "eu4_metal_boot": types.SimpleNamespace(
                build_library=lambda arch="x86_64": library,
                launch_session=fake_launch,
            ),
        }):
            code = live_gfx.main([
                "probe", "--backend", "metal", "--checkpoint", "after-draw",
                "--occurrence", "1", "--timeout", "30",
                "--output", str(self.output_root / "probe"),
                "--campaign-run-root", str(self.run_root),
            ])
        self.assertEqual(code, live_gfx.EXIT_COMPLETE)
        self.assertEqual(len(launched), 1)
        self.assertEqual(envs[0]["EU4_GFX_CHECKPOINT_CALLER_RVA"], "0")
        with patch.dict(sys.modules, {
            "eu4_benchmark": bench,
            "eu4_metal_boot": types.SimpleNamespace(
                build_library=lambda arch="x86_64": library,
                launch_session=fake_launch,
            ),
        }):
            second = live_gfx.main([
                "probe", "--backend", "metal", "--checkpoint", "after-draw",
                "--caller-rva", "0x1234", "--occurrence", "1", "--timeout", "30",
                "--output", str(self.output_root / "probe2"),
                "--campaign-run-root", str(self.run_root),
            ])
        self.assertEqual(second, live_gfx.EXIT_COMPLETE)
        self.assertEqual(envs[1]["EU4_GFX_CHECKPOINT_CALLER_RVA"], "0x1234")
        result = json.loads((self.output_root / "probe" / "probe-result.json").read_text())
        self.assertTrue(result["status"]["pixels_captured"])
        self.assertIsNone(result["status"]["reference_matched"])
        ledger = launch_ledger.load_ledger(self.ledger)
        self.assertEqual(ledger["used"], 2)
        self.assertEqual(ledger["launches"][0]["status"], "completed")
        self.assertEqual(ledger["launches"][1]["status"], "completed")


class ReferenceEqualityTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from PIL import Image  # noqa: F401
        except Exception:  # noqa: BLE001
            self.skipTest("PIL is not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.width = 4
        self.height = 4
        self.bgra = bytes([0, 0, 255, 255] * self.width * self.height)
        self.raw = self.root / "checkpoint-after-draw-1.bgra"
        self.raw.write_bytes(self.bgra)
        self.checkpoint = {
            "checkpoint": "after-draw",
            "target": {"width": self.width, "height": self.height},
            "capture": {"raw_file": self.raw.name},
        }

    def test_pixel_equality_is_not_opengl_equivalence(self) -> None:
        reference = self.root / "identical.png"
        live_gfx._write_png(reference, self.bgra, self.width, self.height)
        result = live_gfx._compare_reference(reference, self.checkpoint, self.root)
        self.assertEqual(result["status"], "compared")
        self.assertTrue(result["comparable"])
        self.assertTrue(result["pixels_identical"])
        self.assertFalse(result["authenticated_opengl"])
        self.assertEqual(result["mismatched_pixels"], 0)
        self.assertNotIn("reference_matched", result)

    def test_differing_reference_reports_metrics(self) -> None:
        from PIL import Image
        reference = self.root / "different.png"
        image = Image.frombytes(
            "RGBA", (self.width, self.height),
            live_gfx._bgra_to_rgba(self.bgra),
        )
        pixels = list(image.getdata())
        pixels[0] = (0, 255, 0, 255)
        image.putdata(pixels)
        image.save(reference)
        result = live_gfx._compare_reference(reference, self.checkpoint, self.root)
        self.assertEqual(result["status"], "compared")
        self.assertFalse(result["pixels_identical"])
        self.assertGreater(result["mismatched_pixels"], 0)
        self.assertGreater(result["maximum_channel_error"], 0)
        self.assertTrue(Path(result["difference_image"]).is_file())


if __name__ == "__main__":
    unittest.main()
