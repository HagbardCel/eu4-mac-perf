"""Multi-variant statically_determined rule for P5.2a closure."""

from __future__ import annotations

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


class MultiVariantGateTests(unittest.TestCase):
    def test_unique_hash_per_supported_config(self) -> None:
        mod = load_accept()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            v0 = root / "analysis" / "gfx-shader" / "v0"
            v0.mkdir(parents=True)
            domain = {
                "target_environment": {"id": "cfg-a"},
                "supported_configurations": [
                    {
                        "id": "cfg-a",
                        "variant_id": "v1",
                        "predicates": {"api": "gl"},
                        "compiler_input_sha256": "a" * 64,
                    },
                    {
                        "id": "cfg-b",
                        "variant_id": "v2",
                        "predicates": {"api": "gl", "vendor": "x"},
                        "compiler_input_sha256": "b" * 64,
                    },
                ],
            }
            (v0 / "capability-domain.json").write_text(
                json.dumps(domain), encoding="utf-8"
            )
            identity = {
                "variants": [
                    {"id": "v1", "compiler_input_sha256": "a" * 64},
                    {"id": "v2", "compiler_input_sha256": "b" * 64},
                ]
            }
            self.assertIsNone(mod.check_statically_determined_variants(identity, root))

    def test_overlapping_predicates_different_hashes_fail(self) -> None:
        mod = load_accept()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            v0 = root / "analysis" / "gfx-shader" / "v0"
            v0.mkdir(parents=True)
            domain = {
                "target_environment": {"id": "cfg-a"},
                "supported_configurations": [
                    {
                        "id": "cfg-a",
                        "variant_id": "v1",
                        "predicates": {"api": "gl"},
                        "compiler_input_sha256": "a" * 64,
                    },
                    {
                        "id": "cfg-b",
                        "variant_id": "v2",
                        "predicates": {"api": "gl"},
                        "compiler_input_sha256": "b" * 64,
                    },
                ],
            }
            (v0 / "capability-domain.json").write_text(
                json.dumps(domain), encoding="utf-8"
            )
            identity = {
                "variants": [
                    {"id": "v1", "compiler_input_sha256": "a" * 64},
                    {"id": "v2", "compiler_input_sha256": "b" * 64},
                ]
            }
            err = mod.check_statically_determined_variants(identity, root)
            self.assertIsNotNone(err)
            self.assertIn("overlapping", err)


if __name__ == "__main__":
    unittest.main()
