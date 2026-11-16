"""M7 PR4 persistent Metal resource history across successive frames.

The tests replay two real captured frames through one Metal session and assert
that a texture rendered in frame N is sampled from Metal in frame N+1, never
from the captured OpenGL image.  They use the canonical title fixture (or any
local fixture with the same layout) and skip on the portable Linux CI.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import m7_continuous  # noqa: E402
BENCH = ROOT / "benchmark"
HOST = ROOT / "tests" / "gfx_metal_replay_abi_host.mm"
CORE = BENCH / "eu4_gfx_frame_replay.mm"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture():
    candidates = [ROOT, ROOT.parent / "eu4-mac-perf", ROOT.parent / "wt_infra"]
    candidates.extend(Path(item) for item in
                      os.environ.get("EU4_EVIDENCE_ROOT", "").split(os.pathsep)
                      if item)
    for root in candidates:
        base = root / ".local" / "m6" / "frames" / "menu-occ300"
        frame = base / "frame"
        shaders = base / "translated"
        if (frame / "frame-trace.json").is_file() and (
                shaders / "shaders.json").is_file():
            return frame, shaders
    return None, None


@unittest.skipUnless(sys.platform == "darwin",
                     "the Metal replay core runs on macOS")
class ContinuousReplayHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.source, cls.shaders = _fixture()
        if cls.source is None:
            raise unittest.SkipTest("canonical frame fixture is not available")
        cls.host = cls.base / "gfx_metal_replay_abi"
        built = subprocess.run(
            ["clang", "-arch", "arm64", "-O2", "-fobjc-arc", "-lc++",
             "-I", str(BENCH), "-framework", "Foundation",
             "-framework", "Metal", "-o", str(cls.host),
             str(HOST), str(CORE)],
            capture_output=True, text=True, check=False, timeout=300)
        if built.returncode:
            raise unittest.SkipTest(built.stderr.strip() or "replay build failed")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def make_frame(self, name: str, *, remove_events=()) -> Path:
        frame = self.base / name
        if frame.exists():
            shutil.rmtree(frame)
        (frame / "blobs").mkdir(parents=True)
        for file in ("frame-trace.json", "frame-events.jsonl",
                     "frame-reference.bgra"):
            shutil.copy2(self.source / file, frame / file)
        for blob in (self.source / "blobs").iterdir():
            os.link(blob, frame / "blobs" / blob.name)
        if remove_events:
            lines = (frame / "frame-events.jsonl").read_text().splitlines()
            kept = [line for index, line in enumerate(lines)
                    if index not in remove_events]
            (frame / "frame-events.jsonl").write_text(
                "\n".join(kept) + "\n")
            manifest = json.loads((frame / "frame-trace.json").read_text())
            manifest["events"] = len(kept)
            (frame / "frame-trace.json").write_text(json.dumps(manifest))
        return frame

    def write_sequence(self, frame: Path, index: int, *,
                       previous: Path | None = None,
                       carried=(), lifecycle=()):
        manifest = json.loads((frame / "frame-trace.json").read_text())
        value = {
            "schema": "gfx-frame-sequence/v1",
            "session_id": "synthetic-sequence",
            "pid": 1,
            "sequence_index": index,
            "campaign_frames": 2,
            "capture_frame_id": index,
            "complete": True,
            "boundary": "scene",
            "reason": "scene_present",
            "previous": None,
            "render_targets": [],
            "carried_renderer_outputs": list(carried),
            "lifecycle": list(lifecycle),
            "reference_sha256": _sha(frame / "frame-reference.bgra"),
            "manifest_sha256": _sha(frame / "frame-trace.json"),
            "event_stream_sha256": _sha(frame / "frame-events.jsonl"),
            "operations": manifest["events"],
            "draws": 0,
            "clears": 0,
            "copies": 0,
        }
        if previous is not None:
            value["previous"] = {
                "capture_frame_id": index - 1,
                "dir": str(previous),
                "manifest_sha256": _sha(previous / "frame-trace.json"),
                "event_stream_sha256": _sha(
                    previous / "frame-events.jsonl"),
            }
        (frame / "frame-sequence.json").write_text(json.dumps(value))
        return value

    def run_sequence(self, frames):
        out = self.base / "out"
        if out.exists():
            shutil.rmtree(out)
        ran = subprocess.run(
            [str(self.host), "--sequence", ",".join(map(str, frames)),
             "--shaders", str(self.shaders), "--out", str(out)],
            capture_output=True, text=True, check=False, timeout=300)
        rows = []
        for line in ran.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                rows.append(json.loads(line))
        return ran, rows, out

    def assert_ok(self, row):
        self.assertTrue(row.get("ok"), row)
        self.assertEqual(row.get("history_status"), 1, row)

    def test_frame_n_output_is_sampled_from_metal_in_frame_n_plus_one(self):
        frame1 = self.make_frame("carry-1")
        frame2 = self.make_frame("carry-2", remove_events=(288, 289))
        self.write_sequence(frame1, 1)
        self.write_sequence(frame2, 2, previous=frame1, carried=("443",))
        ran, rows, out = self.run_sequence([frame1, frame2])
        self.assertEqual(ran.returncode, 0, ran.stderr[-1000:])
        self.assertEqual(len(rows), 2, rows)
        self.assert_ok(rows[0])
        self.assert_ok(rows[1])
        self.assertEqual(rows[0]["carried_bindings"], 0)
        self.assertGreaterEqual(rows[1]["carried_bindings"], 1)
        provenance = [
            json.loads(line)
            for line in (out / "out-002" / "replay-provenance.jsonl"
                         ).read_text().splitlines()
            if line.strip()
        ]
        carried = [row for row in provenance
                   if row.get("texture") == "443" and row.get("carried")]
        self.assertTrue(carried, provenance)
        self.assertEqual(carried[0]["source"], "metal_render_target")
        self.assertEqual(carried[0]["history_frame"], 1)
        history = json.loads(
            (out / "out-002" / "replay.json").read_text())["history"]
        self.assertEqual(history["status"], "ok")
        self.assertEqual(history["sequence"], 2)
        resource = next(item for item in history["resources"]
                        if item["name"] == "443")
        self.assertEqual(resource["kind"], "renderer_generated")
        self.assertEqual(resource["last_frame"], 1)

    def test_campaign_processes_ordered_frames_with_one_session(self):
        frame1 = self.make_frame("campaign-1")
        frame2 = self.make_frame("campaign-2", remove_events=(288, 289))
        self.write_sequence(frame1, 1)
        self.write_sequence(frame2, 2, previous=frame1, carried=("443",))
        out = self.base / "campaign"
        report = m7_continuous.run_campaign(
            [frame1, frame2], out, shaders=self.shaders,
            stop_on_difference=False)
        self.assertEqual(report["status"], "complete", report)
        self.assertEqual(len(report["frames"]), 2)
        self.assertEqual(report["frames"][1]["sequence"], 2)
        self.assertGreaterEqual(report["frames"][1]["carried_bindings"], 1)
        self.assertEqual(report["frames"][1]["history_status"], 1)
        self.assertFalse(report["qualified"])
        self.assertEqual(report["metrics"]["frames"], 2)
        self.assertGreater(report["metrics"]["replay_seconds"]["worst"], 0)
        self.assertGreater(report["metrics"]["bytes_written"], 0)
        self.assertTrue((out / "campaign-report.json").is_file())
        self.assertTrue((out / "metrics.json").is_file())

    def test_missing_previous_producer_fails_closed(self):
        frame1 = self.make_frame("missing-1", remove_events=(288, 289))
        frame2 = self.make_frame("missing-2", remove_events=(288, 289))
        self.write_sequence(frame1, 1)
        self.write_sequence(frame2, 2, previous=frame1, carried=("443",))
        ran, rows, _ = self.run_sequence([frame1, frame2])
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(len(rows), 2, rows)
        self.assert_ok(rows[0])
        self.assertFalse(rows[1]["ok"])
        self.assertEqual(rows[1]["history_status"], 2)
        self.assertEqual(rows[1]["missing_history"], 1)
        self.assertIn("no Metal producer exists", rows[1]["error"])
        self.assertNotIn("Segmentation", ran.stderr)

    def test_unrecorded_renderer_output_is_not_imported_as_opengl(self):
        frame1 = self.make_frame("unrecorded-1")
        frame2 = self.make_frame("unrecorded-2", remove_events=(288, 289))
        self.write_sequence(frame1, 1)
        # Frame 1 generated 443, but frame 2 neither reproduces it nor declares
        # it carried: importing the captured OpenGL image would hide the gap.
        self.write_sequence(frame2, 2, previous=frame1)
        ran, rows, _ = self.run_sequence([frame1, frame2])
        self.assertNotEqual(ran.returncode, 0)
        self.assertFalse(rows[1]["ok"])
        self.assertEqual(rows[1]["history_status"], 2)
        self.assertIn("is not carried by this frame", rows[1]["error"])

    def test_explicit_lifecycle_reset_allows_a_cpu_origin_update(self):
        frame1 = self.make_frame("reset-1")
        frame2 = self.make_frame("reset-2", remove_events=(288, 289))
        self.write_sequence(frame1, 1)
        self.write_sequence(
            frame2, 2, previous=frame1,
            lifecycle=({"kind": "delete", "names": ("443",)},))
        ran, rows, out = self.run_sequence([frame1, frame2])
        self.assertEqual(ran.returncode, 0, ran.stderr[-1000:])
        self.assert_ok(rows[1])
        provenance = [
            json.loads(line)
            for line in (out / "out-002" / "replay-provenance.jsonl"
                         ).read_text().splitlines()
            if line.strip()
        ]
        row = next(item for item in provenance if item.get("texture") == "443")
        self.assertEqual(row["source"], "external_capture")
        history = json.loads(
            (out / "out-002" / "replay.json").read_text())["history"]
        resource = next(item for item in history["resources"]
                        if item["name"] == "443")
        self.assertEqual(resource["kind"], "external")

    def test_previous_frame_identity_mismatch_does_not_execute(self):
        frame1 = self.make_frame("identity-1")
        frame2 = self.make_frame("identity-2")
        self.write_sequence(frame1, 1)
        sequence = self.write_sequence(frame2, 2, previous=frame1)
        sequence["previous"]["manifest_sha256"] = "0" * 64
        (frame2 / "frame-sequence.json").write_text(json.dumps(sequence))
        ran, rows, _ = self.run_sequence([frame1, frame2])
        self.assertNotEqual(ran.returncode, 0)
        self.assertFalse(rows[1]["ok"])
        self.assertEqual(rows[1]["stage"], "history_sequence")
        self.assertIn("previous-frame manifest_sha256", rows[1]["error"])
        self.assertEqual(rows[1]["encoded"], 0)


if __name__ == "__main__":
    unittest.main()
