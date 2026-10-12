import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from analysis.tools.r0_stack_analyze import analyze_sample_text

ROOT = Path(__file__).resolve().parents[1]
WORKER_SOURCE = ROOT / "tests/sample_spin_worker.c"
FIXTURE_PATH = ROOT / "tests/fixtures/r0_authentic_sample.txt"


def _main_thread_id_from_sample(text: str, expected_decimal: int | None = None) -> int:
    if expected_decimal is not None:
        hex_token = f"{expected_decimal:x}".lower()
        for line in text.splitlines():
            lowered = line.lower()
            if "thread 0x" in lowered and hex_token in lowered:
                match = re.search(r"0x([0-9a-f]+)", line, re.IGNORECASE)
                if match:
                    return int(match.group(1), 16)
    for block in text.split("Thread ")[1:]:
        if "sample_spin_worker" not in block:
            continue
        match = re.search(r"0x([0-9a-f]+)", block.splitlines()[0], re.IGNORECASE)
        if match:
            return int(match.group(1), 16)
    raise ValueError("main thread section not found in sample output")


@unittest.skipUnless(sys.platform == "darwin", "sample qualification requires macOS")
class R0AuthenticSampleQualification(unittest.TestCase):
    def test_authentic_sample_parser_conservation(self) -> None:
        if FIXTURE_PATH.is_file():
            raw = FIXTURE_PATH.read_text(errors="replace")
            thread_match = re.search(r"Thread_(\d+)", raw)
            self.assertIsNotNone(thread_match, "fixture missing Thread_<id> header")
            main_tid = int(thread_match.group(1))
            report = analyze_sample_text(raw, main_thread_id=main_tid)
            attributed = int(report["attributed_sample_weight"])
            root_inclusive = int(report["root_inclusive"])
            self.assertGreater(attributed, 0)
            self.assertEqual(attributed, root_inclusive)
            return

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
            expected_tid = int(match.group(1))
            subprocess.run(
                ["sample", str(process.pid), "1", "-file", str(sample_path), "-mayDie"],
                check=True,
            )
            process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
            text = sample_path.read_text(errors="replace")
            main_tid = _main_thread_id_from_sample(text, expected_decimal=expected_tid)
            FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
            FIXTURE_PATH.write_text(text)
            report = analyze_sample_text(text, main_thread_id=main_tid)
            attributed = int(report["attributed_sample_weight"])
            root_inclusive = int(report["root_inclusive"])
            self.assertGreater(attributed, 0)
            self.assertEqual(attributed, root_inclusive)


if __name__ == "__main__":
    unittest.main()
