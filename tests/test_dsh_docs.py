"""The DSH documentation must keep up with the DSH code.

Twice a documentation edit silently did nothing (a `str.replace` that matched
nothing) while the implementation moved on, and the README claimed `DSH_HOME`
could be overridden in the environment when the scripts deliberately ignore it.
These checks make that drift fail a test instead of a reader.
"""

import unittest
from pathlib import Path

from automation import dsh_bridge

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "dsh-native-goal.md"
README = REPO / "dsh" / "README.md"


class DocumentationCoverageTests(unittest.TestCase):
    def test_every_bridge_operation_is_documented(self) -> None:
        text = DOCS.read_text(encoding="utf-8")
        missing = [name for name in sorted(dsh_bridge.OPERATIONS)
                   if f"`{name}`" not in text]
        self.assertEqual(missing, [], f"undocumented operations: {missing}")

    def test_every_operator_script_is_documented(self) -> None:
        text = README.read_text(encoding="utf-8")
        missing = [path.name for path in sorted((REPO / "dsh" / "scripts").glob("*.sh"))
                   if path.name not in text]
        self.assertEqual(missing, [], f"undocumented scripts: {missing}")

    def test_the_readme_describes_the_real_home_resolution(self) -> None:
        text = README.read_text(encoding="utf-8")
        self.assertIn("EU4_DSH_HOME", text)
        self.assertIn("ignore", text.lower())
        # The scripts set DSH_HOME themselves; claiming an env override would be
        # wrong, and it is the mistake this test exists to prevent.
        self.assertNotIn("`DSH_HOME` defaults to", text)

    def test_the_documented_exit_codes_match_the_bridge(self) -> None:
        # Prose wraps; compare on collapsed whitespace so a line break cannot
        # make the assertion brittle.
        text = " ".join(DOCS.read_text(encoding="utf-8").split())
        self.assertIn(f"`{dsh_bridge.EXIT_USAGE}` is a usage error", text)
        self.assertIn(f"`{dsh_bridge.EXIT_INTEGRITY}` is a refusal", text)


if __name__ == "__main__":
    unittest.main()
