import datetime as dt
import hashlib
import json
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import autonomous_runner as runner  # noqa: E402
from fixture_manager import FixtureManager  # noqa: E402


class AutonomousRunnerTests(unittest.TestCase):
    def test_continuation_restored_byte_for_byte_and_changed_save_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user = root / "user"
            (user / "save games").mkdir(parents=True)
            original = b'{\n\t"filename": "save games/personal.eu4"\n}\n'
            continuation = user / "continue_game.json"
            continuation.write_bytes(original)
            continuation.chmod(0o600)
            fixture = root / "fixture.eu4"
            fixture.write_bytes(b"known save")
            digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
            run_dir = root / "run"
            run_dir.mkdir()
            with FixtureManager(root / "output", user, fixture, digest) as manager:
                working = manager.install()
                self.assertEqual(json.loads(continuation.read_text())["filename"],
                                 f"save games/{working.name}")
                self.assertTrue(manager.journal.is_file())
                working.write_bytes(b"changed by game")
                self.assertTrue(manager.finish(run_dir))
                self.assertEqual((run_dir / "working_save_changed.eu4").read_bytes(),
                                 b"changed by game")
                self.assertEqual(continuation.read_bytes(), original)
                self.assertFalse(manager.journal.exists())
                self.assertFalse(working.exists())

    def test_interrupted_fixture_recovers_on_next_locked_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user = root / "user"
            (user / "save games").mkdir(parents=True)
            continuation = user / "continue_game.json"
            continuation.write_bytes(b"original\n")
            fixture = root / "fixture.eu4"
            fixture.write_bytes(b"save")
            digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
            with FixtureManager(root / "output", user, fixture, digest) as manager:
                manager.install()
                self.assertNotEqual(continuation.read_bytes(), b"original\n")
            with FixtureManager(root / "output", user, fixture, digest) as manager:
                self.assertTrue(manager.recover())
                self.assertEqual(continuation.read_bytes(), b"original\n")

    def test_probe_alignment_and_clock_jump_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "probe.csv"
            anchor = {"monotonic_ns": 100, "wall_ns": 1000}
            path.write_text("S,10,1010,1000000000,60,60\n"
                            "S,1000000010,1000001010,1000000000,60,0\n")
            rows = runner.probe_rows(path, anchor)
            self.assertEqual(rows[0]["monotonic_ns"], 110)
            self.assertEqual(rows[0]["swaps_s"], 60)
            self.assertEqual(rows[1]["paused_swaps"], 0)
            path.write_text(path.read_text()+"S,2000000010,2200001010,1000000000,60,60\n")
            with self.assertRaisesRegex(runner.base.BenchmarkError, "diverged"):
                runner.probe_rows(path, anchor)

    def test_powermetrics_tail_requires_complete_plists(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "power.pliststream"
            item = {"timestamp": dt.datetime(2026, 9, 28, tzinfo=dt.timezone.utc),
                    "tasks": [{"pid": 42, "cputime_ms_per_s": 1000}]}
            data = plistlib.dumps(item)
            path.write_bytes(data[:20])
            tail = runner.PowerTail(path)
            self.assertEqual(tail.poll(), [])
            with path.open("ab") as output:
                output.write(data[20:]+b"\0")
            self.assertEqual(len(tail.poll()), 1)
            self.assertEqual(len(tail.poll()), 1)

    def test_fresh_game_log_and_readiness_markers(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "game.log"
            old = b"previous game\n"
            path.write_bytes(old)
            self.assertEqual(runner.fresh_game_log(path, old), "")
            markers = ("Human Player set as primary local of Venice\n"
                       "Launching SINGLEPLAYER-game\nStart-date: 1444.11.11\n"
                       "End RestoreDeviceObjects\n")
            path.write_bytes(old+markers.encode())
            self.assertTrue(runner.game_log_ready(runner.fresh_game_log(path, old)))
            path.write_text(markers)
            self.assertTrue(runner.game_log_ready(runner.fresh_game_log(path, old)))
            self.assertFalse(runner.stable([100, 100, 150], 3, .1))

    def test_runner_failure_stops_owned_process_and_restores_continuation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user = root / "user"
            (user / "save games").mkdir(parents=True)
            original = b'{"filename":"save games/personal.eu4"}\n'
            (user / "continue_game.json").write_bytes(original)
            fixture = root / "fixture.eu4"
            fixture.write_bytes(b"fixed save")
            digest = hashlib.sha256(fixture.read_bytes()).hexdigest()

            class FakeProcess:
                def __init__(self, pid):
                    self.pid = pid
                    self.returncode = None

                def poll(self):
                    return self.returncode

                def terminate(self):
                    self.returncode = -15

                def wait(self, timeout=None):
                    return self.returncode

            pm, game = FakeProcess(41), FakeProcess(42)
            evidence = {"fixture": {"save_sha256": digest}, "launcher_args": []}
            with patch.object(runner.base, "USER_DATA", user), \
                 patch.object(runner.base, "running_game_pid", return_value=None), \
                 patch.object(runner, "FIXTURE", fixture), \
                 patch.object(runner.subprocess, "Popen", side_effect=[pm, game]), \
                 patch.object(runner.time, "sleep"), \
                 patch.object(runner, "wait_until_ready",
                              side_effect=runner.base.BenchmarkError("not ready")), \
                 patch.object(runner, "focus", return_value=False):
                with self.assertRaisesRegex(runner.base.BenchmarkError, "not ready"):
                    runner.run(root / "output", evidence)
            self.assertEqual((user / "continue_game.json").read_bytes(), original)
            self.assertEqual(pm.returncode, -15)
            self.assertEqual(game.returncode, -15)
            manifest = json.loads(next((root / "output").glob("*-autonomous/manifest.json")).read_text())
            self.assertEqual(manifest["status"], "incomplete")
            self.assertFalse((root / "output/.autonomous-recovery.json").exists())

    def test_scene_guard_tolerates_animation_but_rejects_wrong_map(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            a, b, c = (root / name for name in ("a.png", "b.png", "c.png"))
            Image.new("RGB", (320, 200), (100, 60, 80)).save(a)
            Image.new("RGB", (320, 200), (107, 65, 75)).save(b)
            Image.new("RGB", (320, 200), (20, 180, 20)).save(c)
            self.assertLess(runner.scene_difference(a, b)["mean_rgb_delta"], 15)
            self.assertGreater(runner.scene_difference(a, c)["mean_rgb_delta"], 15)

    def test_assessment_accepts_registered_bootstrap_and_rejects_incomplete_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scene = root / "scene.png"
            scene.write_bytes(b"reviewed scene")
            scene_manifest = root / "scene.json"
            runs = [root / f"run-{index}" for index in range(3)]
            cpu = (1000, 1010, 1020)
            for index, path in enumerate(runs):
                path.mkdir()
                (path / "ready-scene.png").write_bytes(scene.read_bytes())
                summary = {"swap_samples": 29, "power_samples": 27,
                           "eu4_cpu_ms_per_s": cpu[index],
                           "combined_w": 9.0 + index*.02,
                           "median_swaps_s": 52.0 + index*.1}
                (path / "summary.json").write_text(json.dumps(summary))
                (path / "manifest.json").write_text(json.dumps({
                    "status": "complete_unverified_scene" if index == 0 else "complete",
                    "evidence": {"fixture": "fixed", "probe": "fixed"},
                    "summary": summary,
                }))
            scene_manifest.write_text(json.dumps({
                "source_run": str(runs[0]),
                "sha256": hashlib.sha256(scene.read_bytes()).hexdigest(),
            }))
            with patch.object(runner, "SCENE_MANIFEST", scene_manifest):
                report = runner.assess_runs(root, runs)
                self.assertTrue(report["stable"])
                self.assertEqual(report["summary"]["eu4_cpu_ms_per_s"]["median"], 1010)
                metadata = json.loads((runs[2] / "manifest.json").read_text())
                metadata["status"] = "incomplete"
                (runs[2] / "manifest.json").write_text(json.dumps(metadata))
                with self.assertRaisesRegex(runner.base.BenchmarkError, "not complete"):
                    runner.assess_runs(root, runs)

    def test_thirty_second_summary_aligns_full_swap_and_power_samples(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            start = dt.datetime(2026, 9, 28, tzinfo=dt.timezone.utc)
            wall_ns = int(start.timestamp()*1e9)
            anchor = {"monotonic_ns": 0, "wall_ns": wall_ns}
            lines = [f"S,{second*1_000_000_000},{wall_ns+second*1_000_000_000},"
                     f"1000000000,60,60" for second in range(1, 31)]
            (root / "auto-probe.csv").write_text("\n".join(lines)+"\n")
            raw = [{"timestamp": start+dt.timedelta(seconds=second),
                    "processor": {"cpu_power": 8000, "gpu_power": 2000,
                                  "combined_power": 10000},
                    "gpu": {"idle_ratio": .2},
                    "tasks": [{"pid": 42, "cputime_ms_per_s": 1000}]}
                   for second in range(1, 31)]
            result = runner.summarize(root, [{"name": "measure", "start_ns": 0,
                                              "end_ns": 31_000_000_000}],
                                      anchor, 42, raw)
            self.assertEqual(result["median_swaps_s"], 60)
            self.assertEqual(result["eu4_cpu_ms_per_s"], 1000)
            self.assertEqual(result["combined_w"], 10)
            self.assertEqual(result["eu4_cpu_ms_per_swap"], 16.667)


if __name__ == "__main__":
    unittest.main()
