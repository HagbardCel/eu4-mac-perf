import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.callsite_audit import canonical_jsonl_bytes, validate_census, validate_scan_meta
from reverse_engineering.corpus import analyze_headless

FIXTURE_DIR = Path(__file__).resolve().parent / "re_fixtures" / "callsite_mini"
BINARY = FIXTURE_DIR / "callsite_mini"


def ghidra_available() -> bool:
    root = os.environ.get("GHIDRA_INSTALL")
    if not root:
        return False
    return (Path(root).expanduser() / "support" / "analyzeHeadless").is_file()


@unittest.skipUnless(ghidra_available(), "GHIDRA_INSTALL not set")
class GhidraExportCallsitesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not BINARY.is_file():
            build = FIXTURE_DIR / "build_callsite_mini.sh"
            subprocess.run(["bash", str(build)], check=True, cwd=FIXTURE_DIR)

    def _run_export(self, staging: Path, project_dir: Path, project_name: str) -> bytes:
        if project_dir.exists():
            shutil.rmtree(project_dir)
        project_dir.mkdir(parents=True)
        analyze_headless(
            project_dir,
            project_name,
            "-import",
            str(BINARY),
        )
        analyze_headless(
            project_dir,
            project_name,
            "-process",
            BINARY.name,
            "-noanalysis",
            "-readOnly",
            "-postScript",
            "ExportCallsites.java",
            str(staging),
        )
        scan_meta = json.loads((staging / "scan-meta.json").read_text())
        validate_scan_meta(scan_meta)
        callsites = validate_census(staging / "callsites-v1.jsonl", scan_meta)
        self.assertGreaterEqual(len(callsites), 3)
        classes = {c["instruction_class"] for c in callsites}
        self.assertIn("direct", classes)
        self.assertIn("indirect", classes)
        for key in (
            "pcode_is_call",
            "flow_instruction_class",
            "classification_conflict",
        ):
            self.assertIn(key, callsites[0])
        return canonical_jsonl_bytes(callsites)

    def test_direct_and_indirect_callsites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "ghidra-project"
            staging = Path(tmp) / "staging"
            staging.mkdir()
            self._run_export(staging, project_dir, "CallsiteMini")

    def test_reproducible_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "ghidra-project"
            staging1 = Path(tmp) / "staging1"
            staging2 = Path(tmp) / "staging2"
            staging1.mkdir()
            staging2.mkdir()
            b1 = self._run_export(staging1, project_dir, "CallsiteMini")
            b2 = self._run_export(staging2, project_dir, "CallsiteMiniRepro")
            self.assertEqual(b1, b2)


if __name__ == "__main__":
    unittest.main()
