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
    @staticmethod
    def _reference(directory: Path, **overrides) -> None:
        import hashlib
        import json

        width = int(overrides.pop("width", 4))
        height = int(overrides.pop("height", 4))
        pixels = bytes([10, 20, 30, 255]) * (width * height)
        (directory / "frame-reference.bgra").write_bytes(pixels)
        manifest = {
            "complete": True,
            "frame": {
                "width": width,
                "height": height,
                "origin": "lower_left",
                "reference": "frame-reference.bgra",
                "reference_sha256": hashlib.sha256(pixels).hexdigest(),
            },
        }
        for key, value in overrides.items():
            if key == "frame":
                manifest["frame"].update(value)
            else:
                manifest[key] = value
        (directory / "frame-trace.json").write_text(json.dumps(manifest))

    def test_valid_reference_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._reference(Path(tmp))
            reference, error = m6_live_accept.load_reference(Path(tmp), (4, 4))
        self.assertIsNotNone(reference)
        self.assertEqual(error, "")

    def test_missing_manifest_is_a_hard_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reference, error = m6_live_accept.load_reference(Path(tmp), (4, 4))
        self.assertIsNone(reference)
        self.assertIn("manifest", error)

    def test_missing_pixels_are_a_hard_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._reference(Path(tmp))
            (Path(tmp) / "frame-reference.bgra").unlink()
            reference, error = m6_live_accept.load_reference(Path(tmp), (4, 4))
        self.assertIsNone(reference)
        self.assertIn("missing", error)

    def test_short_pixels_are_a_hard_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._reference(Path(tmp))
            (Path(tmp) / "frame-reference.bgra").write_bytes(b"\0" * 16)
            reference, error = m6_live_accept.load_reference(Path(tmp), (4, 4))
        self.assertIsNone(reference)
        self.assertTrue(error)

    def test_digest_mismatch_is_a_hard_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._reference(Path(tmp))
            (Path(tmp) / "frame-reference.bgra").write_bytes(b"\0" * 64)
            reference, error = m6_live_accept.load_reference(Path(tmp), (4, 4))
        self.assertIsNone(reference)
        self.assertIn("digest", error)

    def test_manifest_extent_must_match_the_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._reference(Path(tmp), frame={"width": 8, "height": 8})
            reference, error = m6_live_accept.load_reference(Path(tmp), (4, 4))
        self.assertIsNone(reference)
        self.assertIn("extent", error)

    def test_unsupported_origin_is_a_hard_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._reference(Path(tmp), frame={"origin": "top_right"})
            reference, error = m6_live_accept.load_reference(Path(tmp), (4, 4))
        self.assertIsNone(reference)
        self.assertIn("origin", error)

    def test_incomplete_manifest_is_a_hard_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._reference(Path(tmp), complete=False)
            reference, error = m6_live_accept.load_reference(Path(tmp), (4, 4))
        self.assertIsNone(reference)
        self.assertIn("incomplete", error)


@unittest.skipUnless(_HAS_DEPS, "the live acceptance checker needs numpy")
class ResolveTraceRequirementTests(unittest.TestCase):
    def test_missing_or_empty_trace_is_not_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            self.assertFalse(m6_live_accept.resolve_trace_present(run))
            (run / "resolve-trace.jsonl").write_text("\n")
            self.assertFalse(m6_live_accept.resolve_trace_present(run))
            (run / "resolve-trace.jsonl").write_text(
                '{"seq":1,"status":"scaled_ok"}\n'
            )
            self.assertTrue(m6_live_accept.resolve_trace_present(run))


@unittest.skipUnless(_HAS_DEPS, "the live acceptance checker needs numpy")
class ResolveTraceTests(unittest.TestCase):
    def test_failed_resolve_is_reported_even_without_refused_draws(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            records = [
                {"seq": 1, "op": "_GfxResolve", "status": "scaled_ok"},
                {"seq": 2, "op": "_GfxResolve", "status": "scaled_failed"},
            ]
            (run / "resolve-trace.jsonl").write_text(
                "\n".join(json.dumps(record) for record in records) + "\n"
            )
            failures = m6_live_accept.resolve_failures(run)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["status"], "scaled_failed")

    def test_malformed_resolve_trace_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "resolve-trace.jsonl").write_text("{not json}\n")
            failures = m6_live_accept.resolve_failures(run)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["status"], "malformed_trace")

    def test_absent_trace_is_not_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(m6_live_accept.resolve_failures(Path(tmp)), [])


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
