import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from analysis.tools.r0_stack_analyze import analyze_sample_text

ROOT = Path(__file__).resolve().parents[1]
WORKER_SOURCE = ROOT / "tests/sample_spin_worker.c"
EVIDENCE_DIR = ROOT / "results/r0-native-qualification"
SAMPLE_PATH = EVIDENCE_DIR / "authentic-sample.txt"
PROVENANCE_PATH = EVIDENCE_DIR / "provenance.json"
LIVE_DIR = EVIDENCE_DIR / "live"
LIVE_SAMPLE_PATH = LIVE_DIR / "live-sample.txt"
LIVE_PROVENANCE_PATH = LIVE_DIR / "live-provenance.json"


def _load_provenance() -> dict | None:
    if not PROVENANCE_PATH.is_file():
        return None
    return json.loads(PROVENANCE_PATH.read_text())


class R0AuthenticSampleQualification(unittest.TestCase):
    def test_authentic_sample_cached_replay(self) -> None:
        provenance = _load_provenance()
        if provenance is None or not SAMPLE_PATH.is_file():
            self.skipTest("missing results/r0-native-qualification evidence (run native capture on macOS)")
        main_tid = int(provenance["main_thread_id"])
        text = SAMPLE_PATH.read_text(errors="replace")
        report = analyze_sample_text(text, main_thread_id=main_tid)
        attributed = int(report["attributed_sample_weight"])
        root_inclusive = int(report["root_inclusive"])
        self.assertGreater(attributed, 0)
        self.assertEqual(attributed, root_inclusive)
        self.assertEqual(int(report["weighted_row_count"]), 15)
        self.assertEqual(root_inclusive, 722)

    # EU4_R0_REFRESH_NATIVE_EVIDENCE=1 writes live/live-sample.txt (never overwrites authentic-sample.txt).
    @unittest.skipUnless(sys.platform == "darwin", "native sample capture requires macOS")
    def test_authentic_sample_native_capture(self) -> None:
        worker = ROOT / "benchmark/.build/sample_spin_worker"
        subprocess.run(
            [
                "clang",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-o",
                str(worker),
                str(WORKER_SOURCE),
            ],
            check=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            sample_path = Path(temporary) / "epoch_b.sample.txt"
            process = subprocess.Popen([str(worker)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertIsNotNone(process.stdout)
            line = process.stdout.readline()
            match = re.search(r"MAIN_TID=(\d+)", line)
            self.assertIsNotNone(match)
            main_tid = int(match.group(1))
            sample_cmd = ["sample", str(process.pid), "1", "-file", str(sample_path), "-mayDie"]
            subprocess.run(sample_cmd, check=True)
            process.wait(timeout=15)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
            text = sample_path.read_text(errors="replace")
            report = analyze_sample_text(text, main_thread_id=main_tid)
            attributed = int(report["attributed_sample_weight"])
            root_inclusive = int(report["root_inclusive"])
            self.assertGreater(attributed, 0)
            self.assertEqual(attributed, root_inclusive)
            self.assertGreater(int(report["weighted_row_count"]), 0)

            if os.environ.get("EU4_R0_REFRESH_NATIVE_EVIDENCE") != "1":
                return

            LIVE_DIR.mkdir(parents=True, exist_ok=True)
            LIVE_SAMPLE_PATH.write_text(text)
            commit = (
                subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=False,
                    cwd=str(ROOT),
                ).stdout.strip()
                or None
            )
            provenance = {
                "captured_utc": datetime.now(timezone.utc).isoformat(),
                "main_thread_id": main_tid,
                "sample_command": [
                    "sample",
                    "<pid>",
                    "1",
                    "-file",
                    "<output>",
                    "-mayDie",
                ],
                "host": "native-qualification",
                "platform": platform.platform(),
                "git_commit": commit,
            }
            LIVE_PROVENANCE_PATH.write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    unittest.main()
