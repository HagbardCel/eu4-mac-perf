"""Multi-variant statically_determined rule for P5.2a closure."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ACCEPT = REPO / "campaigns" / "eu4-p52a-closure" / "accept.py"


def load_accept():
    spec = importlib.util.spec_from_file_location("p52a_closure_accept", ACCEPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _write_fixture(root: Path, digest: str, stage: str, body: bytes) -> None:
    assert hashlib.sha256(body).hexdigest() == digest
    assets = root / "analysis" / "gfx-shader" / "v0" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    suffix = "vert.glsl" if stage == "vs" else "frag.glsl"
    (assets / f"{digest}.{suffix}").write_bytes(body)


class MultiVariantGateTests(unittest.TestCase):
    def test_unique_pair_per_supported_config(self) -> None:
        mod = load_accept()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vs_a = hashlib.sha256(b"vs-a").hexdigest()
            ps_a = hashlib.sha256(b"ps-a").hexdigest()
            vs_b = hashlib.sha256(b"vs-b").hexdigest()
            ps_b = hashlib.sha256(b"ps-b").hexdigest()
            _write_fixture(root, vs_a, "vs", b"vs-a")
            _write_fixture(root, ps_a, "ps", b"ps-a")
            _write_fixture(root, vs_b, "vs", b"vs-b")
            _write_fixture(root, ps_b, "ps", b"ps-b")
            domain = {
                "target_environment": {"id": "cfg-a"},
                "supported_configurations": [
                    {
                        "id": "cfg-a",
                        "variant_id": "v1",
                        "predicates": {"api": "gl"},
                        "sha256_vs": vs_a,
                        "sha256_ps": ps_a,
                    },
                    {
                        "id": "cfg-b",
                        "variant_id": "v2",
                        # Disjoint predicates (neither subset nor equal)
                        "predicates": {"api": "metal"},
                        "sha256_vs": vs_b,
                        "sha256_ps": ps_b,
                    },
                ],
            }
            v0 = root / "analysis" / "gfx-shader" / "v0"
            (v0 / "capability-domain.json").write_text(
                json.dumps(domain), encoding="utf-8"
            )
            identity = {
                "variants": [
                    {"id": "v1", "sha256_vs": vs_a, "sha256_ps": ps_a},
                    {"id": "v2", "sha256_vs": vs_b, "sha256_ps": ps_b},
                ]
            }
            self.assertIsNone(mod.check_statically_determined_variants(identity, root))

    def test_overlapping_predicates_different_pairs_fail(self) -> None:
        mod = load_accept()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vs_a = hashlib.sha256(b"vs-a").hexdigest()
            ps_a = hashlib.sha256(b"ps-a").hexdigest()
            vs_b = hashlib.sha256(b"vs-b").hexdigest()
            ps_b = hashlib.sha256(b"ps-b").hexdigest()
            _write_fixture(root, vs_a, "vs", b"vs-a")
            _write_fixture(root, ps_a, "ps", b"ps-a")
            _write_fixture(root, vs_b, "vs", b"vs-b")
            _write_fixture(root, ps_b, "ps", b"ps-b")
            domain = {
                "target_environment": {"id": "cfg-a"},
                "supported_configurations": [
                    {
                        "id": "cfg-a",
                        "variant_id": "v1",
                        "predicates": {"api": "gl"},
                        "sha256_vs": vs_a,
                        "sha256_ps": ps_a,
                    },
                    {
                        "id": "cfg-b",
                        "variant_id": "v2",
                        "predicates": {"api": "gl"},
                        "sha256_vs": vs_b,
                        "sha256_ps": ps_b,
                    },
                ],
            }
            v0 = root / "analysis" / "gfx-shader" / "v0"
            v0.mkdir(parents=True, exist_ok=True)
            (v0 / "capability-domain.json").write_text(
                json.dumps(domain), encoding="utf-8"
            )
            identity = {
                "variants": [
                    {"id": "v1", "sha256_vs": vs_a, "sha256_ps": ps_a},
                    {"id": "v2", "sha256_vs": vs_b, "sha256_ps": ps_b},
                ]
            }
            err = mod.check_statically_determined_variants(identity, root)
            self.assertIsNotNone(err)
            self.assertIn("overlapping", err)

    def test_subset_superset_predicates_different_pairs_fail(self) -> None:
        mod = load_accept()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vs_a = hashlib.sha256(b"vs-a").hexdigest()
            ps_a = hashlib.sha256(b"ps-a").hexdigest()
            vs_b = hashlib.sha256(b"vs-b").hexdigest()
            ps_b = hashlib.sha256(b"ps-b").hexdigest()
            _write_fixture(root, vs_a, "vs", b"vs-a")
            _write_fixture(root, ps_a, "ps", b"ps-a")
            _write_fixture(root, vs_b, "vs", b"vs-b")
            _write_fixture(root, ps_b, "ps", b"ps-b")
            domain = {
                "target_environment": {"id": "cfg-a"},
                "supported_configurations": [
                    {
                        "id": "cfg-a",
                        "variant_id": "v1",
                        "predicates": {"api": "gl"},
                        "sha256_vs": vs_a,
                        "sha256_ps": ps_a,
                    },
                    {
                        "id": "cfg-b",
                        "variant_id": "v2",
                        "predicates": {"api": "gl", "vendor": "x"},
                        "sha256_vs": vs_b,
                        "sha256_ps": ps_b,
                    },
                ],
            }
            # Wait — subset with *different* pairs should fail. Same pairs OK.
            # Reconfigure cfg-b as proper superset with different hashes:
            v0 = root / "analysis" / "gfx-shader" / "v0"
            v0.mkdir(parents=True, exist_ok=True)
            (v0 / "capability-domain.json").write_text(
                json.dumps(domain), encoding="utf-8"
            )
            identity = {
                "variants": [
                    {"id": "v1", "sha256_vs": vs_a, "sha256_ps": ps_a},
                    {"id": "v2", "sha256_vs": vs_b, "sha256_ps": ps_b},
                ]
            }
            err = mod.check_statically_determined_variants(identity, root)
            self.assertIsNotNone(err)
            self.assertIn("overlapping", err)


if __name__ == "__main__":
    unittest.main()
