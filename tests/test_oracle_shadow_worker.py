"""F1 implementation tests for the cases the acceptance suite does not drive.

The independent acceptance file is intentionally untouched. These tests cover
the work-order refusal, isolation, queue and accounting paths with synthetic
transcripts and stand-in harnesses. Nothing here launches EU IV.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from hashlib import sha256
from pathlib import Path
from unittest import mock

from oracle.function import synthetic
from oracle.function.fixture import load_descriptor
from oracle.shadow import run_owned
from oracle.shadow import supervisor

from tests.oracle_shadow_acceptance.contract import (
    assert_accounting_partition,
    pid_process_state,
)


ROOT = Path(__file__).resolve().parents[1]


def _snapshot(name: str = "s03") -> str:
    return synthetic.capture_text(load_descriptor(), name)


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _candidate(path: Path, function_id: str = "gfx:set-constant-buffer") -> dict[str, str]:
    return {
        "function_id": function_id,
        "source_sha256": "0" * 64,
        "executable_sha256": sha256(path.read_bytes()).hexdigest(),
        "harness_path": str(path),
    }


def _without_buffer(text: str) -> str:
    kept: list[str] = []
    removed = 0
    for line in text.splitlines():
        if " name=buffer " in line:
            removed += 1
            continue
        kept.append(line)
    rendered: list[str] = []
    for line in kept:
        if line.startswith("END "):
            fields = dict(part.split("=", 1) for part in line.split()[1:])
            fields["regions"] = str(int(fields["regions"]) - removed)
            line = "END " + " ".join(f"{key}={fields[key]}" for key in fields)
        rendered.append(line)
    return "\n".join(rendered) + "\n"


class ShadowWorkerTests(unittest.TestCase):
    def _assert_accounted(self, result: dict, submitted: int) -> None:
        assert_accounting_partition(result, submitted)

    def test_malformed_snapshot_is_refused_without_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            marker = work / "ran"
            harness = _script(work / "harness.py", f"open({str(marker)!r}, 'w').write('ran\\n')\n")
            result = run_owned(
                ["this is not a capture\n"],
                _candidate(harness),
                work,
                deadline_seconds=2,
                output_limit_bytes=4096,
                queue_limit=4,
            )
            self._assert_accounted(result, 1)
            self.assertEqual(result["outcomes"], ["refused"])
            self.assertEqual(result["comparisons"], ["none"])
            self.assertFalse(marker.exists())
            self.assertTrue(any("reference:" in item for item in result["failures"][0]))

    def test_missing_descriptor_input_is_an_evidence_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            marker = work / "ran"
            harness = _script(work / "harness.py", f"open({str(marker)!r}, 'w').write('ran\\n')\n")
            result = run_owned(
                [_without_buffer(_snapshot())],
                _candidate(harness),
                work,
                deadline_seconds=2,
                output_limit_bytes=4096,
                queue_limit=4,
            )
            self._assert_accounted(result, 1)
            self.assertEqual(result["outcomes"], ["refused"])
            self.assertEqual(result["comparisons"], ["none"])
            self.assertFalse(marker.exists())
            self.assertTrue(any("unqualified" in item for item in result["failures"][0]))

    def test_missing_candidate_fields_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            result = run_owned(
                [_snapshot()],
                {"function_id": "gfx:set-constant-buffer"},
                work,
                deadline_seconds=2,
                output_limit_bytes=4096,
                queue_limit=2,
            )
            self._assert_accounted(result, 1)
            self.assertEqual(result["outcomes"], ["refused"])
            self.assertEqual(result["comparisons"], ["none"])
            self.assertIn("missing descriptor input", result["failures"][0][0])

    def test_unsupported_function_does_not_execute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            marker = work / "ran"
            harness = _script(work / "harness.py", f"open({str(marker)!r}, 'w').write('ran\\n')\n")
            result = run_owned(
                [_snapshot()],
                _candidate(harness, function_id="gfx:not-supported"),
                work,
                deadline_seconds=2,
                output_limit_bytes=4096,
                queue_limit=2,
            )
            self._assert_accounted(result, 1)
            self.assertEqual(result["outcomes"], ["refused"])
            self.assertEqual(result["comparisons"], ["none"])
            self.assertFalse(marker.exists())
            self.assertIn("unsupported-function", result["failures"][0][0])

    def test_identity_mismatch_does_not_execute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            marker = work / "ran"
            real = _script(work / "real.py", "print('unused')\n")
            wrong = _script(work / "wrong.py", f"open({str(marker)!r}, 'w').write('ran\\n')\n")
            candidate = _candidate(real)
            candidate["harness_path"] = str(wrong)
            result = run_owned(
                [_snapshot()], candidate, work,
                deadline_seconds=2, output_limit_bytes=4096, queue_limit=2,
            )
            self._assert_accounted(result, 1)
            self.assertEqual(result["outcomes"], ["refused"])
            self.assertNotEqual(result["comparisons"][0], "match")
            self.assertFalse(marker.exists())

    def test_replay_setup_refusal_stays_infrastructure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            harness = _script(
                work / "harness.py",
                "import sys\nsys.stdout.write('error=usage\\n')\nraise SystemExit(2)\n",
            )
            result = run_owned(
                [_snapshot()], _candidate(harness), work,
                deadline_seconds=2, output_limit_bytes=4096, queue_limit=2,
            )
            self._assert_accounted(result, 1)
            self.assertEqual(result["outcomes"], ["refused"])
            self.assertEqual(result["comparisons"], ["infrastructure"])
            self.assertTrue(any(item == "infrastructure:usage" for item in result["failures"][0]))

    def test_relative_work_dir_and_harness_path_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            harness = _script(parent / "harness.py", "print('not-a-report')\n")
            candidate = _candidate(harness)
            candidate["harness_path"] = "harness.py"
            previous = os.getcwd()
            os.chdir(parent)
            try:
                result = run_owned(
                    [_snapshot()], candidate, "shadow",
                    deadline_seconds=2, output_limit_bytes=4096, queue_limit=2,
                )
            finally:
                os.chdir(previous)
            self._assert_accounted(result, 1)
            self.assertEqual(result["outcomes"], ["completed"])
            self.assertEqual(result["comparisons"], ["mismatch"])
            self.assertTrue((parent / "shadow" / "jobs" / "0" / "report.json").is_file())

    def test_candidate_mismatch_is_not_infrastructure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            harness = _script(work / "harness.py", "print('not-a-report')\n")
            result = run_owned(
                [_snapshot()], _candidate(harness), work,
                deadline_seconds=2, output_limit_bytes=4096, queue_limit=2,
            )
            self._assert_accounted(result, 1)
            self.assertEqual(result["outcomes"], ["completed"])
            self.assertEqual(result["comparisons"], ["mismatch"])
            self.assertTrue(any(item.startswith("candidate:") for item in result["failures"][0]))

    def test_timeout_kills_the_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            harness = _script(work / "harness.py", """
import os, pathlib, sys, time
root = pathlib.Path(sys.argv[0]).resolve().parent
pid = os.fork()
if pid == 0:
    (root / "child.pid").write_text(str(os.getpid()) + "\\n")
    time.sleep(30)
    os._exit(0)
while not (root / "child.pid").exists():
    time.sleep(0.01)
time.sleep(30)
""")
            started = time.monotonic()
            result = run_owned(
                [_snapshot()], _candidate(harness), work,
                deadline_seconds=0.4, output_limit_bytes=4096, queue_limit=2,
            )
            elapsed = time.monotonic() - started
            self._assert_accounted(result, 1)
            self.assertEqual(result["outcomes"], ["timeout"])
            self.assertEqual(result["comparisons"], ["infrastructure"])
            self.assertLess(elapsed, 2.4)
            child = int((work / "child.pid").read_text().strip())
            self.assertNotEqual(pid_process_state(child), "running")

    def test_output_cap_is_crash_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            harness = _script(work / "harness.py", """
import os, pathlib, sys, time
root = pathlib.Path(sys.argv[0]).resolve().parent
pid = os.fork()
if pid == 0:
    (root / "child.pid").write_text(str(os.getpid()) + "\\n")
    time.sleep(30)
    os._exit(0)
while not (root / "child.pid").exists():
    time.sleep(0.01)
sys.stdout.write("y" * 200000)
sys.stdout.flush()
time.sleep(30)
""")
            result = run_owned(
                [_snapshot()], _candidate(harness), work,
                deadline_seconds=5, output_limit_bytes=4096, queue_limit=2,
            )
            self._assert_accounted(result, 1)
            self.assertEqual(result["outcomes"], ["crash"])
            self.assertEqual(result["comparisons"], ["infrastructure"])
            self.assertLessEqual(result["output_bytes"], 4096)
            observed = sum(
                path.stat().st_size for path in work.rglob("*")
                if path.is_file() and path.name.startswith(("worker-", "capture-")))
            self.assertLessEqual(observed, 4096)
            self.assertIn("infrastructure:output-cap", result["failures"][0])
            child = int((work / "child.pid").read_text().strip())
            self.assertNotEqual(pid_process_state(child), "running")

    def test_queue_exhaustion_drops_the_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            log = work / "runs.log"
            harness = _script(
                work / "harness.py",
                f"open({str(log)!r}, 'a').write('x\\n')\nprint('nope')\n",
            )
            result = run_owned(
                [_snapshot(), _snapshot(), _snapshot()],
                _candidate(harness),
                work,
                deadline_seconds=2,
                output_limit_bytes=4096,
                queue_limit=1,
            )
            self._assert_accounted(result, 3)
            self.assertEqual(result["accounting"]["accepted"], 1)
            self.assertEqual(result["accounting"]["dropped"], 2)
            self.assertEqual(result["outcomes"][1:], ["dropped", "dropped"])
            self.assertEqual(log.read_text(encoding="utf-8").count("x"), 1)

    def test_duplicates_are_delayed_and_agree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            harness = _script(work / "harness.py", "import time\ntime.sleep(0.15)\nprint('nope')\n")
            text = _snapshot()
            started = time.monotonic()
            result = run_owned(
                [text, text], _candidate(harness), work,
                deadline_seconds=2, output_limit_bytes=4096, queue_limit=4,
            )
            elapsed = time.monotonic() - started
            self._assert_accounted(result, 2)
            self.assertGreaterEqual(elapsed, 0.3)
            self.assertEqual(result["outcomes"], ["completed", "completed"])
            self.assertEqual(result["comparisons"][0], result["comparisons"][1])
            self.assertEqual(result["failures"][0], result["failures"][1])
            self.assertEqual(result["comparisons"][0], "mismatch")

    def test_identical_inputs_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            text = _snapshot()
            results = []
            for name in ("a", "b"):
                work = parent / name
                work.mkdir()
                harness = _script(work / "harness.py", "print('not-a-report')\n")
                results.append(run_owned(
                    [text, text], _candidate(harness), work,
                    deadline_seconds=2, output_limit_bytes=4096, queue_limit=4,
                ))
            for key in ("schema", "outcomes", "comparisons", "failures",
                        "output_bytes", "accounting"):
                self.assertEqual(results[0][key], results[1][key])
            self._assert_accounted(results[0], 2)

    def test_worker_crash_does_not_change_or_block_the_producer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            work = parent / "work"
            work.mkdir()
            outside = parent / "producer.txt"
            text = _snapshot()
            producer = {"verdict": "recorded", "snapshot": text}
            outside.write_text("recorded\n", encoding="utf-8")
            harness = _script(work / "harness.py", "print('not-a-report')\n")
            real = supervisor._worker_argv
            calls = {"n": 0}

            def crashing(job_dir: Path) -> list[str]:
                calls["n"] += 1
                if calls["n"] == 1:
                    return [sys.executable, "-c",
                            "import os, resource\n"
                            "resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
                            "os.abort()\n"]
                return real(job_dir)

            started = time.monotonic()
            with mock.patch.object(supervisor, "_worker_argv", crashing):
                result = run_owned(
                    [text, text], _candidate(harness), work,
                    deadline_seconds=2, output_limit_bytes=4096, queue_limit=4,
                )
            elapsed = time.monotonic() - started
            self._assert_accounted(result, 2)
            self.assertEqual(result["outcomes"][0], "crash")
            self.assertEqual(result["comparisons"][0], "infrastructure")
            self.assertEqual(result["outcomes"][1], "completed")
            self.assertEqual(producer, {"verdict": "recorded", "snapshot": text})
            self.assertEqual(outside.read_text(encoding="utf-8"), "recorded\n")
            self.assertLess(elapsed, 4)
            self.assertEqual(calls["n"], 2)

    def test_no_writes_outside_the_worker_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            work = parent / "work"
            work.mkdir()
            outside = parent / "outside.txt"
            outside.write_text("same\n", encoding="utf-8")
            harness = _script(work / "harness.py", "print('not-a-report')\n")
            before = {path.relative_to(parent).as_posix() for path in parent.rglob("*")}
            run_owned(
                [_snapshot()], _candidate(harness), work,
                deadline_seconds=2, output_limit_bytes=4096, queue_limit=2,
            )
            after = {path.relative_to(parent).as_posix() for path in parent.rglob("*")}
            self.assertEqual(outside.read_text(encoding="utf-8"), "same\n")
            for created in after - before:
                self.assertTrue(created.startswith("work/"), created)

    def test_mixed_accounting_reconciles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            harness = _script(work / "harness.py", "import time\ntime.sleep(30)\n")
            result = run_owned(
                ["not-a-capture\n", _snapshot(), _snapshot()],
                _candidate(harness),
                work,
                deadline_seconds=0.3,
                output_limit_bytes=4096,
                queue_limit=2,
            )
            self._assert_accounted(result, 3)
            self.assertEqual(result["outcomes"], ["refused", "timeout", "dropped"])
            self.assertEqual(result["accounting"]["refused"], 1)
            self.assertEqual(result["accounting"]["timed_out"], 1)
            self.assertEqual(result["accounting"]["dropped"], 1)
            self.assertEqual(
                result["accounting"]["submitted"],
                result["accounting"]["accepted"] + result["accounting"]["dropped"])

    def test_shadow_sources_do_not_capture_output_unbounded(self) -> None:
        for path in (ROOT / "oracle" / "shadow").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("capture_output", text, path.name)

    def test_owned_snapshot_is_a_copy(self) -> None:
        from oracle.shadow.contract import build_snapshot, snapshot_text
        text = _snapshot()
        owned = build_snapshot(text)
        self.assertEqual(snapshot_text(owned), text)
        self.assertIsNot(owned.transcript, text)
        self.assertEqual(len(owned.transcript_sha256), 64)


if __name__ == "__main__":
    unittest.main()
