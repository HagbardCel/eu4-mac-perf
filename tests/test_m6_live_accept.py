import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover - the portable CI runner has no numpy
    np = None

try:
    from automation import m6_live_accept
except ImportError:  # numpy is imported by the live checker
    m6_live_accept = None

_HAS_DEPS = np is not None and m6_live_accept is not None


def _run(ok=True, canonical="/tmp/a", identity="one"):
    return {"ok": ok, "status": {}, "canonical": canonical, "identity": identity}


@unittest.skipUnless(_HAS_DEPS, "the live acceptance checker needs numpy")
class SummarizeRunsTests(unittest.TestCase):
    def test_three_distinct_runs_and_identities_qualify(self) -> None:
        runs = [_run(canonical=f"/tmp/{i}", identity=f"session-{i}") for i in range(3)]
        summary = m6_live_accept.summarize_runs(runs)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["clean_starts"], 3)
        self.assertTrue(summary["distinct_runs"])
        self.assertTrue(summary["distinct_identities"])

    def test_same_run_passed_three_times_does_not_qualify(self) -> None:
        runs = [_run() for _ in range(3)]
        summary = m6_live_accept.summarize_runs(runs)
        self.assertFalse(summary["ok"])
        self.assertFalse(summary["distinct_runs"])
        self.assertFalse(summary["distinct_identities"])

    def test_copied_directories_with_one_launch_do_not_qualify(self) -> None:
        runs = [
            _run(canonical=f"/tmp/copy-{i}", identity="same-launch")
            for i in range(3)
        ]
        summary = m6_live_accept.summarize_runs(runs)
        self.assertFalse(summary["ok"])
        self.assertTrue(summary["distinct_runs"])
        self.assertFalse(summary["distinct_identities"])

    def test_missing_identity_does_not_qualify(self) -> None:
        runs = [_run(canonical=f"/tmp/{i}", identity="") for i in range(3)]
        summary = m6_live_accept.summarize_runs(runs)
        self.assertFalse(summary["ok"])
        self.assertFalse(summary["distinct_identities"])

    def test_failed_run_does_not_qualify(self) -> None:
        runs = [_run(canonical=f"/tmp/{i}", identity=f"s{i}") for i in range(2)]
        runs.append(_run(ok=False, canonical="/tmp/2", identity="s2"))
        summary = m6_live_accept.summarize_runs(runs)
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["clean_starts"], 2)


@unittest.skipUnless(_HAS_DEPS, "the live acceptance checker needs numpy")
class ReferenceTests(unittest.TestCase):
    def test_missing_reference_is_a_hard_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reference, error = m6_live_accept.load_reference(Path(tmp), (4, 4))
        self.assertIsNone(reference)
        self.assertIn("missing", error)

    def test_reference_size_must_match_the_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "frame-reference.bgra").write_bytes(b"\0" * 16)
            reference, error = m6_live_accept.load_reference(Path(tmp), (4, 4))
        self.assertIsNone(reference)
        self.assertIn("expected", error)


@unittest.skipUnless(_HAS_DEPS, "the live acceptance checker needs numpy")
class CandidateTests(unittest.TestCase):
    def test_dump_dimensions_are_derived_from_its_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "swap-frame.bgra"
            path.write_bytes(b"\x10" * (4 * 4 * 4))
            candidate, error, details = m6_live_accept.load_candidate(path, (4, 4))
            self.assertEqual(error, "")
            self.assertEqual(details["physical"], [4, 4])
            path.write_bytes(b"\x10" * (8 * 8 * 4))
            candidate, error, details = m6_live_accept.load_candidate(path, (4, 4))
            self.assertEqual(error, "")
            self.assertEqual(details["physical"], [8, 8])
            self.assertEqual(details["scale"], 2)

    def test_non_integer_scale_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "swap-frame.bgra"
            path.write_bytes(b"\x10" * (6 * 6 * 4))
            candidate, error, details = m6_live_accept.load_candidate(path, (4, 4))
        self.assertIsNone(candidate)
        self.assertIn("integer scale", error)


@unittest.skipUnless(_HAS_DEPS, "the live acceptance checker needs numpy")
class MapStructureTests(unittest.TestCase):
    POLICY = {"map_min_std": 20.0, "map_min_coherence": 0.5}

    def test_noise_magenta_and_blank_maps_are_rejected(self) -> None:
        rng = np.random.default_rng(7)
        noise = rng.integers(0, 255, (80, 120, 3)).astype(np.uint8)
        self.assertFalse(m6_live_accept.map_structure_ok(noise, self.POLICY))
        magenta = np.zeros((80, 120, 3), dtype=np.uint8)
        magenta[:, :, 0] = 255
        magenta[:, :, 2] = 255
        self.assertFalse(m6_live_accept.map_structure_ok(magenta, self.POLICY))
        self.assertFalse(
            m6_live_accept.map_structure_ok(
                np.zeros((80, 120, 3), dtype=np.uint8), self.POLICY
            )
        )

    def test_palette_affinity_rejects_a_colorful_but_wrong_map(self) -> None:
        y, x = np.mgrid[0:80, 0:120]
        terrain = (np.sin(x / 9.0) * 40 + np.cos(y / 7.0) * 30 + 150).astype(np.uint8)
        reference = np.stack([terrain, terrain, terrain - 20], axis=2)
        self.assertGreater(
            m6_live_accept.map_color_similarity(reference, reference), 0.99
        )
        pink = np.stack(
            [
                np.clip(terrain.astype(np.int16) + 30, 0, 255).astype(np.uint8),
                np.clip(terrain.astype(np.int16) - 40, 0, 255).astype(np.uint8),
                np.clip(terrain.astype(np.int16) + 20, 0, 255).astype(np.uint8),
            ],
            axis=2,
        )
        self.assertLess(m6_live_accept.map_color_similarity(pink, reference), 0.6)

    def test_spatially_coherent_map_passes(self) -> None:
        y, x = np.mgrid[0:80, 0:120]
        base = (np.sin(x / 9.0) * 60 + np.cos(y / 7.0) * 40 + 128).astype(np.uint8)
        window = np.stack([base, base // 2 + 40, base // 3 + 80], axis=2)
        self.assertTrue(m6_live_accept.map_structure_ok(window, self.POLICY))

    def test_incorrect_orientation_lowers_ui_correlation(self) -> None:
        y, x = np.mgrid[0:64, 0:64]
        # Asymmetric structure: a horizontal flip or vertical mirror cannot
        # correlate with it (the UI has the same property).
        image = (x * 2 + y * 3 + 20).astype(np.int16)
        region = (0, 64, 0, 64)
        same = m6_live_accept.region_correlation(image, image, region)
        flipped = m6_live_accept.region_correlation(image, image[::-1, :], region)
        self.assertGreater(same, 0.99)
        self.assertLess(flipped, same)
        self.assertLess(flipped, 0.9)


if __name__ == "__main__":
    unittest.main()
