#!/usr/bin/env python3
"""Focused adversarial tests for the M4 presentation gates."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "m4_accept", ROOT / "campaigns" / "eu4-m4" / "accept.py"
)
accept = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(accept)

EXE = "a" * 64
LIB = "b" * 64
SESSION = "session-1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PresentationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        self.run = self.workspace / ".local" / "m4" / "experiments" / "present"
        self.run.mkdir(parents=True)
        self.width = 8
        self.height = 8
        self.raw = bytes((0, 0, 255, 255)) * (self.width * self.height)  # red BGRA
        (self.run / "drawable.bgra").write_bytes(self.raw)
        self.window = self.run / "window.png"
        self._write_window((255, 0, 0))
        self.descriptor = {
            "schema": "gfx-checkpoint/v1",
            "checkpoint": "after-present",
            "occurrence": 1,
            "executable_sha256": EXE,
            "library_sha256": LIB,
            "session": SESSION,
            "caller": {"pid": 7},
            "target": {"width": self.width, "height": self.height},
            "capture": {
                "raw_file": "drawable.bgra",
                "raw_bytes": len(self.raw),
                "raw_sha256": hashlib.sha256(self.raw).hexdigest(),
            },
            "gpu": {"completed": True, "command_status": 4},
            "presentation": {"shown": True, "drawable_readback": True},
        }
        self.write_descriptor()
        self.candidate = {
            "provenance": {
                "executable_sha256": EXE,
                "library_sha256": LIB,
                "session": SESSION,
                "pid": 7,
            }
        }
        self.manifest = {
            "executable_sha256": EXE,
            "library_sha256": LIB,
            "presentation": {
                "run": str(self.run.relative_to(self.workspace)),
                "occurrence": 1,
                "window_file": "window.png",
                "window_sha256": _sha(self.window),
                "window_width": self.width,
                "window_height": self.height,
            },
        }
        self.policy = {
            "presentation_similarity_max_mean_error": 2.0,
            "presentation_scene_max_mean_error": 2.0,
            "presentation_scene_max_channel_error": 64,
        }

    def write_descriptor(self) -> None:
        (self.run / "checkpoint-after-present-1.json").write_text(
            json.dumps(self.descriptor)
        )

    def _write_window(self, color: tuple[int, int, int]) -> None:
        Image.new("RGB", (self.width * 2, self.height * 2), color).save(self.window)

    def gates(self):
        return accept.presentation_gates(
            self.workspace, self.manifest, self.policy, self.candidate,
            expected_extent=(self.width, self.height),
        )

    def test_valid_presentation_passes_recomputed_gates(self) -> None:
        gates, hard_fail, missing, _ = self.gates()
        by_name = {gate["name"]: gate for gate in gates}
        self.assertTrue(by_name["presentation:checkpoint"]["ok"])
        self.assertTrue(by_name["presentation:drawable"]["ok"])
        self.assertTrue(by_name["presentation:visible"]["ok"])
        self.assertFalse(hard_fail)
        self.assertTrue(missing)  # no GL reference fixture: scene gate is absent

    def test_missing_presentation_is_inconclusive(self) -> None:
        (self.run / "checkpoint-after-present-1.json").unlink()
        gates, hard_fail, missing, _ = self.gates()
        by_name = {gate["name"]: gate for gate in gates}
        self.assertFalse(by_name["presentation:checkpoint"]["ok"])
        self.assertFalse(by_name["presentation:visible"]["ok"])
        self.assertTrue(missing)
        self.assertFalse(hard_fail)

    def test_mismatched_screenshot_fails(self) -> None:
        self._write_window((0, 255, 0))
        self.manifest["presentation"]["window_sha256"] = _sha(self.window)
        gates, hard_fail, _, _ = self.gates()
        by_name = {gate["name"]: gate for gate in gates}
        self.assertFalse(by_name["presentation:visible"]["ok"])
        self.assertTrue(hard_fail)

    def test_inconsistent_library_identity_fails(self) -> None:
        self.candidate["provenance"]["library_sha256"] = "c" * 64
        gates, hard_fail, _, _ = self.gates()
        by_name = {gate["name"]: gate for gate in gates}
        self.assertFalse(by_name["presentation:checkpoint"]["ok"])
        self.assertTrue(hard_fail)

    def test_payload_digest_mismatch_fails(self) -> None:
        (self.run / "drawable.bgra").write_bytes(bytes(len(self.raw)))
        gates, hard_fail, _, _ = self.gates()
        by_name = {gate["name"]: gate for gate in gates}
        self.assertFalse(by_name["presentation:checkpoint"]["ok"])
        self.assertFalse(by_name["presentation:drawable"]["ok"])
        self.assertTrue(hard_fail)

    def test_similarity_unavailable_is_not_success(self) -> None:
        self.window.unlink()
        gates, _, missing, _ = self.gates()
        by_name = {gate["name"]: gate for gate in gates}
        self.assertFalse(by_name["presentation:visible"]["ok"])
        self.assertTrue(missing)


class OccurrenceTests(unittest.TestCase):
    def test_occurrence_two_is_resolved_from_its_own_descriptor(self) -> None:
        from automation import live_gfx

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            raw = bytes((0, 255, 0, 255)) * 16
            (run / "checkpoint-after-present-2.bgra").write_bytes(raw)
            (run / "checkpoint-after-present-2.json").write_text(json.dumps({
                "schema": "gfx-checkpoint/v1",
                "checkpoint": "after-present",
                "occurrence": 2,
                "target": {"width": 4, "height": 4, "origin": "top_left"},
                "capture": {
                    "raw_file": "checkpoint-after-present-2.bgra",
                    "raw_bytes": len(raw),
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                },
                "gpu": {"completed": True, "command_status": 4},
                "presentation": {"shown": True, "drawable_readback": True},
                "caller": {"pid": 1},
            }))
            (run / "presented.json").write_text(json.dumps({
                "schema": "gfx-presentation/v1",
                "shown": True,
                "occurrence": 2,
                "checkpoint_file": "checkpoint-after-present-2.json",
                "raw_file": "checkpoint-after-present-2.bgra",
            }))
            block = live_gfx._presentation_block(run, 2)
            self.assertTrue(block["presented"], block.get("reason"))
            self.assertEqual(block["occurrence"], 2)
            first = live_gfx._presentation_block(run, 1)
            self.assertFalse(first["presented"])


if __name__ == "__main__":
    unittest.main()
