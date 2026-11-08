#!/usr/bin/env python3
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

import automation.live_gfx as live_gfx  # noqa: E402
import automation.state as state  # noqa: E402
import eu4_gfx_observe_registry as registry  # noqa: E402
import eu4_gfx_op_capture as capture  # noqa: E402
import eu4_gfx_startup_trace as startup  # noqa: E402


def _pin_artifact(directory: Path) -> None:
    metadata = json.loads((directory / "metadata.json").read_text())
    metadata["executable_sha256"] = capture.PINNED_SHA
    (directory / "metadata.json").write_text(json.dumps(metadata) + "\n")
    operations = json.loads((directory / "operations-summary.json").read_text())
    operations["executable_sha256"] = capture.PINNED_SHA
    operations["session"] = metadata["session"]
    operations["pid"] = metadata["pid"]
    (directory / "operations-summary.json").write_text(
        json.dumps(operations) + "\n"
    )
    status = "\n".join(
        [
            "EU4-GFX-STARTUP v1",
            "state=installed",
            f"session={metadata['session']}",
            f"pid={metadata['pid']}",
            "executable=/tmp/eu4",
            "arch=x86_64",
            f"image_base={metadata['image_base']}",
            "settings_site=0x0",
            "init_site=0x0",
            f"module={capture.PINNED_SHA}",
            "error=",
            "END",
            "",
        ]
    )
    (directory / "startup.status").write_text(status)


class ObserveHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "darwin":
            raise unittest.SkipTest("observe host is macOS x86_64")
        cls.host = startup.build_host()

    def _run(self, scenario: str, directory: Path) -> subprocess.CompletedProcess:
        command = [str(self.host), scenario, str(directory)]
        if platform.machine() != "x86_64":
            command = ["arch", "-x86_64", *command]
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def _events(self, directory: Path) -> list[dict]:
        return [
            json.loads(line)
            for line in (directory / "operations.jsonl").read_text().splitlines()
            if line.strip()
        ]

    def _summary(self, directory: Path) -> dict:
        return json.loads((directory / "operations-summary.json").read_text())

    def test_vertex_buffer_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("observe-buffers", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            events = self._events(directory)
            self.assertEqual(len(events), 3)
            self.assertEqual(events[0]["kind"], "vertex_buffer_create")
            self.assertEqual(events[0]["resource_id"], "vb_1")
            self.assertTrue(events[0]["contents_known"])
            self.assertEqual(events[0]["payload"]["size"], 256)
            self.assertEqual(events[1]["resource_id"], "vb_1")
            self.assertEqual(events[1]["payload"]["size"], 64)
            self.assertEqual(events[1]["offset"], 32)
            self.assertEqual(events[2]["ids"], ["vb_1"])
            self.assertTrue((directory / events[0]["payload"]["file"]).is_file())
            self.assertTrue((directory / events[1]["payload"]["file"]).is_file())

    def test_viewport_basic(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("observe-basic", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            events = self._events(directory)
            self.assertEqual(len(events), 2)
            self.assertEqual([event["call_id"] for event in events], [3, 4])
            self.assertEqual([event["completion_seq"] for event in events], [1, 2])
            first = bytes.fromhex(events[0]["input_hex"])
            self.assertEqual(first, bytes.fromhex(events[0]["output_hex"]))
            self.assertEqual(len(first), 24)
            summary = self._summary(directory)
            self.assertTrue(summary["integrity_ok"])
            self.assertEqual(summary["events"], 2)

    def test_startup_stream_freezes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("observe-freeze", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertEqual(len(self._events(directory)), 1)
            startup_summary = json.loads((directory / "summary.json").read_text())
            self.assertTrue(startup_summary["complete"])
            self.assertEqual(startup_summary["event_count"], 4)
            self.assertEqual(startup_summary["settings_calls"], 1)
            self.assertEqual(startup_summary["init_calls"], 1)

    def test_quota_refuses_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("observe-quota", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = self._summary(directory)
            self.assertEqual(summary["events"], 1)
            self.assertGreaterEqual(summary["refused"], 1)
            self.assertTrue(summary["integrity_ok"])

    def test_unpaired_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("observe-unpaired", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = self._summary(directory)
            self.assertFalse(summary["integrity_ok"])
            self.assertFalse(summary["records_copied"])
            self.assertEqual(summary["unmatched"], 1)
            self.assertEqual((directory / "operations.jsonl").stat().st_size, 0)

    def test_invalid_output_is_partial_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("observe-invalid-output", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = self._summary(directory)
            self.assertFalse(summary["integrity_ok"])
            self.assertEqual(summary["unavailable"], 1)
            self.assertEqual((directory / "operations.jsonl").stat().st_size, 0)
            partial = directory / "operations.partial.jsonl"
            self.assertGreater(partial.stat().st_size, 0)
            record = json.loads(partial.read_text().splitlines()[0])
            self.assertTrue(record["partial"])
            self.assertFalse(record["fields_ok"])

    def test_two_operation_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("observe-scalar", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            events = self._events(directory)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[1]["symbol"], "_GfxObserveScalar")
            self.assertEqual(events[1]["input_hex"], "05000000")
            self.assertEqual(events[1]["return"], 12)

    def test_install_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run("observe-rollback", Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_duration_stops_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("observe-duration", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = self._summary(directory)
            self.assertTrue(summary["integrity_ok"])
            self.assertEqual(summary["events"], 1)

    def test_rejects_unsafe_and_wrong_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run("observe-reject", Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_rx_failure_restores_original_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run("observe-rx-failure", Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_overlap_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("observe-overlap", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = self._summary(directory)
            self.assertEqual(summary["overlaps"], 1)
            self.assertTrue(self._events(directory)[0]["overlap"])


class CaptureReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "darwin":
            raise unittest.SkipTest("observe host is macOS x86_64")
        cls.host = startup.build_host()

    def _artifact(self, tmp: Path, scenario: str = "observe-basic") -> Path:
        directory = tmp / "capture"
        directory.mkdir()
        command = [str(self.host), scenario, str(directory)]
        if platform.machine() != "x86_64":
            command = ["arch", "-x86_64", *command]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        _pin_artifact(directory)
        return directory

    def test_accepts_resource_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._artifact(Path(tmp), "observe-buffers")
            artifact = capture.load_capture(directory)
            self.assertEqual(len(artifact.operations), 3)
            create = artifact.operations[0]
            self.assertEqual(create.kind, "vertex_buffer_create")
            self.assertEqual(create.resource_id, "vb_1")
            self.assertTrue(create.contents_known)
            self.assertEqual(create.payload_size, 256)
            self.assertIsNotNone(create.payload_sha256)
            self.assertEqual(len(create.payload_bytes or b""), 256)
            self.assertIn(create.payload_file, artifact.source_hashes)

    def test_accepts_complete_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._artifact(Path(tmp))
            artifact = capture.load_capture(directory)
            self.assertEqual(artifact.session, "host-observe")
            self.assertEqual(len(artifact.operations), 2)
            self.assertEqual(artifact.coverage["distinct_inputs"], 2)
            self.assertEqual(artifact.operations[0].input_words[0], 1920)

    def test_rejects_removed_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._artifact(Path(tmp))
            lines = (directory / "operations.jsonl").read_text().splitlines()
            (directory / "operations.jsonl").write_text("\n".join(lines[:1]) + "\n")
            with self.assertRaises(capture.CaptureIncomplete) as caught:
                capture.load_capture(directory)
            self.assertEqual(caught.exception.reason, "op_event_count")

    def test_rejects_invalid_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._artifact(Path(tmp))
            record = json.loads((directory / "operations.jsonl").read_text().splitlines()[0])
            record["fields_ok"] = False
            (directory / "operations.jsonl").write_text(json.dumps(record) + "\n")
            operations = json.loads((directory / "operations-summary.json").read_text())
            operations["events"] = 1
            (directory / "operations-summary.json").write_text(json.dumps(operations) + "\n")
            with self.assertRaises(capture.CaptureIncomplete) as caught:
                capture.load_capture(directory)
            self.assertEqual(caught.exception.reason, "op_fields")

    def test_rejects_mismatched_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._artifact(Path(tmp))
            operations = json.loads((directory / "operations-summary.json").read_text())
            operations["pid"] += 1
            (directory / "operations-summary.json").write_text(json.dumps(operations) + "\n")
            with self.assertRaises(capture.CaptureIncomplete) as caught:
                capture.load_capture(directory)
            self.assertEqual(caught.exception.reason, "op_pid_mismatch")

    def test_zero_events_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._artifact(Path(tmp))
            (directory / "operations.jsonl").write_text("")
            operations = json.loads((directory / "operations-summary.json").read_text())
            operations["events"] = 0
            (directory / "operations-summary.json").write_text(json.dumps(operations) + "\n")
            with self.assertRaises(capture.CaptureIncomplete) as caught:
                capture.load_capture(directory)
            self.assertEqual(caught.exception.reason, "op_no_events")


class PreflightObserveTests(unittest.TestCase):
    def test_rejects_unknown_target(self):
        with self.assertRaises(startup.StartupError):
            startup.preflight_ops(["_NoSuchOperation"])

    def test_accepts_viewport_with_matching_bytes(self):
        mapper = MagicMock()
        mapper.__enter__.return_value.read_bytes_at_rva.return_value = bytes.fromhex(
            "554889e5f30f6f06"
        )
        with patch.object(
            startup,
            "preflight",
            return_value={"executable_sha256": "a", "library_sha256": "b"},
        ), patch.object(startup, "MachOMapper", return_value=mapper), patch.object(
            startup.paths, "load_target", return_value={"image_base": 0x100000000}
        ):
            evidence = startup.preflight_ops(["_GfxSetViewPort"])
        self.assertEqual(evidence["ops"][0]["symbol"], "_GfxSetViewPort")
        self.assertEqual(evidence["ops"][0]["prefix"], "554889e5f30f6f06")
        self.assertEqual(evidence["ops"][0]["rva"], "0x15ea4f8")


class ReplayOutputTests(unittest.TestCase):
    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "out"
            output.mkdir()
            sentinel = '{"sentinel": true}\n'
            (output / "comparison.json").write_text(sentinel)
            bad = root / "capture"
            bad.mkdir()
            result = live_gfx.main(
                [
                    "replay",
                    "--capture",
                    str(bad),
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(result, live_gfx.EXIT_INCOMPLETE)
            self.assertEqual((output / "comparison.json").read_text(), sentinel)

    def test_existing_output_is_refused_for_valid_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            output.mkdir()
            sentinel = '{"sentinel": true}\n'
            (output / "comparison.json").write_text(sentinel)
            result = live_gfx.main(
                [
                    "replay",
                    "--capture",
                    str(ROOT / "analysis" / "gfx-ops" / "v0" / "capture"),
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(result, live_gfx.EXIT_INCOMPLETE)
            self.assertEqual((output / "comparison.json").read_text(), sentinel)

    def test_incomplete_replay_creates_new_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "out"
            bad = root / "capture"
            bad.mkdir()
            result = live_gfx.main(
                [
                    "replay",
                    "--capture",
                    str(bad),
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(result, live_gfx.EXIT_INCOMPLETE)
            comparison = json.loads((output / "comparison.json").read_text())
            self.assertEqual(comparison["status"], "incomplete")


class RegistryTests(unittest.TestCase):
    def test_production_registry(self):
        entries = registry.load_entries()
        self.assertTrue(entries)
        self.assertTrue(all(registry.installable(entry) for entry in entries))
        self.assertEqual(entries[0].symbol, "_GfxSetViewPort")
        self.assertEqual(entries[0].prefix.hex(), "554889e5f30f6f06")
        self.assertEqual(len(entries[0].prefix), 8)

    def test_rejects_malformed(self):
        text = (
            'OBS_ENTRY("a", 0x1000, "\\x55\\x48\\x89\\xe5\\x53", '
            "EU4_GFX_OBSERVE_RELOC_SAFE, a_entry, a_exit)\n"
            'OBS_ENTRY("a", 0x2000, "\\x55\\x48\\x89\\xe5\\x53", '
            "EU4_GFX_OBSERVE_RELOC_SAFE, b_entry, b_exit)\n"
        )
        with self.assertRaises(ValueError):
            registry.parse_observe_entries(text)
        with self.assertRaises(ValueError):
            registry.parse_observe_entries(
                'OBS_ENTRY("a", 0x1000, "\\x55\\x48", '
                "EU4_GFX_OBSERVE_RELOC_SAFE, a_entry, a_exit)\n"
            )


class CampaignLaunchTests(unittest.TestCase):
    def _root(self, limit: int = 3, used: int = 0, deadline=120) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "live-launch.json").write_text(
            json.dumps(
                {
                    "schema": state.LAUNCH_SCHEMA,
                    "campaign_id": "test",
                    "limit": limit,
                    "deadline_seconds": deadline,
                    "used": used,
                    "launches": [],
                }
            )
            + "\n"
        )
        (root / "state.json").write_text('{"sentinel": true}\n')
        return root

    def test_claim_works_while_runner_lock_is_held(self):
        root = self._root()
        before = (root / "state.json").read_text()
        with state.campaign_lock(root / "campaign.lock"):
            token = live_gfx._claim_launch(
                root,
                root / "capture",
                ["_GfxSetViewPort"],
                requested_timeout=120.0,
            )
        counter = json.loads((root / "live-launch.json").read_text())
        self.assertEqual(counter["used"], 1)
        self.assertEqual(counter["launches"][0]["token"], token)
        self.assertEqual((root / "state.json").read_text(), before)

    def test_exhausted_counter_refuses(self):
        root = self._root(limit=1, used=1)
        with self.assertRaises(live_gfx.LaunchLimit) as caught:
            live_gfx._claim_launch(
                root,
                root / "capture",
                ["_GfxSetViewPort"],
                requested_timeout=120.0,
            )
        self.assertEqual(caught.exception.kind, "exhausted")

    def test_campaign_deadline_is_enforced(self):
        root = self._root(limit=3, used=0, deadline=120)
        with self.assertRaises(live_gfx.LaunchLimit) as caught:
            live_gfx._claim_launch(
                root,
                root / "capture",
                ["_GfxSetViewPort"],
                requested_timeout=600.0,
            )
        self.assertEqual(caught.exception.kind, "deadline")
        counter = json.loads((root / "live-launch.json").read_text())
        self.assertEqual(counter["used"], 0)
        token = live_gfx._claim_launch(
            root,
            root / "capture",
            ["_GfxSetViewPort"],
            requested_timeout=120.0,
        )
        self.assertTrue(token)
        counter = json.loads((root / "live-launch.json").read_text())
        self.assertEqual(counter["used"], 1)

    def test_campaign_without_deadline_allows_request(self):
        root = self._root(limit=3, used=0, deadline=None)
        token = live_gfx._claim_launch(
            root,
            root / "capture",
            ["_GfxSetViewPort"],
            requested_timeout=600.0,
        )
        self.assertTrue(token)
        counter = json.loads((root / "live-launch.json").read_text())
        self.assertEqual(counter["used"], 1)

    def test_initialization_preserves_used_count(self):
        root = self._root(used=2)
        state.initialize_launch_counter(root, "test", 3, 120)
        counter = json.loads((root / "live-launch.json").read_text())
        self.assertEqual(counter["used"], 2)

    def test_capture_requires_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "capture"
            result = live_gfx.main(
                [
                    "capture",
                    "--target",
                    "_GfxSetViewPort",
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(result, live_gfx.EXIT_INCOMPLETE)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
