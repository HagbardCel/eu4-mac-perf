"""Portable cross-frame reproduction cases.

The fixture is a deterministic four-frame Metal sequence: frame 2 renders
texture 500, frame 3 leaves it unchanged, frame 4 samples it.  Frame 1 is an
unrelated successful frame that the minimal prefix must exclude.  No EU4
process and no original campaign directory are used after packaging.
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
BENCH = ROOT / "benchmark"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_frame_graph, gfx_replay_case, m6_frame, m7_continuous  # noqa: E402

WIDTH = HEIGHT = 4
SESSION = "synthetic-cross-frame"

VERT = """#include <metal_stdlib>
using namespace metal;
struct VOut { float4 position [[position]]; float2 uv; };
vertex VOut vmain(uint vid [[vertex_id]]) {
    float2 p[3] = { float2(-1,-1), float2(3,-1), float2(-1,3) };
    VOut o; o.position = float4(p[vid], 0.0, 1.0);
    o.uv = (p[vid] + 1.0) * 0.5; return o;
}
"""
FRAG_PATTERN = """#include <metal_stdlib>
using namespace metal;
struct FOut { float4 color [[color(0)]]; };
fragment FOut fpattern(float4 pos [[position]]) {
    int x = int(pos.x);
    int glrow = 3 - int(pos.y);
    float r = (x == 0 || x == 3) ? 1.0 : 0.0;
    float g = (glrow == 0 || glrow == 3) ? 1.0 : 0.0;
    float b = (x == glrow) ? 1.0 : 0.0;
    return FOut{float4(r, g, b, 1.0)};
}
"""
FRAG_SAMPLE = """#include <metal_stdlib>
using namespace metal;
struct VOut { float4 position [[position]]; float2 uv; };
struct FOut { float4 color [[color(0)]]; };
fragment FOut fsample(VOut in [[stage_in]], texture2d<float> tex0 [[texture(0)]],
                      sampler s0 [[sampler(0)]]) {
    return FOut{tex0.sample(s0, in.uv)};
}
"""
FRAG_BLACK = """#include <metal_stdlib>
using namespace metal;
struct FOut { float4 color [[color(0)]]; };
fragment FOut fblack() { return FOut{float4(0.0, 0.0, 0.0, 1.0)}; }
"""


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def byte(value: float) -> int:
    return int(round(value * 255.0))


def pattern_bgra(x: int, glrow: int) -> bytes:
    r = 1.0 if x in (0, 3) else 0.0
    g = 1.0 if glrow in (0, 3) else 0.0
    b = 1.0 if x == glrow else 0.0
    return bytes([byte(b), byte(g), byte(r), 255])


def expected_image() -> bytes:
    return b"".join(pattern_bgra(x, glrow)
                    for glrow in range(HEIGHT) for x in range(WIDTH))


def target(name=None):
    if name is None:
        return {"framebuffer": 0, "width": WIDTH, "height": HEIGHT}
    return {
        "framebuffer": 1, "width": WIDTH, "height": HEIGHT,
        "color_attachment": {
            "type": "texture", "name": name, "level": 0,
            "internal_format": "RGB8", "width": WIDTH, "height": HEIGHT,
        },
    }


def draw(program, target_value, textures=(), index=0, shaders=(), attributes=()):
    return {
        "kind": "draw", "index": index,
        "draw": {"entry": "_GfxDraw", "mode": 4, "count": 3,
                 "base_vertex": 0, "instance_count": 1},
        "state": {
            "viewport": [0, 0, WIDTH, HEIGHT],
            "depth": {"test": False, "write": False, "func": "0x0201"},
            "blend": {"enabled": False},
            "cull": {"enabled": False},
        },
        "program": {"name": program, "uniforms": [],
                    "shaders": list(shaders)},
        "attributes": list(attributes), "textures": list(textures),
        "target": target_value,
    }


def manifest(events, reference):
    return {
        "schema": "gfx-frame-trace/v1", "complete": True,
        "reason": "scene_present", "events": len(events),
        "frame": {"width": WIDTH, "height": HEIGHT, "origin": "lower_left",
                  "pixel_format": "BGRA8", "row_stride": WIDTH * 4,
                  "reference": "frame-reference.bgra",
                  "reference_bytes": len(reference),
                  "reference_sha256": sha(reference)},
        "seed": {"width": WIDTH, "height": HEIGHT},
    }


class SequenceFixture:
    def __init__(self, base: Path, host: Path):
        self.base = base
        self.host = host
        self.shaders = base / "shaders"
        self.shaders.mkdir(parents=True)
        (self.shaders / "v.metal").write_text(VERT)
        (self.shaders / "f_pattern.metal").write_text(FRAG_PATTERN)
        (self.shaders / "f_sample.metal").write_text(FRAG_SAMPLE)
        (self.shaders / "f_black.metal").write_text(FRAG_BLACK)
        stage = {
            "msl_file": "v.metal", "msl_entry": "vmain",
            "msl_uniform_buffer": None, "msl_samplers": [],
            "attributes": [], "uniforms": [],
        }

        def pixel(file, entry, samplers=()):
            return {
                "msl_file": file, "msl_entry": entry,
                "msl_uniform_buffer": None,
                "msl_samplers": list(samplers),
                "attributes": [], "uniforms": [],
            }

        programs = [
            {"program": 1, "status": "ok", "stages": {
                "vertex": stage,
                "pixel": pixel("f_pattern.metal", "fpattern")}},
            {"program": 2, "status": "ok", "stages": {
                "vertex": stage,
                "pixel": pixel("f_sample.metal", "fsample", [{
                    "kind": "texture2d", "name": "tex0",
                    "texture_index": 0, "sampler_index": 0}])}},
            {"program": 3, "status": "ok", "stages": {
                "vertex": stage,
                "pixel": pixel("f_black.metal", "fblack")}},
        ]
        (self.shaders / "shaders.json").write_text(
            json.dumps({"programs": programs}))
        self.frames: list[Path] = []

    def frame(self, index: int, events: list[dict], reference: bytes, *,
              previous: Path | None = None, render_targets=(),
              carried=()) -> Path:
        frame = self.base / "frames" / f"frame-{index:04d}"
        (frame / "blobs").mkdir(parents=True)
        (frame / "blobs" / "v.metal").write_bytes(b"vertex")
        (frame / "blobs" / "f_pattern.metal").write_bytes(b"pattern")
        (frame / "blobs" / "f_sample.metal").write_bytes(b"sample")
        (frame / "blobs" / "f_black.metal").write_bytes(b"black")
        (frame / "blobs" / "verts.bin").write_bytes(b"verts!")
        (frame / "blobs" / "tex500.bin").write_bytes(bytes(64))
        shader_stages = [
            {"stage": "vertex", "file": "v.metal",
             "sha256": sha(b"vertex")},
        ]
        attribute = {
            "location": 0, "name": "in_POSITION", "components": 2,
            "gl_type": "0x1406", "stride": 8, "data": "verts.bin",
            "sha256": sha(b"verts!"),
        }
        for event in events:
            fragment = {1: "f_pattern.metal", 2: "f_sample.metal",
                        3: "f_black.metal"}[event["program"]["name"]]
            event["program"]["shaders"] = shader_stages + [{
                "stage": "pixel", "file": fragment,
                "sha256": sha(Path(frame / "blobs" / fragment).read_bytes()),
            }]
            event["attributes"] = [attribute]
            for texture in event.get("textures") or []:
                if texture.get("name") == 500 and not texture.get("levels"):
                    texture["levels"] = [{
                        "level": 0, "width": WIDTH, "height": HEIGHT,
                        "pixel_format": "RGB8", "sha256": sha(bytes(64)),
                        "file": "tex500.bin",
                    }]
        (frame / "frame-events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n")
        (frame / "frame-reference.bgra").write_bytes(reference)
        (frame / "frame-trace.json").write_text(
            json.dumps(manifest(events, reference)))
        sequence = {
            "schema": "gfx-frame-sequence/v1", "session_id": SESSION,
            "pid": 1, "sequence_index": index, "campaign_frames": 4,
            "capture_frame_id": index, "complete": True,
            "boundary": "scene", "reason": "scene_present",
            "previous": None,
            "render_targets": list(render_targets),
            "carried_renderer_outputs": list(carried),
            "lifecycle": [],
            "reference_sha256": sha(reference),
            "manifest_sha256": sha((frame / "frame-trace.json").read_bytes()),
            "event_stream_sha256": sha(
                (frame / "frame-events.jsonl").read_bytes()),
            "operations": len(events), "draws": len(events),
            "clears": 0, "copies": 0,
        }
        if previous is not None:
            sequence["previous"] = {
                "capture_frame_id": index - 1,
                "dir": str(previous),
                "manifest_sha256": sha(
                    (previous / "frame-trace.json").read_bytes()),
                "event_stream_sha256": sha(
                    (previous / "frame-events.jsonl").read_bytes()),
            }
        (frame / "frame-sequence.json").write_text(json.dumps(sequence))
        (frame / "frame-seal.json").write_text(json.dumps({
            "schema": "gfx-frame-seal/v1", "sealed": True, "complete": True,
            "struct_size": 2256, "abi_version": 2,
            "trace_complete": True, "trace_integrity": True,
            "readback_ok": True, "event_stream_verified": True,
            "capture_frame_id": index, "pid": 1, "session_id": SESSION,
            "capture_dir": str(frame),
            "manifest_file": "frame-trace.json",
            "manifest_sha256": sequence["manifest_sha256"],
            "reference_file": "frame-reference.bgra",
            "reference_bytes": len(reference),
            "reference_sha256": sequence["reference_sha256"],
            "event_stream_file": "frame-events.jsonl",
            "event_stream_bytes": (frame / "frame-events.jsonl").stat().st_size,
            "event_stream_sha256": sequence["event_stream_sha256"],
            "operation_count": len(events), "draw_count": len(events),
            "clear_count": 0, "copy_count": 0,
        }))
        self.frames.append(frame)
        return frame

    def build(self):
        black = bytes([0, 0, 0, 255]) * (WIDTH * HEIGHT)
        self.frame(1, [draw(3, target())], black)
        self.frame(2, [draw(1, target("500")), draw(3, target())], black,
                   previous=self.frames[-1], render_targets=("500",))
        self.frame(3, [draw(3, target())], black,
                   previous=self.frames[-1], carried=("500",))
        sampled = {
            "name": 500, "sampler_name": "tex0", "unit": 0,
            "width": WIDTH, "height": HEIGHT, "internal_format": "RGB8",
            "levels": [],
        }
        self.frame(4, [draw(2, target(), textures=[sampled])],
                   expected_image(), previous=self.frames[-1],
                   carried=("500",))
        return self.frames


def canonical_fixture():
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


@unittest.skipUnless(sys.platform == "darwin", "Metal replay is macOS")
class AuthenticCanonicalPrefixTests(unittest.TestCase):
    """Relocate an ordered prefix built from the canonical authentic capture."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.source, cls.shaders = canonical_fixture()
        if cls.source is None:
            raise unittest.SkipTest("canonical frame fixture is not available")
        try:
            cls.host = m6_frame.build_replay_host()
        except Exception as exc:  # noqa: BLE001 - explicit skip
            raise unittest.SkipTest(str(exc))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def make_frame(self, name, index, previous, carried, render_targets,
                   remove=()):
        frame = self.base / "campaign" / name
        (frame / "blobs").mkdir(parents=True)
        for file in ("frame-trace.json", "frame-events.jsonl",
                     "frame-reference.bgra"):
            shutil.copy2(self.source / file, frame / file)
        for blob in (self.source / "blobs").iterdir():
            try:
                os.link(blob, frame / "blobs" / blob.name)
            except OSError:
                shutil.copy2(blob, frame / "blobs" / blob.name)
        if remove:
            lines = (frame / "frame-events.jsonl").read_text().splitlines()
            kept = [line for line_index, line in enumerate(lines)
                    if line_index not in remove]
            (frame / "frame-events.jsonl").write_text(
                "\n".join(kept) + "\n")
            manifest = json.loads((frame / "frame-trace.json").read_text())
            manifest["events"] = len(kept)
            (frame / "frame-trace.json").write_text(json.dumps(manifest))
        manifest = json.loads((frame / "frame-trace.json").read_text())
        sequence = {
            "schema": "gfx-frame-sequence/v1", "session_id": "canonical-prefix",
            "pid": 1, "sequence_index": index, "campaign_frames": 3,
            "capture_frame_id": index, "complete": True,
            "boundary": "scene", "reason": "scene_present",
            "previous": None,
            "render_targets": list(render_targets),
            "carried_renderer_outputs": list(carried),
            "lifecycle": [],
            "reference_sha256": sha((frame / "frame-reference.bgra").read_bytes()),
            "manifest_sha256": sha((frame / "frame-trace.json").read_bytes()),
            "event_stream_sha256": sha(
                (frame / "frame-events.jsonl").read_bytes()),
            "operations": manifest["events"], "draws": 0, "clears": 0,
            "copies": 0,
        }
        if previous is not None:
            sequence["previous"] = {
                "capture_frame_id": index - 1,
                "dir": str(previous),
                "manifest_sha256": sha(
                    (previous / "frame-trace.json").read_bytes()),
                "event_stream_sha256": sha(
                    (previous / "frame-events.jsonl").read_bytes()),
            }
        (frame / "frame-sequence.json").write_text(json.dumps(sequence))
        return frame

    def test_canonical_prefix_relocates_and_keeps_carried_metal_history(self):
        campaign = self.base / "campaign"
        frame1 = self.make_frame("frame-1", 1, None, (), ("443",))
        frame2 = self.make_frame("frame-2", 2, frame1, ("443",), (),
                                 remove=(288, 289))
        frame3 = self.make_frame("frame-3", 3, frame2, ("443",), (),
                                 remove=(288, 289))
        frames = [frame1, frame2, frame3]
        out = self.base / "out"
        report = m7_continuous.run_campaign(
            frames, out, shaders=self.shaders, stop_on_difference=False)
        self.assertEqual(report["status"], "complete", report)
        self.assertGreaterEqual(report["frames"][1]["carried_bindings"], 1)
        diagnosis = json.loads(
            (out / "out-003" / "diagnostic-report.json").read_text())
        case = self.base / "case"
        gfx_replay_case.package_sequence_diagnosis(
            frames, out / "out-003", case, diagnosis,
            shaders=self.shaders, case_id="canonical-prefix", max_bytes=0)
        moved = self.base / "relocated"
        shutil.copytree(case, moved)
        shutil.rmtree(campaign)
        shutil.rmtree(out)
        verified = gfx_replay_case.verify(moved)
        self.assertTrue(verified["ok"], verified["problems"])
        replay = gfx_replay_case.replay_case(moved, self.base / "replay")
        self.assertEqual(replay["campaign"]["target_sequence"], 3)
        self.assertEqual(replay["classification"],
                         diagnosis["classification"])
        self.assertGreaterEqual(
            replay["provenance"]["metal_render_target_bindings"], 1)


@unittest.skipUnless(sys.platform == "darwin", "Metal replay is macOS")
class PortableSequenceCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        try:
            cls.host = m6_frame.build_replay_host()
        except Exception as exc:  # noqa: BLE001 - explicit skip
            raise unittest.SkipTest(str(exc))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.fixture = SequenceFixture(self.base / self.id().split(".")[-1],
                                       self.host)
        self.frames = self.fixture.build()
        self.shaders = self.fixture.shaders

    def baseline(self):
        out = self.fixture.base / "baseline"
        report = m7_continuous.run_campaign(
            self.frames, out, shaders=self.shaders,
            stop_on_difference=False)
        self.assertEqual(report["status"], "complete", report)
        diagnosis = json.loads(
            (out / "out-004" / "diagnostic-report.json").read_text())
        return out, diagnosis

    def package(self, out, diagnosis, case=None):
        case = case or (self.fixture.base / "case")
        manifest = gfx_replay_case.package_sequence_diagnosis(
            self.frames, out / "out-004", case, diagnosis,
            shaders=self.shaders, case_id="cross-frame")
        return case, manifest

    def refresh_manifest_digest(self, case: Path, relative: str):
        manifest = json.loads((case / "case.json").read_text())
        path = case / relative
        for item in manifest["files"]:
            if item["path"] == relative:
                item["bytes"] = path.stat().st_size
                item["sha256"] = sha(path.read_bytes())
                break
        (case / "case.json").write_text(json.dumps(manifest, indent=2) + "\n")

    def test_minimal_prefix_is_relocatable_and_reproduces_the_fault(self):
        out, diagnosis = self.baseline()
        self.assertEqual(diagnosis["classification"], "qualified_match",
                         diagnosis)
        case, manifest = self.package(out, diagnosis)
        sequence = json.loads((case / "sequence.json").read_text())
        self.assertEqual(sequence["target_index"], 3, sequence)
        self.assertTrue(sequence["minimal_prefix"], sequence)
        self.assertEqual(sequence["excluded_frames"], 1, sequence)
        self.assertEqual([entry["original_sequence_index"]
                          for entry in sequence["frames"]], [2, 3, 4])
        prefix_bytes = sum(
            path.stat().st_size for path in (case / "frames").rglob("*")
            if path.is_file())
        self.assertLess(sequence["source_prefix_bytes"],
                        sequence["full_source_bytes"])
        self.assertGreater(sequence["excluded_source_bytes"], 0)
        self.assertLess(prefix_bytes, sequence["full_source_bytes"])
        moved = self.fixture.base / "relocated"
        shutil.copytree(case, moved)
        shutil.rmtree(self.fixture.base / "frames")
        shutil.rmtree(out)
        verified = gfx_replay_case.verify(moved)
        self.assertTrue(verified["ok"], verified["problems"])
        self.assertEqual(verified["sequence"]["frames"], 3)
        replay_out = self.fixture.base / "replay-ok"
        replay = gfx_replay_case.replay_case(moved, replay_out)
        self.assertEqual(replay["final"]["status"], "qualified_match",
                         replay)
        self.assertEqual(replay["campaign"]["target_sequence"], 3)
        fault_out = self.fixture.base / "replay-fault"
        fault = gfx_replay_case.replay_case(
            moved, fault_out,
            faults={"disable_render_target_vflip": True})
        self.assertNotEqual(fault["final"]["status"], "qualified_match",
                            fault)
        self.assertGreater(fault["pixels"]["statistics"]["mismatched_pixels"],
                           0)
        self.assertEqual(fault["campaign"]["target_sequence"], 3)
        self.assertEqual(
            fault["reproduction"]["earliest_affected_frame"], 3)
        self.assertEqual(
            fault["reproduction"]["earliest_affected_operation"], "draw")

    def test_verifier_detects_missing_predecessor(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        sequence = json.loads((case / "sequence.json").read_text())
        sequence["frames"] = sequence["frames"][1:]
        for index, entry in enumerate(sequence["frames"], start=1):
            entry["index"] = index
            entry["dir"] = f"frames/{entry['dir'].split('/')[-1]}"
        sequence["target_index"] = len(sequence["frames"])
        (case / "sequence.json").write_text(json.dumps(sequence))
        self.refresh_manifest_digest(case, "sequence.json")
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertTrue(any("predecessor" in problem
                            for problem in verified["problems"]), verified)

    def test_verifier_detects_wrong_session_and_missing_lifecycle(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        frame = case / "frames" / "000002" / "frame-sequence.json"
        document = json.loads(frame.read_text())
        document["session_id"] = "another-session"
        document.pop("lifecycle")
        frame.write_text(json.dumps(document))
        self.refresh_manifest_digest(case, "frames/000002/frame-sequence.json")
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        joined = "\n".join(verified["problems"])
        self.assertIn("another session", joined)
        self.assertIn("lifecycle mutation record", joined)

    def test_verifier_detects_reordered_and_stale_checkpoint(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        sequence = json.loads((case / "sequence.json").read_text())
        sequence["frames"][0], sequence["frames"][1] = (
            sequence["frames"][1], sequence["frames"][0])
        (case / "sequence.json").write_text(json.dumps(sequence))
        self.refresh_manifest_digest(case, "sequence.json")
        verified = gfx_replay_case.verify(case)
        self.assertTrue(any("reordered" in problem
                            for problem in verified["problems"]), verified)
        checkpoint = case / "checkpoints" / "frame-002" / (
            "checkpoint-request.json")
        checkpoint.write_text(json.dumps({"checkpoints": []}))
        self.refresh_manifest_digest(
            case, "checkpoints/frame-002/checkpoint-request.json")
        verified = gfx_replay_case.verify(case)
        self.assertTrue(any("stale or substituted" in problem
                            for problem in verified["problems"]), verified)

    def test_verifier_detects_corrupt_payload_and_wrong_generation(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        blob = next((case / "frames" / "000002" / "blobs").iterdir())
        original = blob.read_bytes()
        tampered = bytearray(original)
        tampered[0] ^= 0xFF
        blob.write_bytes(bytes(tampered))
        verified = gfx_replay_case.verify(case)
        self.assertTrue(any("digest drift" in problem
                            for problem in verified["problems"]), verified)
        blob.write_bytes(original)
        frame = case / "frames" / "000001" / "frame-sequence.json"
        document = json.loads(frame.read_text())
        document["render_targets"] = []
        frame.write_text(json.dumps(document))
        self.refresh_manifest_digest(
            case, "frames/000001/frame-sequence.json")
        verified = gfx_replay_case.verify(case)
        self.assertTrue(any("no included predecessor" in problem
                            for problem in verified["problems"]), verified)

    def test_supervisor_packages_the_prefix_when_it_stops(self):
        target = self.frames[-1]
        wrong = bytes([0, 255, 0, 255]) * (WIDTH * HEIGHT)
        (target / "frame-reference.bgra").write_bytes(wrong)
        manifest = json.loads((target / "frame-trace.json").read_text())
        manifest["frame"]["reference_sha256"] = sha(wrong)
        (target / "frame-trace.json").write_text(json.dumps(manifest))
        sequence = json.loads((target / "frame-sequence.json").read_text())
        sequence["manifest_sha256"] = sha(
            (target / "frame-trace.json").read_bytes())
        sequence["reference_sha256"] = sha(wrong)
        (target / "frame-sequence.json").write_text(json.dumps(sequence))
        seal = json.loads((target / "frame-seal.json").read_text())
        seal["manifest_sha256"] = sequence["manifest_sha256"]
        seal["reference_sha256"] = sha(wrong)
        (target / "frame-seal.json").write_text(json.dumps(seal))
        out = self.fixture.base / "supervisor"
        report = m7_continuous.run_campaign(
            self.frames, out, shaders=self.shaders, stop_on_difference=True)
        self.assertEqual(report["status"], "stopped", report)
        entry = report["frames"][-1]
        self.assertEqual(entry["sequence"], 4)
        self.assertTrue(entry.get("case"), entry)
        case = out / "case"
        manifest = json.loads((case / "case.json").read_text())
        self.assertEqual(manifest["schema"], gfx_replay_case.SEQUENCE_SCHEMA)
        sequence = json.loads((case / "sequence.json").read_text())
        self.assertEqual(sequence["target_index"], 3)
        self.assertEqual(sequence["excluded_frames"], 1)

    def test_artifact_budget_marks_the_reproduction_incomplete(self):
        out, diagnosis = self.baseline()
        case = self.fixture.base / "budget-case"
        threshold = 1
        manifest = gfx_replay_case.package_sequence_diagnosis(
            self.frames, out / "out-004", case, diagnosis,
            shaders=self.shaders, case_id="budget", max_bytes=threshold)
        self.assertIn(manifest["schema"],
                      (gfx_replay_case.SCHEMA,
                       gfx_replay_case.SEQUENCE_SCHEMA))
        self.assertFalse(manifest["reproduction"]["independently_reproducible"])
        self.assertEqual(manifest["reproduction"]["status"], "incomplete")
        self.assertEqual(
            manifest["reproduction"]["missing"][0]["kind"], "artifact_budget")
        self.assertTrue((case / "reproduction-incomplete.json").is_file())
        with self.assertRaises(gfx_replay_case.CaseError) as caught:
            gfx_replay_case.replay_case(case, self.fixture.base / "budget-out")
        self.assertEqual(caught.exception.kind, "verification")


if __name__ == "__main__":
    unittest.main()
