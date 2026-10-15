import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from reverse_engineering.corpus import analyze_headless, build_analyze_headless_argv

FIXTURE_DIR = Path(__file__).resolve().parent / "re_fixtures" / "callsite_mini"
BINARY = FIXTURE_DIR / "callsite_mini"


def ghidra_available() -> bool:
    root = os.environ.get("GHIDRA_INSTALL")
    if not root:
        return False
    return (Path(root).expanduser() / "support" / "analyzeHeadless").is_file()


def _discover_start_entry_rva(project_dir: Path, project_name: str, callsite_staging: Path) -> str:
    callsite_staging.mkdir(parents=True, exist_ok=True)
    analyze_headless(
        project_dir,
        project_name,
        "-process",
        BINARY.name,
        "-noanalysis",
        "-readOnly",
        "-postScript",
        "ExportCallsites.java",
        str(callsite_staging),
    )
    callsites_path = callsite_staging / "callsites-v1.jsonl"
    by_caller: dict[str, list[dict]] = defaultdict(list)
    for line in callsites_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        caller = row.get("caller_rva")
        if caller:
            by_caller[caller].append(row)
    if not by_caller:
        raise AssertionError("callsite fixture produced no caller_rva rows")
    start_rva = max(by_caller, key=lambda rva: len(by_caller[rva]))
    if len(by_caller[start_rva]) < 3:
        raise AssertionError(f"expected _start with 3 callsites, got {len(by_caller[start_rva])} at {start_rva}")
    return start_rva


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
            if project_dir.exists():
                shutil.rmtree(project_dir)
            project_dir.mkdir(parents=True, exist_ok=True)
            project_name = "callsite_mini"
            analyze_headless(project_dir, project_name, "-import", str(BINARY))
            start_rva = _discover_start_entry_rva(
                project_dir,
                project_name,
                staging / "callsite_staging",
            )
            rva_file = staging / "rvas.txt"
            rva_file.write_text(f"{start_rva}\n")
            static_out = staging / "static.jsonl"
            meta_out = staging / "scan-meta.json"
            cmd = build_analyze_headless_argv(
                project_dir,
                project_name,
                "-process",
                BINARY.name,
                "-noanalysis",
                "-readOnly",
                "-log",
                str(staging / "headless.log"),
                "-scriptlog",
                str(staging / "script.log"),
                "-postScript",
                "ExportRendererStatic.java",
                str(rva_file),
                str(static_out),
                str(meta_out),
            )
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0 or not meta_out.is_file() or not static_out.is_file():
                headless_log = staging / "headless.log"
                log_tail = (
                    headless_log.read_text()[-8000:]
                    if headless_log.is_file()
                    else "missing"
                )
                self.fail(
                    f"Ghidra export failed (exit={result.returncode})\n"
                    f"STDOUT:\n{(result.stdout or '')[-12000:]}\n"
                    f"STDERR:\n{(result.stderr or '')[-4000:]}\n"
                    f"Application log:\n{log_tail}"
                )
            meta = json.loads(meta_out.read_text())
            self.assertIn("program_name", meta)
            self.assertIn("image_base", meta)
            self.assertIn("executable_sha256", meta)
            rows = [json.loads(line) for line in static_out.read_text().splitlines() if line.strip()]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["entry_rva"], start_rva)
            self.assertEqual(row["status"], "success")
            self.assertGreater(row.get("instruction_count", 0), 0)
            ins = row.get("instructions") or []
            self.assertTrue(ins)
            self.assertIn("pcode_kind", ins[0])
            self.assertIn("flow_override", ins[0])
            self.assertIn("body_ranges", row)
            self.assertIn("decoded_instruction_ranges", row)


if __name__ == "__main__":
    unittest.main()
