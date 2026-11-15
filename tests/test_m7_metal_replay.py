"""Reusable Metal replay core: C ABI and standalone CLI equivalence.

Stage A/B coverage for PR 2.  The tests build the actual core and exercise the
canonical M6 fixtures; no EU4 process is launched and no OpenGL context is
created.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import m6_compare  # noqa: E402

CORE = BENCH / "eu4_gfx_frame_replay.mm"
CLI = BENCH / "eu4_gfx_frame_replay_cli.mm"
ABI_HOST = ROOT / "tests" / "gfx_metal_replay_abi_host.mm"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _fixture(name: str) -> tuple[Path, Path] | None:
    candidates = [
        ROOT / ".local" / name,
        ROOT.parent / "eu4-mac-perf" / ".local" / name,
    ]
    for root in os.environ.get("EU4_EVIDENCE_ROOT", "").split(os.pathsep):
        if root:
            candidates.append(Path(root) / ".local" / name)
    for candidate in candidates:
        if name == "m6/frames/menu-occ300":
            frame = candidate / "frame"
            shaders = candidate / "translated"
        else:
            frame = candidate / "capture"
            shaders = candidate / "shaders"
        if (frame / "frame-trace.json").is_file() and (shaders / "shaders.json").is_file():
            return frame, shaders
    return None


@unittest.skipUnless(sys.platform == "darwin", "the Metal replay core is macOS")
class MetalReplayLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = Path(cls.temporary.name)
        cls.cli = base / "gfx_frame_replay"
        cls.abi = base / "gfx_metal_replay_abi"
        common = [
            "clang",
            "-arch",
            "arm64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-fobjc-arc",
            "-lc++",
            "-framework",
            "Foundation",
            "-framework",
            "Metal",
            "-I",
            str(BENCH),
        ]
        for output, sources in (
            (cls.cli, [CORE, CLI]),
            (cls.abi, [CORE, ABI_HOST]),
        ):
            built = subprocess.run(
                [*common, "-o", str(output), *map(str, sources)],
                capture_output=True,
                text=True,
                check=False,
                timeout=240,
            )
            if built.returncode:
                raise unittest.SkipTest(built.stderr.strip() or "core build failed")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _cli(self, frame: Path, shaders: Path, output: Path):
        result = subprocess.run(
            [
                str(self.cli),
                "--frame",
                str(frame),
                "--shaders",
                str(shaders),
                "--out",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        return result

    def _abi(self, frame: Path | None, shaders: Path | None, output: Path,
             loops: int = 1, *extra: str):
        command = [str(self.abi), "--out", str(output), "--loops", str(loops)]
        if frame is not None:
            command += ["--frame", str(frame), "--shaders", str(shaders)]
        command += list(extra)
        return subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=300
        )

    def test_library_and_cli_agree_on_the_canonical_title_frame(self):
        fixture = _fixture("m6/frames/menu-occ300")
        if fixture is None:
            self.skipTest("canonical M6 frame is absent in this worktree")
        frame, shaders = fixture
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            cli_out = base / "cli"
            abi_out = base / "abi"
            cli = self._cli(frame, shaders, cli_out)
            self.assertEqual(cli.returncode, 0, cli.stderr)
            abi = self._abi(frame, shaders, abi_out)
            self.assertEqual(abi.returncode, 0, abi.stderr)
            cli_report = json.loads((cli_out / "replay.json").read_text())
            abi_report = json.loads(abi.stdout.splitlines()[-1])
            self.assertEqual(cli_report["events"], 326)
            self.assertEqual(cli_report["encoded"], 326)
            self.assertEqual(cli_report["failed"], 0)
            self.assertTrue(cli_report["readback"])
            self.assertEqual(abi_report["events"], 326)
            self.assertEqual(abi_report["encoded"], 326)
            self.assertEqual(abi_report["failed"], 0)
            self.assertEqual(abi_report["command_status"], 4)
            self.assertTrue(abi_report["readback_ok"])
            self.assertEqual(
                abi_report["manifest_reference_sha256"],
                cli_report["manifest_reference_sha256"],
            )
            self.assertEqual(
                abi_report["metal_frame_sha256"],
                "ff2f4e2b9d2cfeef8509dc6777213c859a7ef6af7bc6ad37c4ca564ce57deb15",
            )
            self.assertEqual(
                _sha(cli_out / "metal-frame.bgra"),
                abi_report["metal_frame_sha256"],
            )
            self.assertGreater(abi_report["duration_ns"], 0)

    def test_reused_session_does_not_retain_stale_render_targets(self):
        fixture = _fixture("m6/frames/menu-occ300")
        if fixture is None:
            self.skipTest("canonical M6 frame is absent in this worktree")
        frame, shaders = fixture
        with tempfile.TemporaryDirectory() as temporary:
            result = self._abi(
                frame, shaders, Path(temporary) / "out", 2,
                "--reuse-session",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            reports = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([item["iteration"] for item in reports], [0, 1])
            self.assertEqual(
                {item["metal_frame_sha256"] for item in reports},
                {"ff2f4e2b9d2cfeef8509dc6777213c859a7ef6af7bc6ad37c4ca564ce57deb15"},
            )
            self.assertTrue(all(item["ok"] for item in reports))

    def test_invalid_trace_and_invalid_config_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "frame").mkdir()
            (base / "shaders").mkdir()
            cli = self._cli(base / "frame", base / "shaders", base / "out")
            self.assertNotEqual(cli.returncode, 0)
            abi = self._abi(base / "frame", base / "shaders", base / "out2")
            self.assertNotEqual(abi.returncode, 0)
            report = json.loads(abi.stdout.splitlines()[-1])
            self.assertEqual(report["ok"], 0)
            self.assertFalse((base / "out2" / "metal-frame.bgra").exists())
            bad = self._abi(None, None, base / "out3", 1, "--bad-config")
            self.assertEqual(bad.returncode, 0, bad.stderr)
            self.assertTrue(json.loads(bad.stdout)["create_failed"])

    def test_timeout_is_bounded_and_reported(self):
        fixture = _fixture("m6/frames/menu-occ300")
        if fixture is None:
            self.skipTest("canonical M6 frame is absent in this worktree")
        frame, shaders = fixture
        with tempfile.TemporaryDirectory() as temporary:
            result = self._abi(
                frame, shaders, Path(temporary) / "out", 1,
                "--timeout-ms", "1",
            )
            self.assertNotEqual(result.returncode, 0)
            report = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(report["ok"], 0)
            self.assertLess(report["duration_ns"], 10_000_000_000)

    def test_unwritable_output_is_a_failure_not_a_silent_success(self):
        fixture = _fixture("m6/frames/menu-occ300")
        if fixture is None:
            self.skipTest("canonical M6 frame is absent in this worktree")
        frame, shaders = fixture
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            output.mkdir()
            output.chmod(0o555)
            try:
                result = self._abi(frame, shaders, output)
            finally:
                output.chmod(0o755)
            self.assertNotEqual(result.returncode, 0)
            report = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(report["ok"], 0)
            self.assertEqual(report["failed"], 1)

    def test_core_does_not_link_opengl(self):
        linked = subprocess.run(
            ["otool", "-L", str(self.cli)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(linked.returncode, 0, linked.stderr)
        self.assertNotIn("OpenGL", linked.stdout)

    def test_copy_bearing_scene_preserves_known_divergence(self):
        fixture = _fixture("infra/strategy-b/case-copy-442-moved")
        if fixture is None:
            self.skipTest("copy-bearing scene-75 case is absent in this worktree")
        frame, shaders = fixture
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "replay"
            result = self._cli(frame, shaders, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "replay.json").read_text())
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["events"], 283)
            self.assertEqual(report["encoded"], 283)
            self.assertEqual(report["failed"], 0)
            self.assertEqual(len(report["transfers"]), 1)
            reference = base / "reference"
            candidate = base / "candidate"
            reference.mkdir()
            candidate.mkdir()
            shutil.copy(frame / "frame-trace.json", reference)
            shutil.copy(frame / "frame-events.jsonl", reference)
            shutil.copy(frame / "frame-reference.bgra", reference)
            shutil.copy(output / "replay.json", candidate)
            shutil.copy(output / "metal-frame.bgra", candidate)
            policy = json.loads(
                (ROOT / "analysis" / "m6" / "comparison-policy.json").read_text()
            )
            compared = m6_compare.compare(reference, candidate, policy)
            self.assertEqual(compared["status"], "fail")
            self.assertEqual(compared["statistics"]["pixels"], 1930176)
            self.assertEqual(compared["statistics"]["mismatched_pixels"], 1792005)
            self.assertEqual(compared["statistics"]["max_channel_error"], 23)
