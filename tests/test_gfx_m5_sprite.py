"""M5 sprite operation: exact seeded replay, bounds and refusal tests.

The authentic engine now reaches and GPU-completes the sprite draw. These tests
use the deterministic offline replay as the exact isolated pixel qualification
with the local authentic original-GL capture. Full live-scene framebuffer
equivalence is not yet established because the preceding loading-screen
background producer is not reconstructed. They skip when that operator-local
evidence is absent, like the other live graphics tests in this repository.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_compare, m5_replay  # noqa: E402

REFERENCE = ROOT / ".local" / "m5" / "experiments" / "sprite-gl-ref-09"
POLICY = ROOT / "analysis" / "m5" / "comparison-policy.json"


@unittest.skipUnless(sys.platform == "darwin", "M5 replay needs Metal/macOS")
@unittest.skipUnless(REFERENCE.is_dir(), "local authentic sprite reference is absent")
class M5SpriteReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.host = m5_replay.build_host(force=True)
        cls.policy = json.loads(POLICY.read_text())

    def _replay(self, reference: Path, output: Path) -> dict:
        return m5_replay.run_replay(reference, output, force_build=False)

    def test_replay_matches_authentic_reference_exactly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m5-exact-") as tmp:
            output = Path(tmp) / "candidate"
            replay = self._replay(REFERENCE, output)
            descriptor = replay["candidate"]
            self.assertTrue(descriptor["gpu"]["completed"])
            self.assertEqual(descriptor["draw"]["shader_identity"], "sprite-map/asset-maincode-v0")
            self.assertEqual(descriptor["draw"]["caller_rva"], "0x14f6c1b")
            report = gfx_compare.compare(
                gfx_compare.load_capture(REFERENCE, "reference"),
                gfx_compare.load_capture(output, "candidate"),
                self.policy,
            )
            self.assertEqual(report["status"], "pass", report.get("reasons"))
            samples = report["samples"]
            self.assertEqual(samples["max_channel_error"], 0)
            self.assertEqual(samples["mismatched_pixels"], 0)
            self.assertEqual(samples["pixels"], 1728 * 1117)

    def test_replay_records_captured_sampler(self) -> None:
        reference = json.loads((REFERENCE / "gl-reference.json").read_text())
        sampler = reference.get("sampler") or {}
        self.assertEqual(sampler.get("min_filter"), 9729)
        self.assertEqual(sampler.get("mag_filter"), 9729)
        self.assertEqual(sampler.get("wrap_s"), 33071)
        self.assertEqual(sampler.get("wrap_t"), 33071)
        with tempfile.TemporaryDirectory(prefix="m5-sampler-") as tmp:
            output = Path(tmp) / "candidate"
            replay = self._replay(REFERENCE, output)
            candidate = replay["candidate"]["state"]["sampler"]
            self.assertEqual(candidate["min_filter"], sampler["min_filter"])
            self.assertEqual(candidate["mag_filter"], sampler["mag_filter"])
            self.assertEqual(candidate["wrap_s"], sampler["wrap_s"])
            self.assertEqual(candidate["wrap_t"], sampler["wrap_t"])

    def test_constant_buffer_word_count_is_dwords(self) -> None:
        reference = json.loads((REFERENCE / "gl-reference.json").read_text())
        env = os.environ.copy()
        env["EU4_GFX_EXPECTED_EXE"] = reference["provenance"]["executable"]
        env["EU4_GFX_EXECUTABLE_SHA256"] = reference["provenance"]["executable_sha256"]
        with tempfile.TemporaryDirectory(prefix="m5-words-") as tmp:
            output = Path(tmp) / "candidate"
            output.mkdir()
            ran = subprocess.run(
                [str(self.host), "--reference", str(REFERENCE), "--out", str(output),
                 "--constant-words", "23"],
                capture_output=True, text=True, check=False, env=env,
            )
            self.assertNotEqual(ran.returncode, 0)
            self.assertIn("constant", ran.stderr.lower())
            self.assertIn("24 dwords", ran.stderr.lower())
        with tempfile.TemporaryDirectory(prefix="m5-words-ok-") as tmp:
            output = Path(tmp) / "candidate"
            output.mkdir()
            ran = subprocess.run(
                [str(self.host), "--reference", str(REFERENCE), "--out", str(output),
                 "--constant-words", "28"],
                capture_output=True, text=True, check=False, env=env,
            )
            self.assertEqual(ran.returncode, 0, ran.stderr)
            descriptor = json.loads((output / "metal-logical.json").read_text())
            expected_hex = reference["constants"]["hex"][: 96 * 2]
            self.assertEqual(descriptor["state"]["constant_hex"], expected_hex)

    def test_replay_is_deterministic(self) -> None:
        hashes = []
        with tempfile.TemporaryDirectory(prefix="m5-det-") as tmp:
            for index in range(2):
                output = Path(tmp) / f"candidate-{index}"
                replay = self._replay(REFERENCE, output)
                hashes.append(replay["candidate"]["capture"]["raw_sha256"])
        self.assertEqual(hashes[0], hashes[1])

    def test_corrupted_texture_fails_comparison(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m5-corrupt-") as tmp:
            root = Path(tmp)
            reference = root / "reference"
            shutil.copytree(REFERENCE, reference)
            texture = reference / "gl-reference-texture.bgra"
            data = bytearray(texture.read_bytes())
            row = 1235 * 4
            for y in range(70, 105):
                for i in range(y * row, (y + 1) * row, 4):
                    data[i] ^= 0xFF
                    data[i + 1] ^= 0xFF
                    data[i + 2] ^= 0xFF
            texture.write_bytes(bytes(data))
            output = root / "candidate"
            self._replay(reference, output)
            report = gfx_compare.compare(
                gfx_compare.load_capture(REFERENCE, "reference"),
                gfx_compare.load_capture(output, "candidate"),
                self.policy,
            )
            self.assertEqual(report["status"], "fail")
            self.assertGreater(report["samples"]["max_channel_error"], 0)
            self.assertGreater(report["samples"]["mismatched_pixels"], 0)

    def test_unsupported_state_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m5-bad-") as tmp:
            env = os.environ.copy()
            env["EU4_M5_REPLAY_BAD_STATE"] = "1"
            env["EU4_GFX_EXPECTED_EXE"] = "/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4"
            env["EU4_GFX_EXECUTABLE_SHA256"] = (
                "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
            )
            ran = subprocess.run(
                [str(self.host), "--reference", str(REFERENCE), "--out", tmp],
                capture_output=True, text=True, check=False, env=env,
            )
            self.assertNotEqual(ran.returncode, 0)
            self.assertIn("blend state", ran.stderr)


if __name__ == "__main__":
    unittest.main()
