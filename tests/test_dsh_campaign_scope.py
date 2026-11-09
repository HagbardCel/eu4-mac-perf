"""The DSH sidecar's allowlist must match the campaign checker's own scope.

A sidecar that is narrower than the checker silently forbids work the campaign
asks for; one that is wider permits edits the checker will reject. Both are
drift, so the two declarations are compared directly.
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_checker(campaign: str) -> object:
    path = REPO / "campaigns" / campaign / "accept.py"
    spec = importlib.util.spec_from_file_location(f"dsh_scope_{campaign}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FrozenSidecarScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sidecar = json.loads(
            (REPO / "dsh" / "campaigns" / "frozen-offline.json").read_text(
                encoding="utf-8"
            )
        )

    def test_allowlist_matches_the_checker_implementation_paths(self) -> None:
        checker = load_checker("eu4-vertex-buffer-multiple")
        declared = set(checker.IMPLEMENTATION_PATHS)
        allowed = set(self.sidecar["candidate"]["policy"]["allowed_paths"])
        self.assertEqual(
            allowed,
            declared,
            "the sidecar allowlist drifted from the checker's IMPLEMENTATION_PATHS",
        )

    def test_the_note_path_is_the_checkers_own(self) -> None:
        checker = load_checker("eu4-vertex-buffer-multiple")
        self.assertIn(checker.NOTE_REL, self.sidecar["candidate"]["policy"]["allowed_paths"])

    def test_the_campaign_directory_is_forbidden(self) -> None:
        forbidden = set(self.sidecar["candidate"]["policy"]["forbidden_paths"])
        self.assertIn("campaigns/eu4-vertex-buffer-multiple", forbidden)


if __name__ == "__main__":
    unittest.main()
