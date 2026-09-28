import datetime as dt
import argparse
import csv
import contextlib
import importlib.util
import io
import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "benchmark/eu4_benchmark.py"
spec = importlib.util.spec_from_file_location("eu4_benchmark", MODULE_PATH)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class PowermetricsTests(unittest.TestCase):
    def test_power_guard_requires_the_same_enabled_game_process_and_controls(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "20260927T062445Z-paused-mp" / "metadata.json"
            path.parent.mkdir()
            path.write_text(json.dumps({
                "status": "complete", "multithreaded_gl_requested": True,
                "game_pid": 42, "gog": {"executable_sha256": "binary"},
                "save_sha256": "save", "settings": {"render_settings_sha256": "render"},
                "dlc_mods": {"sha256": "mods"},
                "multithreaded_gl_contexts": [{"after": 1, "enable_error": 0}],
            }))
            self.assertEqual(benchmark.matching_multithreaded_run(
                Path(temporary), 42, "binary", "save", "render", "mods"), path.parent)
            with self.assertRaisesRegex(benchmark.BenchmarkError, "No matching active"):
                benchmark.matching_multithreaded_run(
                    Path(temporary), 43, "binary", "save", "render", "mods")
            with self.assertRaisesRegex(benchmark.BenchmarkError, "No matching active"):
                benchmark.matching_multithreaded_run(
                    Path(temporary), 42, "binary", "save", "changed", "mods")

    def test_nul_separated_plists_and_milliwatt_conversion(self):
        samples = [
            {"timestamp": dt.datetime(2026, 9, 26, 12, 0, i),
             "elapsed_ns": 1_000_000_000,
             "processor": {"cpu_energy": 500, "cpu_power": 2000 + 1000 * i,
                           "gpu_energy": 100, "gpu_power": 500,
                           "combined_power": 3000 + 1000 * i},
             "gpu": {"idle_ratio": 0.75}, "thermal_pressure": "Nominal"}
            for i in (0, 1)
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "raw.pliststream"
            path.write_bytes(b"\0".join(plistlib.dumps(sample) for sample in samples) + b"\0")
            parsed = benchmark.read_plist_stream(path)
        self.assertEqual(len(parsed), 2)
        row = benchmark.normalized_sample(parsed[1], 1)
        self.assertEqual(row["cpu_w"], 3)
        self.assertEqual(row["gpu_w"], 0.5)
        self.assertEqual(row["combined_w"], 4)
        self.assertEqual(row["interval_s"], 1)
        self.assertEqual(row["gpu_active_pct"], 25)
        self.assertEqual(row["thermal_pressure"], "Nominal")

    def test_missing_power_is_rejected(self):
        with self.assertRaisesRegex(benchmark.BenchmarkError, "power fields missing"):
            benchmark.normalized_sample({"processor": {"cpu_energy": 1000}}, 0)

    def test_energy_fallback_uses_sample_duration(self):
        row = benchmark.normalized_sample({"elapsed_ns": 2_000_000_000,
                                           "processor": {"cpu_energy": 1000, "gpu_energy": 200}}, 0)
        self.assertEqual(row["cpu_w"], 0.5)
        self.assertEqual(row["gpu_w"], 0.1)

    def test_process_path_must_match_gog_executable(self):
        steam = "/Users/test/Library/Application Support/Steam/steamapps/common/Europa Universalis IV/eu4.app/Contents/MacOS/eu4"
        output = f"  100 {steam}\n  200 {benchmark.GOG_EXE}\n"
        with patch.object(benchmark, "command", return_value=output), \
             patch.object(benchmark, "process_executable_path", side_effect=lambda pid: steam if pid == 100 else str(benchmark.GOG_EXE)):
            with self.assertRaisesRegex(benchmark.BenchmarkError, "Expected one running GOG"):
                benchmark.running_game_pid("paused")
        with patch.object(benchmark, "command", return_value="  200 ./eu4\n"), \
             patch.object(benchmark, "process_executable_path", return_value=str(benchmark.GOG_EXE)):
            self.assertEqual(benchmark.running_game_pid("paused"), 200)

    def test_power_mode_parses_macos_27_and_legacy_output(self):
        outputs = {
            ("pmset", "-g", "ps"): "Now drawing from 'AC Power'\n",
            ("pmset", "-g", "custom"): "AC Power:\n powermode 1\n",
            ("pmset", "-g"): "Currently in use:\n powermode 1\n",
        }
        with patch.object(benchmark, "command", side_effect=lambda *args: outputs[args]):
            self.assertEqual(benchmark.power_state()["mode"], "low")
        outputs[("pmset", "-g", "custom")] = "AC Power:\n lowpowermode 0\n"
        outputs[("pmset", "-g")] = "Currently in use:\n lowpowermode 0\n"
        with patch.object(benchmark, "command", side_effect=lambda *args: outputs[args]):
            self.assertEqual(benchmark.power_state()["mode"], "normal")

    def test_display_probe_checks_game_foreground_mode_without_capture(self):
        args = argparse.Namespace(lead_in=5, expect_hz=60)
        before = {"refresh_hz": 60, "verified": True}
        after = {"refresh_hz": 120, "verified": True}
        output = io.StringIO()
        with patch.object(benchmark, "running_game_pid", return_value=42), \
             patch.object(benchmark, "display_mode", side_effect=[before, after]), \
             patch.object(benchmark, "prompt", return_value=""), \
             patch.object(benchmark.time, "sleep"), \
             patch.object(benchmark, "cue"), contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(benchmark.BenchmarkError, "observed 120 Hz"):
                benchmark.probe_display(args)
        self.assertIn('"with_game_foreground"', output.getvalue())

    def test_short_cpu_profile_keeps_a_reviewable_call_stack_file(self):
        def fake_run(command, **kwargs):
            if command[:3] == ["sudo", "-n", "sample"]:
                kwargs["stdout"].write(b"Call graph:\nthread stack\n")
            return argparse.Namespace(returncode=0)

        with tempfile.TemporaryDirectory() as temporary:
            args = argparse.Namespace(seconds=5, lead_in=0, output=temporary)
            with patch.object(benchmark, "running_game_pid", return_value=42), \
                 patch.object(benchmark, "display_mode", return_value={"refresh_hz": 120, "verified": True}), \
                 patch.object(benchmark, "power_state", return_value={"mode": "normal"}), \
                 patch.object(benchmark, "game_settings", return_value={}), \
                 patch.object(benchmark, "gog_identity", return_value={"executable_sha256": "gog"}), \
                 patch.object(benchmark, "prompt", return_value=""), \
                 patch.object(benchmark.time, "sleep"), \
                 patch.object(benchmark, "cue"), \
                 patch.object(benchmark.subprocess, "run", side_effect=fake_run):
                benchmark.profile_cpu(args)
            run_dir = next(Path(temporary).iterdir())
            self.assertEqual(json.loads((run_dir / "metadata.json").read_text())["status"], "complete")
            self.assertIn("Call graph", (run_dir / "cpu-sample.txt").read_text())

    def test_fps_probe_requires_a_visible_dtrace_swap_probe(self):
        no_probe = argparse.Namespace(returncode=0, stdout="ID PROVIDER MODULE FUNCTION NAME\n", stderr="")
        with patch.object(benchmark.subprocess, "run", return_value=no_probe):
            with self.assertRaisesRegex(benchmark.BenchmarkError, "could not expose"):
                benchmark.dtrace_frame_probe(42)

    def test_fps_probe_explains_rosetta_dtrace_failure(self):
        translated = argparse.Namespace(
            returncode=1, stdout="",
            stderr="dtrace: failed to grab pid 42: DTrace cannot instrument translated processes",
        )
        with patch.object(benchmark.subprocess, "run", return_value=translated):
            with self.assertRaisesRegex(benchmark.BenchmarkError, "runs under Rosetta"):
                benchmark.dtrace_frame_probe(42)

    def test_fps_probe_counts_swaps_using_measured_elapsed_time(self):
        def fake_run(command, **kwargs):
            if command == ["sudo", "-v"]:
                return argparse.Namespace(returncode=0)
            if "-l" in command:
                return argparse.Namespace(returncode=0,
                                          stdout="1 pid42 eu4 Cocoa_GL_SwapWindow entry\n", stderr="")
            if "-q" in command:
                kwargs["stdout"].write(b"frames=600\nelapsed_ns=5000000000\n")
                return argparse.Namespace(returncode=0)
            raise AssertionError(command)

        with tempfile.TemporaryDirectory() as temporary:
            args = argparse.Namespace(check=False, seconds=5, lead_in=0, output=temporary)
            with patch.object(benchmark, "running_game_pid", return_value=42), \
                 patch.object(benchmark, "display_mode", return_value={"refresh_hz": 120, "verified": True}), \
                 patch.object(benchmark, "gog_identity", return_value={"executable_sha256": "gog"}), \
                 patch.object(benchmark, "prompt", return_value=""), \
                 patch.object(benchmark.time, "sleep"), \
                 patch.object(benchmark, "cue"), \
                 patch.object(benchmark.subprocess, "run", side_effect=fake_run):
                benchmark.probe_fps(args)
            metadata = json.loads(next(Path(temporary).iterdir()).joinpath("metadata.json").read_text())
            self.assertEqual(metadata["status"], "complete")
            self.assertEqual(metadata["swaps_per_second"], 120)

    def test_idle_capture_writes_raw_and_parsed_samples(self):
        sample = {"elapsed_ns": 1_000_000_000,
                  "processor": {"cpu_power": 1000, "gpu_power": 200},
                  "gpu": {"idle_ratio": 0.9}, "thermal_pressure": "Nominal"}
        stream = b"\0".join(plistlib.dumps(sample) for _ in range(5)) + b"\0"

        class FakePowermetrics:
            returncode = 0

            def __init__(self, _command, stdout, stderr):
                stdout.write(stream)
                stdout.flush()

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        args = argparse.Namespace(scenario="idle", trial=1, seconds=5, lead_in=5, refresh_hz=60,
                                  power_mode="normal", save=str(benchmark.DEFAULT_SAVE),
                                  output="", start_date=None, end_date=None, fps=None,
                                  non_interactive=True, save_confirmed=False, allow_autosave=False)
        with tempfile.TemporaryDirectory() as temporary:
            args.output = temporary
            with patch.object(benchmark, "gog_identity", return_value={"executable_sha256": "gog"}), \
                 patch.object(benchmark, "running_game_pid", return_value=None), \
                 patch.object(benchmark, "power_state", return_value={"source": "AC Power", "mode": "normal"}), \
                 patch.object(benchmark, "display_mode", return_value={"refresh_hz": 60, "verified": True}), \
                 patch.object(benchmark, "game_settings", return_value={}), \
                 patch.object(benchmark, "mod_settings", return_value={}), \
                 patch.object(benchmark.subprocess, "run", return_value=argparse.Namespace(returncode=0)), \
                 patch.object(benchmark.subprocess, "Popen", FakePowermetrics):
                benchmark.capture(args)
            run_dir = next(Path(temporary).iterdir())
            metadata = json.loads((run_dir / "metadata.json").read_text())
            self.assertEqual(metadata["status"], "complete")
            self.assertEqual(metadata["samples"], 5)
            self.assertEqual(len(benchmark.read_plist_stream(run_dir / "powermetrics.pliststream")), 5)
            self.assertIn("1.0", (run_dir / "samples.csv").read_text())

    def test_save_backup_is_content_addressed_and_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "lubeck.eu4"
            source.write_bytes(b"fixed-save")
            digest = benchmark.sha256(source)
            backup = benchmark.backup_save(source, root / "results", digest)
            self.assertEqual(backup.read_bytes(), b"fixed-save")
            self.assertEqual(benchmark.backup_save(source, root / "results", digest), backup)
            backup.write_bytes(b"tampered")
            with self.assertRaisesRegex(benchmark.BenchmarkError, "has changed"):
                benchmark.backup_save(source, root / "results", digest)

    def test_allow_autosave_records_changed_save(self):
        sample = {"elapsed_ns": 1_000_000_000,
                  "processor": {"cpu_power": 1000, "gpu_power": 200}}
        stream = b"\0".join(plistlib.dumps(sample) for _ in range(5)) + b"\0"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save = root / "campaign.eu4"
            save.write_bytes(b"before")
            initial_hash = benchmark.sha256(save)

            class FakePowermetrics:
                returncode = 0

                def __init__(self, _command, stdout, stderr):
                    stdout.write(stream)
                    save.write_bytes(b"after")

                def poll(self):
                    return 0

                def wait(self, timeout=None):
                    return 0

            args = argparse.Namespace(scenario="speed1", trial=1, seconds=5, lead_in=5,
                                      refresh_hz=60, power_mode="normal", save=str(save),
                                      output=str(root / "results"), start_date="1444.11.11",
                                      end_date="1444.11.12", fps=None, non_interactive=True,
                                      save_confirmed=True, allow_autosave=True)
            with patch.object(benchmark, "gog_identity", return_value={"executable_sha256": "gog"}), \
                 patch.object(benchmark, "running_game_pid", return_value=42), \
                 patch.object(benchmark, "power_state", return_value={"source": "AC Power", "mode": "normal"}), \
                 patch.object(benchmark, "display_mode", return_value={"refresh_hz": 60, "verified": True}), \
                 patch.object(benchmark, "game_settings", return_value={"autosave": "YEARLY"}), \
                 patch.object(benchmark, "mod_settings", return_value={}), \
                 patch.object(benchmark.subprocess, "run", return_value=argparse.Namespace(returncode=0)), \
                 patch.object(benchmark.subprocess, "Popen", FakePowermetrics):
                benchmark.capture(args)
            run_dir = next(path for path in (root / "results").iterdir() if path.is_dir() and not path.name.startswith("_"))
            metadata = json.loads((run_dir / "metadata.json").read_text())
            self.assertEqual(metadata["status"], "complete")
            self.assertEqual(metadata["save_sha256"], initial_hash)
            self.assertEqual(metadata["save_sha256_after"], benchmark.sha256(save))
            self.assertTrue(metadata["save_changed"])

    def test_autosave_change_does_not_change_render_signature(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Path(temporary) / "settings.txt"
            settings.write_text('graphics={ size={ x=1920 y=1200 } vsync=yes }\n'
                                'mapRenderingOptions={ draw_water=yes }\nautosave="YEARLY"\n')
            with patch.object(benchmark, "USER_DATA", Path(temporary)):
                before = benchmark.game_settings()
                settings.write_text(settings.read_text().replace('"YEARLY"', '"NEVER"'))
                after = benchmark.game_settings()
                self.assertNotEqual(before["sha256"], after["sha256"])
                self.assertEqual(before["render_settings_sha256"], after["render_settings_sha256"])
                settings.write_text(settings.read_text().replace("vsync=yes", "vsync=no"))
                self.assertNotEqual(after["render_settings_sha256"],
                                    benchmark.game_settings()["render_settings_sha256"])


class ReportTests(unittest.TestCase):
    def test_low_power_screen_reports_power_and_throughput_tradeoff(self):
        shared = {"scenario": "speed5", "refresh_verified": True,
                  "graphics_engine": "standard",
                  "gog_sha256": "gog", "save_sha256": "save", "power_source": "AC Power",
                  "refresh_hz": 120, "game_resolution": "3456x2234",
                  "render_settings_sha256": "render", "vsync": "yes",
                  "mod_selection_sha256": "mods"}
        normal = {**shared, "power_mode": "normal", "combined_w": 12,
                  "days_per_second": 10}
        low = {**shared, "power_mode": "low", "combined_w": 6,
               "days_per_second": 7}
        observation = benchmark.power_mode_observation([normal, low])
        self.assertIn("-50.0%", observation)
        self.assertIn("-30.0%", observation)
        self.assertIn("+40.0%", observation)

    def test_report_attributes_distinct_gpu_and_cpu_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for scenario, cpu, gpu, active, eu4_cpu in (
                ("idle", 1.0, 0.2, 2, None),
                ("paused", 2.0, 2.0, 30, 40),
                ("speed5", 8.0, 2.1, 32, 160),
            ):
                for trial in (1, 2, 3):
                    run = root / f"{scenario}-{trial}"
                    run.mkdir()
                    meta = {"status": "complete", "scenario": scenario, "trial": trial,
                            "power_mode_requested": "normal", "power": {"source": "AC Power"},
                            "display": {"refresh_hz": 60, "verified": True},
                            "display_capture_phase": "after_game_focus" if scenario != "idle" else "preflight",
                            "settings": {"game_resolution": "3456x2234", "vsync": "yes"},
                            "dlc_mods": {"sha256": "mods"}, "gog": {"executable_sha256": "gog"},
                            "save_sha256": None if scenario == "idle" else "save",
                            "days_per_second": 10 if scenario == "speed5" else None,
                            "fps_observed": None}
                    (run / "metadata.json").write_text(json.dumps(meta))
                    benchmark.write_csv(run / "samples.csv", benchmark.CSV_FIELDS, [
                        {"sample": 0, "timestamp": "", "interval_s": 1,
                         "cpu_w": cpu + trial * 0.01,
                         "gpu_w": gpu + trial * 0.01, "combined_w": cpu + gpu,
                         "gpu_active_pct": active + trial * 0.01, "thermal_pressure": "Nominal"}])
                    benchmark.write_csv(run / "process.csv", ("elapsed_s", "pid", "cpu_pct", "rss_kb"),
                                        [] if eu4_cpu is None else [{"elapsed_s": 1, "pid": 42,
                                                                     "cpu_pct": eu4_cpu + trial,
                                                                     "rss_kb": 1000}])
            runs = benchmark.load_runs(root)
            self.assertIn("Both contributions are supported", benchmark.classify(runs))
            self.assertIn("CPU power +6.00 W", benchmark.screening_observation(runs))
            experimental = root / "paused-multithreaded"
            experimental.mkdir()
            source = json.loads((root / "paused-1" / "metadata.json").read_text())
            source["multithreaded_gl_source_run"] = "a measured multithreaded process"
            (experimental / "metadata.json").write_text(json.dumps(source))
            benchmark.write_csv(experimental / "samples.csv", benchmark.CSV_FIELDS, [
                {"sample": 0, "timestamp": "", "interval_s": 1, "cpu_w": 12,
                 "gpu_w": 3, "combined_w": 15, "gpu_active_pct": 50,
                 "thermal_pressure": "Nominal"}])
            benchmark.write_csv(experimental / "process.csv",
                                ("elapsed_s", "pid", "cpu_pct", "rss_kb"), [])
            runs = benchmark.load_runs(root)
            self.assertIn("Both contributions are supported", benchmark.classify(runs))
            report_dir = root / "report"
            benchmark.report(type("Args", (), {"input": str(root), "output": str(report_dir)})())
            self.assertTrue((report_dir / "power.svg").is_file())
            self.assertTrue((report_dir / "throughput.svg").is_file())
            self.assertIn("Both contributions are supported", (report_dir / "report.md").read_text())
            with (report_dir / "summary.csv").open(newline="") as source:
                paused = [row for row in csv.DictReader(source) if row["scenario"] == "paused"]
            self.assertEqual({row["graphics_engine"] for row in paused},
                             {"standard", "multithreaded OpenGL"})


if __name__ == "__main__":
    unittest.main()
