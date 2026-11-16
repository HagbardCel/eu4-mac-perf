"""Reusable Metal replay core: C ABI and standalone CLI equivalence.

Stage A/B coverage for PR 2.  The tests build the actual core and exercise the
canonical M6 fixtures; no EU4 process is launched and no OpenGL context is
created.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import m6_compare, m6_frame  # noqa: E402

CORE = BENCH / "eu4_gfx_frame_replay.mm"
CLI = BENCH / "eu4_gfx_frame_replay_cli.mm"
ABI_HOST = ROOT / "tests" / "gfx_metal_replay_abi_host.mm"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _fixture(name: str) -> tuple[Path, Path] | None:
    candidates = [
        ROOT / ".local" / name,
        ROOT.parent / "eu4-mac-perf" / ".local" / name,
    ]
    for root in os.environ.get("EU4_EVIDENCE_ROOT", "").split(os.pathsep):
        if root:
            candidates.append(Path(root) / ".local" / name)
    for candidate in candidates:
        if name == "m6/frames/menu-occ300":
            frame = candidate / "frame"
            shaders = candidate / "translated"
        else:
            frame = candidate / "capture"
            shaders = candidate / "shaders"
        if (frame / "frame-trace.json").is_file() and (shaders / "shaders.json").is_file():
            return frame, shaders
    return None


@unittest.skipUnless(sys.platform == "darwin", "the Metal replay core is macOS")
class MetalReplayLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = Path(cls.temporary.name)
        cls.cli = base / "gfx_frame_replay"
        cls.abi = base / "gfx_metal_replay_abi"
        common = [
            "clang",
            "-arch",
            "arm64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-fobjc-arc",
            "-lc++",
            "-framework",
            "Foundation",
            "-framework",
            "Metal",
            "-I",
            str(BENCH),
        ]
        for output, sources in (
            (cls.cli, [CORE, CLI]),
            (cls.abi, [CORE, ABI_HOST]),
        ):
            built = subprocess.run(
                [*common, "-o", str(output), *map(str, sources)],
                capture_output=True,
                text=True,
                check=False,
                timeout=240,
            )
            if built.returncode:
                raise unittest.SkipTest(built.stderr.strip() or "core build failed")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _cli(self, frame: Path, shaders: Path, output: Path):
        result = subprocess.run(
            [
                str(self.cli),
                "--frame",
                str(frame),
                "--shaders",
                str(shaders),
                "--out",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        return result

    def _abi(self, frame: Path | None, shaders: Path | None, output: Path,
             loops: int = 1, *extra: str):
        command = [str(self.abi), "--out", str(output), "--loops", str(loops)]
        if frame is not None:
            command += ["--frame", str(frame), "--shaders", str(shaders)]
        command += list(extra)
        return subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=300
        )

    @staticmethod
    def _abi_report(result: subprocess.CompletedProcess) -> dict:
        return json.loads(result.stdout.splitlines()[-1])

    @staticmethod
    def _manifest(events: int = 0) -> dict:
        return {
            "schema": "gfx-frame-trace/v1",
            "complete": True,
            "events": events,
            "frame": {
                "width": 4,
                "height": 4,
                "origin": "lower_left",
                "pixel_format": "BGRA8",
                "row_stride": 16,
                "reference_sha256": "0" * 64,
            },
        }

    @staticmethod
    def _draw_event(**overrides) -> dict:
        event = {
            "kind": "draw",
            "target": {"framebuffer": 0, "width": 4, "height": 4},
            "program": {"name": 1, "uniforms": []},
            "draw": {"mode": 4, "count": 3, "instance_count": 1},
            "state": {},
            "attributes": [],
            "textures": [],
        }
        event.update(overrides)
        return event

    def _write_minimal_case(self, base: Path, *, manifest: dict | None,
                            events: list[dict] | None = None,
                            event_text: str | None = None,
                            shaders: bool = True,
                            shader_programs: list[dict] | None = None):
        frame = base / "frame"
        shader_dir = base / "shaders"
        frame.mkdir(parents=True)
        shader_dir.mkdir()
        if manifest is not None:
            (frame / "frame-trace.json").write_text(json.dumps(manifest))
        if events is not None:
            (frame / "frame-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n"
            )
        if event_text is not None:
            (frame / "frame-events.jsonl").write_text(event_text)
        if shaders:
            (shader_dir / "shaders.json").write_text(
                json.dumps({"programs": shader_programs or []})
            )
        return frame, shader_dir

    def _canonical_case(self, base: Path) -> tuple[Path, Path, list[dict]]:
        fixture = _fixture("m6/frames/menu-occ300")
        if fixture is None:
            self.skipTest("canonical M6 frame is absent in this worktree")
        source_frame, source_shaders = fixture
        frame = base / "frame"
        shader_dir = base / "shaders"
        frame.mkdir(parents=True)
        shader_dir.mkdir()
        os.symlink(source_frame / "blobs", frame / "blobs",
                   target_is_directory=True)
        shutil.copy(source_frame / "frame-trace.json", frame)
        shutil.copy(source_frame / "frame-reference.bgra", frame)
        shutil.copy(source_shaders / "shaders.json", shader_dir)
        for metal in source_shaders.glob("*.metal"):
            os.symlink(metal, shader_dir / metal.name)
        events = [
            json.loads(line)
            for line in (source_frame / "frame-events.jsonl").read_text().splitlines()
            if line
        ]
        return frame, shader_dir, events

    def assert_structured_failure(self, result, output: Path, stage: str):
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(result.returncode, 128, "native replay crashed")
        report = self._abi_report(result)
        self.assertEqual(report["ok"], 0)
        self.assertEqual(report["stage"], stage, report)
        self.assertIn(stage, report["error"])
        self.assertGreaterEqual(report["failed"], 1)
        self.assertGreaterEqual(report["duration_ns"], 0)
        self.assertTrue(report["error"])
        replay_path = output / "replay.json"
        self.assertTrue(replay_path.is_file())
        replay = json.loads(replay_path.read_text())
        self.assertEqual(replay["failure"]["stage"], stage)
        self.assertEqual(replay["status"], "failed")
        self.assertEqual(replay["failed"], report["failed"])
        return report, replay

    def test_library_and_cli_agree_on_the_canonical_title_frame(self):
        fixture = _fixture("m6/frames/menu-occ300")
        if fixture is None:
            self.skipTest("canonical M6 frame is absent in this worktree")
        frame, shaders = fixture
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            cli_out = base / "cli"
            abi_out = base / "abi"
            cli = self._cli(frame, shaders, cli_out)
            self.assertEqual(cli.returncode, 0, cli.stderr)
            abi = self._abi(frame, shaders, abi_out)
            self.assertEqual(abi.returncode, 0, abi.stderr)
            cli_report = json.loads((cli_out / "replay.json").read_text())
            abi_report = json.loads(abi.stdout.splitlines()[-1])
            self.assertEqual(cli_report["events"], 326)
            self.assertEqual(cli_report["encoded"], 326)
            self.assertEqual(cli_report["failed"], 0)
            self.assertTrue(cli_report["readback"])
            self.assertEqual(abi_report["events"], 326)
            self.assertEqual(abi_report["encoded"], 326)
            self.assertEqual(abi_report["failed"], 0)
            self.assertEqual(abi_report["command_status"], 4)
            self.assertTrue(abi_report["readback_ok"])
            self.assertEqual(
                abi_report["manifest_reference_sha256"],
                cli_report["manifest_reference_sha256"],
            )
            self.assertEqual(
                abi_report["metal_frame_sha256"],
                "ff2f4e2b9d2cfeef8509dc6777213c859a7ef6af7bc6ad37c4ca564ce57deb15",
            )
            self.assertEqual(
                _sha(cli_out / "metal-frame.bgra"),
                abi_report["metal_frame_sha256"],
            )
            self.assertGreater(abi_report["duration_ns"], 0)
            self.assertNotIn("failure", cli_report)
            self.assertNotIn("error", cli_report)
            self.assertEqual(cli_report["status"], "ok")
            self.assertEqual(cli_report["command_status"], 4)
            self.assertEqual(cli_report["failed"], 0)

    def test_reused_session_does_not_retain_stale_render_targets(self):
        fixture = _fixture("m6/frames/menu-occ300")
        if fixture is None:
            self.skipTest("canonical M6 frame is absent in this worktree")
        frame, shaders = fixture
        with tempfile.TemporaryDirectory() as temporary:
            result = self._abi(
                frame, shaders, Path(temporary) / "out", 2,
                "--reuse-session",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            reports = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([item["iteration"] for item in reports], [0, 1])
            self.assertEqual(
                {item["metal_frame_sha256"] for item in reports},
                {"ff2f4e2b9d2cfeef8509dc6777213c859a7ef6af7bc6ad37c4ca564ce57deb15"},
            )
            self.assertTrue(all(item["ok"] for item in reports))

    def test_invalid_trace_and_invalid_config_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "frame").mkdir()
            (base / "shaders").mkdir()
            cli = self._cli(base / "frame", base / "shaders", base / "out")
            self.assertNotEqual(cli.returncode, 0)
            abi = self._abi(base / "frame", base / "shaders", base / "out2")
            self.assertNotEqual(abi.returncode, 0)
            report = json.loads(abi.stdout.splitlines()[-1])
            self.assertEqual(report["ok"], 0)
            self.assertFalse((base / "out2" / "metal-frame.bgra").exists())
            bad = self._abi(None, None, base / "out3", 1, "--bad-config")
            self.assertEqual(bad.returncode, 0, bad.stderr)
            self.assertTrue(json.loads(bad.stdout)["create_failed"])

    def test_timeout_is_bounded_and_reported(self):
        fixture = _fixture("m6/frames/menu-occ300")
        if fixture is None:
            self.skipTest("canonical M6 frame is absent in this worktree")
        frame, shaders = fixture
        with tempfile.TemporaryDirectory() as temporary:
            result = self._abi(
                frame, shaders, Path(temporary) / "out", 1,
                "--timeout-ms", "1",
            )
            self.assertNotEqual(result.returncode, 0)
            report = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(report["ok"], 0)
            self.assertLess(report["duration_ns"], 10_000_000_000)

    def test_unwritable_output_is_a_failure_not_a_silent_success(self):
        fixture = _fixture("m6/frames/menu-occ300")
        if fixture is None:
            self.skipTest("canonical M6 frame is absent in this worktree")
        frame, shaders = fixture
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            output.mkdir()
            output.chmod(0o555)
            try:
                result = self._abi(frame, shaders, output)
            finally:
                output.chmod(0o755)
            self.assertNotEqual(result.returncode, 0)
            report = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(report["ok"], 0)
            self.assertGreaterEqual(report["failed"], 1)
            self.assertIn("artifact_write", report["error"])
            self.assertFalse(report["result_json"])

    def test_coordinator_rejects_missing_seal_and_stale_artifacts(self):
        from tests.test_m7_frame_seal import _write_frame

        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"
            missing = subprocess.run(
                [
                    sys.executable, "-m", "automation.m6_frame",
                    "shadow-coordinator", "--run", str(run),
                    "--timeout", "2", "--seal-timeout", "0.5",
                ],
                cwd=str(ROOT), capture_output=True, text=True, check=False,
                timeout=30,
            )
            self.assertNotEqual(missing.returncode, 0)
            stale_run = Path(temporary) / "stale"
            _write_frame(stale_run / "frame")
            (stale_run / "shadow" / "shaders").mkdir(parents=True)
            (stale_run / "shadow" / "shaders" / "ready").write_text("stale")
            stale = subprocess.run(
                [
                    sys.executable, "-m", "automation.m6_frame",
                    "shadow-coordinator", "--run", str(stale_run),
                    "--timeout", "5", "--seal-timeout", "2",
                ],
                cwd=str(ROOT), capture_output=True, text=True, check=False,
                timeout=30,
            )
            self.assertNotEqual(stale.returncode, 0)
            report = json.loads(
                (stale_run / "shadow" / "coordinator.json").read_text()
            )
            self.assertIn("stale", report.get("error", ""))

    def test_partial_replay_is_explicitly_marked(self):
        fixture = _fixture("m6/frames/menu-occ300")
        if fixture is None:
            self.skipTest("canonical M6 frame is absent in this worktree")
        frame, shaders = fixture
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            result = subprocess.run(
                [
                    str(self.cli), "--frame", str(frame),
                    "--shaders", str(shaders), "--out", str(output),
                    "--stop-after", "1",
                ],
                capture_output=True, text=True, check=False, timeout=300,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "replay.json").read_text())
            self.assertTrue(report["partial"])
            self.assertLess(report["encoded"], report["events"])

    def test_timeout_and_command_failure_are_classified_distinctly(self):
        fixture = _fixture("m6/frames/menu-occ300")
        if fixture is None:
            self.skipTest("canonical M6 frame is absent in this worktree")
        frame, shaders = fixture
        expectations = {
            1: (1, 0, "event_boundary"),  # event boundary timeout
            2: (1, 1, "frame_completion"),  # final command timeout
            3: (1, 1, "frame_readback"),  # readback timeout
            4: (0, 1, "frame_completion"),  # ordinary command failure
        }
        with tempfile.TemporaryDirectory() as temporary:
            for injection, (timed_out, failed, stage) in expectations.items():
                with self.subTest(injection=injection):
                    output = Path(temporary) / str(injection)
                    result = self._abi(
                        frame, shaders, output, 1,
                        "--inject-failure", str(injection),
                    )
                    self.assertNotEqual(result.returncode, 0)
                    report = json.loads(result.stdout.splitlines()[-1])
                    self.assertEqual(report["ok"], 0)
                    self.assertEqual(report["timed_out"], timed_out)
                    self.assertGreaterEqual(report["failed"], failed)
                    self.assertGreater(report["events"], 0)
                    self.assertEqual(report["stage"], stage, report)
                    self.assertEqual(report["failure"]["stage"], stage)
                    self.assertIn(stage, report["error"])
                    if injection == 4:
                        self.assertIn("injected command failure",
                                      report["error"])
                    else:
                        self.assertIn("deadline", report["error"])

    def test_early_input_failures_are_structured(self):
        cases = [
            ("manifest_load", {"manifest": None, "shaders": True}),
            ("event_stream_load", {"manifest": self._manifest(1)}),
            ("event_stream_load", {"manifest": self._manifest(1),
                                   "event_text": "{not json}\n"}),
            ("frame_structure", {"manifest": self._manifest(1),
                                 "events": [{"kind": "clear", "flags": 1}]}),
            ("shader_load", {"manifest": self._manifest(1),
                             "events": [self._draw_event()],
                             "shaders": False}),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for index, (stage, options) in enumerate(cases):
                with self.subTest(stage=stage, index=index):
                    case = base / f"case-{index}"
                    frame, shaders = self._write_minimal_case(case, **options)
                    output = case / "out"
                    result = self._abi(frame, shaders, output)
                    report, replay = self.assert_structured_failure(
                        result, output, stage
                    )
                    self.assertGreaterEqual(report["events"], 0)
                    self.assertFalse((output / "metal-frame.bgra").exists())
                    self.assertNotIn("objective_c_exception",
                                     report["error"])

    def test_malformed_event_shapes_are_structured_not_crashes(self):
        cases = {
            "target": {"target": "not-an-object"},
            "program": {"program": "not-an-object"},
            "state": {"state": "not-an-object"},
            "attributes": {"attributes": [1]},
            "textures": {"textures": ["not-an-object"]},
            "resolve_rect": {
                "kind": "resolve",
                "source_rect": "not-an-array",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for name, overrides in cases.items():
                with self.subTest(case=name):
                    case = base / name
                    event = self._draw_event(**overrides)
                    frame, shaders = self._write_minimal_case(
                        case, manifest=self._manifest(1), events=[event]
                    )
                    output = case / "out"
                    result = self._abi(frame, shaders, output)
                    report, replay = self.assert_structured_failure(
                        result, output, "input_validation"
                    )
                    self.assertIn("event", report["error"])
                    self.assertLess(result.returncode, 128)

            invalid_utf8 = base / "invalid-utf8"
            frame, shaders = self._write_minimal_case(
                invalid_utf8, manifest=self._manifest(1)
            )
            (frame / "frame-events.jsonl").write_bytes(b"\xff\xfe\n")
            output = invalid_utf8 / "out"
            result = self._abi(frame, shaders, output)
            self.assert_structured_failure(result, output, "event_stream_load")

    def test_replay_stage_failures_preserve_native_detail(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)

            frame, shaders, events = self._canonical_case(base / "program")
            selected = [events[0], dict(events[1])]
            selected[1]["program"] = dict(selected[1]["program"])
            selected[1]["program"]["name"] = 999999
            (frame / "frame-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in selected) + "\n"
            )
            output = base / "program" / "out"
            result = self._abi(frame, shaders, output)
            report, replay = self.assert_structured_failure(
                result, output, "event_encode"
            )
            self.assertIn("999999", report["error"])
            self.assertEqual(report["failure"]["operation"], 1)
            self.assertEqual(report["failure"]["kind"], "draw")

            frame, shaders, events = self._canonical_case(base / "seed")
            selected = [events[0], dict(events[1])]
            selected[1]["seed"] = dict(selected[1]["seed"])
            selected[1]["seed"]["file"] = "missing-seed.bin"
            (frame / "frame-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in selected) + "\n"
            )
            output = base / "seed" / "out"
            result = self._abi(frame, shaders, output)
            report, replay = self.assert_structured_failure(
                result, output, "seed_upload"
            )
            self.assertIn("seed", report["error"])

            frame, shaders, events = self._canonical_case(base / "pre-image")
            selected = [events[0], dict(events[1])]
            selected[1]["pre_image"] = {
                "file": "missing-pre-image.bin",
                "width": 4,
                "height": 4,
            }
            (frame / "frame-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in selected) + "\n"
            )
            output = base / "pre-image" / "out"
            result = self._abi(
                frame, shaders, output, 1,
                "--only-event", "1", "--seed-marked",
            )
            report, replay = self.assert_structured_failure(
                result, output, "pre_image_upload"
            )
            self.assertEqual(report["failure"]["operation"], 1)

            frame, shaders, events = self._canonical_case(base / "checkpoint")
            selected = [events[0], events[1]]
            (frame / "frame-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in selected) + "\n"
            )
            checkpoint = base / "checkpoint" / "checkpoints.json"
            checkpoint.write_text(json.dumps({
                "checkpoints": [{
                    "event": 1,
                    "target": "999999",
                    "file": "unreachable.bgra",
                }]
            }))
            output = base / "checkpoint" / "out"
            result = self._abi(
                frame, shaders, output, 1,
                "--checkpoints", str(checkpoint),
            )
            report, replay = self.assert_structured_failure(
                result, output, "checkpoint_readback"
            )
            self.assertIn("not created", report["error"])
            self.assertEqual(replay["checkpoints"][0]["stage"],
                             "checkpoint_readback")

    def test_shadow_and_coordinator_preserve_native_failure(self):
        from tests.test_m7_frame_seal import _write_frame

        native_error = (
            "event_encode (operation 1): program 999999 has no translated stages"
        )
        native_stage = "event_encode"
        native_failure = {
            "stage": native_stage,
            "operation": 1,
            "event": 1,
            "kind": "draw",
            "caller": "0x1000",
            "error": "program 999999 has no translated stages",
            "timed_out": False,
            "command_status": 4,
        }
        report = {
            "schema": "m7-shadow-report/v1",
            "mode": "single-frame",
            "status": "failed",
            "stage": native_stage,
            "error": native_error,
            "failure": native_failure,
            "metal": {
                "ok": 0,
                "stage": native_stage,
                "error": native_error,
                "failed": 1,
                "events": 2,
                "encoded": 2,
            },
        }

        # The same helper feeds capture-report.json, so the final capture
        # summary cannot replace the native failure with a generic one.
        capture_summary: dict = {}
        detail = m6_frame.apply_shadow_report(capture_summary, report)
        self.assertEqual(detail, native_error)
        self.assertEqual(capture_summary["shadow_stage"], native_stage)
        self.assertEqual(capture_summary["shadow_failure"], native_failure)
        self.assertEqual(capture_summary["diagnostic"]["metal"], "failed")
        self.assertEqual(m6_frame.shadow_failure_detail(report), native_error)
        legacy_report = {
            "error": "in-process shadow failed",
            "stage": "metal_replay",
            "metal": {"stage": native_stage, "error": native_error},
        }
        self.assertEqual(m6_frame.shadow_failure_detail(legacy_report),
                         native_error)

        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"
            _write_frame(run / "frame")
            seal = json.loads((run / "frame" / "frame-seal.json").read_text())
            shadow = run / "shadow"
            (shadow / "shaders").mkdir(parents=True)
            (shadow / "shaders" / "shaders.json").write_text(
                json.dumps({"programs": []})
            )
            coordinator_report = dict(report)
            coordinator_report.update({
                "capture_frame_id": seal["capture_frame_id"],
                "pid": seal["pid"],
                "session_id": seal["session_id"],
                "gl_reference_sha256": seal["reference_sha256"],
                "trace_manifest_sha256": seal["manifest_sha256"],
                "operation_count": seal["operation_count"],
            })

            def fake_run(command, **kwargs):
                self.assertIn("translate", command)
                (shadow / "shadow-report.json").write_text(
                    json.dumps(coordinator_report)
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            args = argparse.Namespace(
                run=str(run),
                shadow_dir=str(shadow),
                timeout=10.0,
                seal_timeout=5.0,
                translation_timeout=5.0,
                replay_timeout=5.0,
                compare_timeout=5.0,
                policy=None,
                no_compare=True,
            )
            with mock.patch.object(m6_frame.subprocess, "run",
                                   side_effect=fake_run):
                status = m6_frame.shadow_coordinator(args)
            self.assertEqual(status, 1)
            coordinator = json.loads(
                (shadow / "coordinator.json").read_text()
            )
            self.assertEqual(coordinator["status"], "failed")
            self.assertEqual(coordinator["stage"], native_stage)
            self.assertEqual(coordinator["error"], native_error)
            self.assertEqual(coordinator["failure"], native_failure)
            self.assertEqual(coordinator["metal"]["stage"], native_stage)

    def test_native_blob_verification_uses_structured_references(self):
        from tests.test_m7_frame_seal import _write_frame

        def run_verify(frame: Path):
            event_data = (frame / "frame-events.jsonl").read_bytes()
            manifest = (frame / "frame-trace.json").read_bytes()
            reference = (frame / "frame-reference.bgra").read_bytes()
            return self._abi(
                frame, frame / "shaders", frame / "out", 1,
                "--event-sha", hashlib.sha256(event_data).hexdigest(),
                "--event-bytes", str(len(event_data)),
                "--manifest-sha", hashlib.sha256(manifest).hexdigest(),
                "--reference-sha", hashlib.sha256(reference).hexdigest(),
            )

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            valid = base / "valid"
            _write_frame(valid)
            (valid / "shaders").mkdir()
            result = run_verify(valid)
            self.assertNotIn("sealed input verification", result.stderr)

            corrupt = base / "corrupt"
            _write_frame(corrupt)
            (corrupt / "blobs" / "attr.bin").write_bytes(b"tampered")
            (corrupt / "shaders").mkdir()
            corrupt_result = run_verify(corrupt)
            self.assertIn("sealed input verification", corrupt_result.stderr)
            corrupt_report = self._abi_report(corrupt_result)
            self.assertEqual(corrupt_report["stage"],
                             "sealed_input_verification")
            self.assertEqual(corrupt_report["failure"]["stage"],
                             "sealed_input_verification")
            self.assertIn("size mismatch", corrupt_report["error"])

            traversal = base / "traversal"
            _write_frame(traversal)
            events = json.loads((traversal / "frame-events.jsonl").read_text().splitlines()[0])
            events["attributes"][0]["data"] = "../attr.bin"
            lines = [json.dumps(events)] + [
                line for line in (traversal / "frame-events.jsonl").read_text().splitlines()[1:]
            ]
            (traversal / "frame-events.jsonl").write_text("\n".join(lines) + "\n")
            (traversal / "shaders").mkdir()
            traversal_result = run_verify(traversal)
            self.assertIn("sealed input verification", traversal_result.stderr)
            traversal_report = self._abi_report(traversal_result)
            self.assertEqual(traversal_report["stage"],
                             "sealed_input_verification")
            self.assertIn("invalid blob path", traversal_report["error"])

            conflict = base / "conflict"
            _write_frame(conflict)
            events = json.loads((conflict / "frame-events.jsonl").read_text().splitlines()[0])
            events["attributes"].append(dict(events["attributes"][0]))
            events["attributes"][1]["sha256"] = "0" * 64
            lines = [json.dumps(events)] + [
                line for line in (conflict / "frame-events.jsonl").read_text().splitlines()[1:]
            ]
            (conflict / "frame-events.jsonl").write_text("\n".join(lines) + "\n")
            (conflict / "shaders").mkdir()
            conflict_result = run_verify(conflict)
            self.assertIn("sealed input verification", conflict_result.stderr)
            conflict_report = self._abi_report(conflict_result)
            self.assertEqual(conflict_report["stage"],
                             "sealed_input_verification")
            self.assertIn("conflicting repeated blob reference",
                          conflict_report["error"])

    def test_core_does_not_link_opengl(self):
        linked = subprocess.run(
            ["otool", "-L", str(self.cli)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(linked.returncode, 0, linked.stderr)
        self.assertNotIn("OpenGL", linked.stdout)

    def test_copy_bearing_scene_preserves_known_divergence(self):
        fixture = _fixture("infra/strategy-b/case-copy-442-moved")
        if fixture is None:
            self.skipTest("copy-bearing scene-75 case is absent in this worktree")
        frame, shaders = fixture
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "replay"
            result = self._cli(frame, shaders, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "replay.json").read_text())
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["events"], 283)
            self.assertEqual(report["encoded"], 283)
            self.assertEqual(report["failed"], 0)
            self.assertEqual(len(report["transfers"]), 1)
            reference = base / "reference"
            candidate = base / "candidate"
            reference.mkdir()
            candidate.mkdir()
            shutil.copy(frame / "frame-trace.json", reference)
            shutil.copy(frame / "frame-events.jsonl", reference)
            shutil.copy(frame / "frame-reference.bgra", reference)
            shutil.copy(output / "replay.json", candidate)
            shutil.copy(output / "metal-frame.bgra", candidate)
            policy = json.loads(
                (ROOT / "analysis" / "m6" / "comparison-policy.json").read_text()
            )
            compared = m6_compare.compare(reference, candidate, policy)
            self.assertEqual(compared["status"], "fail")
            self.assertEqual(compared["statistics"]["pixels"], 1930176)
            self.assertEqual(compared["statistics"]["mismatched_pixels"], 1792005)
            self.assertEqual(compared["statistics"]["max_channel_error"], 23)
