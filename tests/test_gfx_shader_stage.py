import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_SOURCE = ROOT / "tests" / "gfx_shader_stage_host.mm"
HOST = ROOT / "benchmark" / ".build" / "gfx_shader_stage_host"
SHADER = ROOT / "benchmark" / "eu4_gfx_shader.mm"
EVENTS = ROOT / "benchmark" / "eu4_metal_events.c"
IDENTITY = "simple-lua-Simple/asset-maincode-v0"


def _record(stage: str, source: str, effect: str = IDENTITY) -> dict:
    return {
        "stage": stage,
        "sha256": hashlib.sha256(source.encode()).hexdigest(),
        "effect": effect,
        "length": len(source),
    }


@unittest.skipUnless(sys.platform == "darwin", "shader admission uses Metal/macOS")
class GfxShaderStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        HOST.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "clang", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
            "-I", str(ROOT / "benchmark"),
            "-framework", "Foundation", "-framework", "Metal", "-framework", "QuartzCore",
            "-o", str(HOST), str(HOST_SOURCE), str(SHADER), str(EVENTS),
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        if built.returncode:
            raise AssertionError(built.stderr.strip() or "shader stage host failed to build")

    def _manifest(self, directory: Path, name: str, sources: list[dict]) -> Path:
        path = directory / name
        path.write_text(json.dumps({"sources": sources}))
        return path

    def test_stage_aware_admission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = self._manifest(root, "valid.json", [
                _record("vertex", "stage-vs"),
                _record("pixel", "stage-ps"),
            ])
            malformed = self._manifest(root, "malformed.json", [
                _record("vertex", "stage-vs"),
                {"stage": "geometry", "sha256": "00" * 32, "effect": IDENTITY},
            ])
            invalid = self._manifest(root, "invalid.json", [
                _record("vertex", "stage-vs", effect="another-effect"),
            ])
            ran = subprocess.run(
                [str(HOST), str(valid), str(malformed), str(invalid)],
                capture_output=True, text=True, check=False,
                env={**os.environ, "MTL_DEBUG_LAYER": "1"},
            )
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertIn("stage_ok", ran.stdout)


if __name__ == "__main__":
    unittest.main()
