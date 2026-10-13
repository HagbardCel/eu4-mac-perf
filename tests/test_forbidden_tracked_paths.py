import unittest

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

    def test_blocks_patched_eu4_under_patches(self) -> None:
        self.assertTrue(violations("patches/implementations/v1/eu4"))

    def test_normalize_posix(self) -> None:
        self.assertEqual(normalize("foo/bar"), "foo/bar")


if __name__ == "__main__":
    unittest.main()
