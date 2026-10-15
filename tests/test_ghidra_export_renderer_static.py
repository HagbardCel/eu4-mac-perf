import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.corpus import analyze_headless

FIXTURE_DIR = Path(__file__).resolve().parent / "re_fixtures" / "callsite_mini"
BINARY = FIXTURE_DIR / "callsite_mini"


def ghidra_available() -> bool:
    root = os.environ.get("GHIDRA_INSTALL")
    if not root:
        return False
    return (Path(root).expanduser() / "support" / "analyzeHeadless").is_file()


@unittest.skipUnless(ghidra_available(), "GHIDRA_INSTALL not set")
class GhidraExportRendererStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not BINARY.is_file():
            build = FIXTURE_DIR / "build_callsite_mini.sh"
            subprocess.run(["bash", str(build)], check=True, cwd=FIXTURE_DIR)

    def test_static_export_meta_and_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            project_dir = staging / "ghidra-project"
            project_name = "callsite_mini"
            analyze_headless(project_dir, project_name, "-import", str(BINARY))
            rva_file = staging / "rvas.txt"
            rva_file.write_text("0x0000000000001000\n")
            static_out = staging / "static.jsonl"
            meta_out = staging / "scan-meta.json"
            analyze_headless(
                project_dir,
                project_name,
                "-process",
                BINARY.name,
                "-noanalysis",
                "-readOnly",
                "-postScript",
                "ExportRendererStatic.java",
                str(rva_file),
                str(static_out),
                str(meta_out),
            )
            meta = json.loads(meta_out.read_text())
            self.assertIn("program_name", meta)
            self.assertIn("image_base", meta)
            self.assertIn("executable_sha256", meta)
            rows = [json.loads(line) for line in static_out.read_text().splitlines() if line.strip()]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["entry_rva"], "0x0000000000001000")
            self.assertEqual(row["status"], "success")
            self.assertGreater(row.get("instruction_count", 0), 0)
            ins = row.get("instructions") or []
            self.assertTrue(ins)
            self.assertIn("pcode_kind", ins[0])


if __name__ == "__main__":
    unittest.main()
