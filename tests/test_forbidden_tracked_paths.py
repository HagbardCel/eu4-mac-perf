import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tooling.common import check_forbidden_tracked_paths
from tooling.common.check_forbidden_tracked_paths import normalize, violations


class ForbiddenTrackedPathsTests(unittest.TestCase):
    def test_allows_control_binaries(self) -> None:
        self.assertEqual(violations("results/20261003T210202Z-intrusive-diagnostic/control.bin"), [])

    def test_allows_benchmark_sources(self) -> None:
        self.assertEqual(violations("benchmark/eu4_frame_model.c"), [])

    def test_blocks_local_tree(self) -> None:
        self.assertTrue(violations(".local/eu4/deadbeef/index.sqlite"))

    def test_blocks_ghidra_gpr(self) -> None:
        self.assertTrue(violations("reverse_engineering/foo.gpr"))

    def test_blocks_decompiled_segment(self) -> None:
        self.assertTrue(violations("scratch/decompiled/foo.c"))

    def test_allows_re_fixtures_decompiled_layout(self) -> None:
        self.assertEqual(
            violations("tests/re_fixtures/export_mini/decompiled/batch-00001.jsonl"),
            [],
        )

    def test_blocks_patched_eu4_under_patches(self) -> None:
        self.assertTrue(violations("patches/implementations/v1/eu4"))

    def test_normalize_posix(self) -> None:
        self.assertEqual(normalize("foo/bar"), "foo/bar")

    def test_root_opencode_guard_reads_the_git_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            skill = ".opencode/skills/opengl-metal-reconstruction/SKILL.md"
            staged = [skill, "examples/opencode.json", "examples/.opencode/tools/oracle.ts"]
            forbidden = ["opencode.json", str(Path(skill).with_name("helper.md"))]
            for name in staged + forbidden:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n")
            subprocess.run(["git", "add", "-f", "--", *staged], cwd=root, check=True)

            command = [sys.executable, check_forbidden_tracked_paths.__file__]
            allowed = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)

            subprocess.run(["git", "add", "-f", "--", *forbidden], cwd=root, check=True)
            rejected = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 1, rejected.stderr)
            self.assertEqual(
                rejected.stderr.splitlines(),
                ["Forbidden tracked paths detected:"]
                + [f"  {name}: tracked root OpenCode activation" for name in sorted(forbidden)],
            )


if __name__ == "__main__":
    unittest.main()
