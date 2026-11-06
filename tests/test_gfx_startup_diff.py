#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys_path = str(ROOT / "benchmark")
import sys

sys.path.insert(0, sys_path)

import eu4_gfx_startup_diff as diff  # noqa: E402


PINNED = diff.PINNED_SHA


def write_capture(directory: Path, events, summary=None, metadata=None, status_pid=41350):
    events_text = "".join(json.dumps(e) + "\n" for e in events)
    (directory / "events.jsonl").write_text(events_text)
    summary = summary or {
        "schema": "gfx-startup/v0",
        "complete": True,
        "state": 2,
        "settings": "observed",
        "qualifying_init": 1,
        "settings_calls": 1,
        "init_calls": 1,
        "event_count": len(events),
        "overflow": 0,
        "unmatched": 0,
        "refused": 0,
        "unavailable": 0,
        "in_flight": 0,
        "events_copied": True,
        "reason": "",
    }
    (directory / "summary.json").write_text(json.dumps(summary) + "\n")
    metadata = metadata or {
        "schema": "gfx-startup/v0",
        "recorder_version": "1",
        "session": "test",
        "pid": status_pid,
        "executable_sha256": PINNED,
        "image_base": "0x100000000",
        "hook_rvas": {"settings": "0x15ed388", "init": "0x15e8ea0"},
    }
    (directory / "metadata.json").write_text(json.dumps(metadata) + "\n")
    (directory / "startup.status").write_text(
        "\n".join(
            [
                "EU4-GFX-STARTUP v1",
                "state=installed",
                "session=test",
                f"pid={status_pid}",
                "executable=/tmp/eu4",
                "arch=x86_64",
                "image_base=0x100000000",
                "settings_site=0x1015ed388",
                "init_site=0x1015e8ea0",
                f"module={PINNED}",
                "error=",
                "END",
                "",
            ]
        )
    )
    return {name: diff.sha256_file(directory / name) for name in diff.EXPECTED_HASHES}


def base_events():
    window_hex = (
        "02000000" + "00000000" + "c0060000" + "5d040000" + "3c000000" + "00000000"
        + "00000000" + "00000000"
    )
    samples = [2, 4, 8]
    flags = [1, 1, 1]
    return [
        {
            "schema": "gfx-startup/v0",
            "seq": 1,
            "call_id": 1,
            "site": "settings",
            "phase": "entry",
            "fields_ok": True,
            "display": -1,
            "windowed": 1,
            "sample_count": 3,
            "sample_captured": 3,
            "sample_values": samples,
            "sample_flags": [0, 0, 0],
            "byte_10": 0,
            "anisotropy": 0,
            "span": 0,
            "constant": 0,
            "return": 0,
        },
        {
            "schema": "gfx-startup/v0",
            "seq": 2,
            "call_id": 1,
            "site": "settings",
            "phase": "exit",
            "fields_ok": True,
            "display": 0,
            "windowed": 0,
            "sample_count": 3,
            "sample_captured": 3,
            "sample_values": samples,
            "sample_flags": flags,
            "byte_10": 1,
            "anisotropy": 16,
            "span": 3298534884096,
            "constant": 1024,
            "return": -1239745597008707471,
        },
        {
            "schema": "gfx-startup/v0",
            "seq": 3,
            "call_id": 2,
            "site": "init",
            "phase": "entry",
            "fields_ok": True,
            "window_ok": True,
            "window_hex": window_hex,
            "success": 0,
            "master_non_null": False,
            "primitive": 0,
            "width": 0,
            "height": 0,
            "source": 0,
            "dirty": 0,
            "lod": 0,
            "max_texture": 0,
            "blend_present": False,
            "depth_present": False,
            "raster_present": False,
            "gl_context_present": False,
            "viewport": [0, 0, 0, 0, 0, 0],
        },
        {
            "schema": "gfx-startup/v0",
            "seq": 4,
            "call_id": 2,
            "site": "init",
            "phase": "exit",
            "fields_ok": True,
            "success": 1,
            "master_non_null": True,
            "primitive": 4,
            "width": 1728,
            "height": 1117,
            "source": 1,
            "dirty": 0,
            "lod": 1,
            "max_texture": 16384,
            "blend_present": True,
            "depth_present": True,
            "raster_present": True,
            "gl_context_present": True,
            "viewport": [0, 0, 0, 0, 0, 0],
        },
    ]


def ok_responses(reference):
    samples = reference["settings"]["input"]["sample_values"]
    supports = [True] * len(samples)
    flags = diff.flags_for(samples, supports)
    s_out = reference["settings"]["output"]
    i_out = reference["init"]["output"]
    return {
        "settings": {
            "schema": "metal-shadow/v0",
            "seq": 1,
            "op": "settings",
            "ok": True,
            "result": {
                "return": 0,
                "flags": flags,
                "byte_10": s_out["byte_10"],
                "anisotropy": 8,
                "span": s_out["span"],
                "constant": s_out["constant"],
                "device_supports": supports,
            },
            "state": {"initialized": False},
        },
        "configure_window": {
            "schema": "metal-shadow/v0",
            "seq": 2,
            "op": "configure_window",
            "ok": True,
            "result": {
                "width": i_out["width"],
                "height": i_out["height"],
                "window_id": reference["init"]["input"]["window_id"],
                "source": reference["init"]["input"]["source"],
            },
            "state": {"initialized": False},
        },
        "init": {
            "schema": "metal-shadow/v0",
            "seq": 3,
            "op": "init",
            "ok": True,
            "result": {"success": 1},
            "state": {
                "initialized": True,
                "backend_present": True,
                "width": i_out["width"],
                "height": i_out["height"],
                "source": i_out["source"],
                "primitive": i_out["primitive"],
                "viewport": i_out["viewport"],
                "dirty": i_out["dirty"],
                "texture_lod": 0,
                "max_texture": 8192,
                "blend_snap": "aa" * 36,
                "depth_snap": "bb" * 36,
                "raster_snap": "cc" * 16,
            },
        },
        "shutdown": {
            "schema": "metal-shadow/v0",
            "seq": 4,
            "op": "shutdown",
            "ok": True,
            "result": {},
            "state": {"initialized": False},
        },
    }


class NormalizeTests(unittest.TestCase):
    def test_normalize_preserves_source_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            hashes = write_capture(directory, base_events())
            ref = diff.normalize_capture(directory, expected_hashes=hashes)
            self.assertEqual(ref["schema"], diff.REF_SCHEMA)
            self.assertEqual(ref["init"]["input"]["source"], 0)
            self.assertEqual(ref["init"]["input"]["window_id"], 2)
            self.assertEqual(ref["init"]["output"]["source"], 1)
            self.assertEqual(ref["settings"]["output"]["return"], -1239745597008707471)
            again = diff.normalize_capture(directory, expected_hashes=hashes)
            self.assertEqual(diff.dumps(ref), diff.dumps(again))

    def test_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_capture(directory, base_events())
            with self.assertRaises(diff.DiffError):
                diff.normalize_capture(directory, expected_hashes=diff.EXPECTED_HASHES)

    def test_fields_ok_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            events = base_events()
            events[0]["fields_ok"] = False
            hashes = write_capture(directory, events)
            with self.assertRaises(diff.DiffError):
                diff.normalize_capture(directory, expected_hashes=hashes)

    def test_sample_values_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            events = base_events()
            events[1]["sample_values"] = [2, 4, 9]
            hashes = write_capture(directory, events)
            with self.assertRaisesRegex(diff.DiffError, "sample_values"):
                diff.normalize_capture(directory, expected_hashes=hashes)

    def test_unsupported_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            events = base_events()
            events[0], events[2] = events[2], events[0]
            events[1], events[3] = events[3], events[1]
            for idx, event in enumerate(events):
                event["seq"] = idx + 1
            hashes = write_capture(directory, events)
            with self.assertRaisesRegex(diff.DiffError, "unsupported_sequence"):
                diff.normalize_capture(directory, expected_hashes=hashes)

    def test_call_id_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            events = base_events()
            events[1]["call_id"] = 99
            hashes = write_capture(directory, events)
            with self.assertRaisesRegex(diff.DiffError, "call_id"):
                diff.normalize_capture(directory, expected_hashes=hashes)

    def test_incomplete_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            events = base_events()
            summary = {
                "complete": False,
                "state": 2,
                "overflow": 0,
                "unmatched": 0,
                "unavailable": 0,
                "in_flight": 0,
                "events_copied": True,
            }
            hashes = write_capture(directory, events, summary=summary)
            with self.assertRaises(diff.DiffError):
                diff.normalize_capture(directory, expected_hashes=hashes)


class CompareTests(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            hashes = write_capture(directory, base_events())
            self.ref = diff.normalize_capture(directory, expected_hashes=hashes)

    def test_contract_pass_with_capability_rows(self):
        comparison = diff.compare(self.ref, ok_responses(self.ref))
        self.assertTrue(comparison["contract_pass"])
        by_field = {row["field"]: row for row in comparison["fields"]}
        self.assertEqual(by_field["settings.return"]["classification"], "abi_unresolved")
        self.assertEqual(
            by_field["settings.anisotropy"]["classification"], "capability_difference"
        )
        self.assertEqual(
            by_field["init.texture_lod"]["classification"], "capability_difference"
        )
        self.assertEqual(by_field["settings.msaa_algorithm"]["classification"], "matched")
        self.assertTrue(all(row["algorithm"] == "matched" for row in comparison["msaa"]))
        again = diff.compare(self.ref, ok_responses(self.ref))
        self.assertEqual(diff.dumps(comparison), diff.dumps(again))

    def test_strict_width_diverge(self):
        responses = ok_responses(self.ref)
        responses["init"]["state"]["width"] = 1
        comparison = diff.compare(self.ref, responses)
        self.assertFalse(comparison["contract_pass"])
        row = next(r for r in comparison["fields"] if r["field"] == "init.width")
        self.assertEqual(row["classification"], "diverged")
        self.assertIn("conditioned_on_reference", row["note"])

    def test_msaa_algorithm_diverge(self):
        responses = ok_responses(self.ref)
        responses["settings"]["result"]["flags"] = [0] * len(
            self.ref["settings"]["input"]["sample_values"]
        )
        comparison = diff.compare(self.ref, responses)
        self.assertFalse(comparison["contract_pass"])
        row = next(
            r for r in comparison["fields"] if r["field"] == "settings.msaa_algorithm"
        )
        self.assertEqual(row["classification"], "diverged")

    def test_gl_vs_metal_capability(self):
        responses = ok_responses(self.ref)
        # Keep algorithm correct but flip relative to GL by changing GL reference…
        # Instead alter Metal flags and supports together so algorithm still matches
        # but differs from GL: use supports that yield different flags than GL.
        samples = self.ref["settings"]["input"]["sample_values"]
        supports = [False] * len(samples)
        responses["settings"]["result"]["device_supports"] = supports
        responses["settings"]["result"]["flags"] = diff.flags_for(samples, supports)
        comparison = diff.compare(self.ref, responses)
        self.assertTrue(comparison["contract_pass"])
        self.assertTrue(
            any(row["gl_vs_metal"] == "capability_difference" for row in comparison["msaa"])
        )

    def test_envelope_failure(self):
        responses = ok_responses(self.ref)
        responses["init"] = {
            "schema": "metal-shadow/v0",
            "seq": 3,
            "op": "init",
            "ok": False,
            "error": "init_failed",
        }
        comparison = diff.compare(self.ref, responses)
        self.assertFalse(comparison["contract_pass"])
        row = next(r for r in comparison["fields"] if r["field"] == "init_envelope")
        self.assertEqual(row["classification"], "unavailable")

    def test_report_retained_shape(self):
        comparison = diff.compare(self.ref, ok_responses(self.ref))
        text = diff.render_report(comparison, self.ref)
        self.assertIn("conditioned_on_reference", text)
        self.assertIn("abi_unresolved", text)


class CommittedReferenceTests(unittest.TestCase):
    def test_committed_reference_loads(self):
        path = ROOT / "analysis" / "gfx-startup" / "v0" / "reference.json"
        reference = json.loads(path.read_text())
        self.assertEqual(reference["schema"], diff.REF_SCHEMA)
        self.assertEqual(reference["init"]["input"]["source"], 0)
        self.assertEqual(reference["source_hashes"], diff.EXPECTED_HASHES)


if __name__ == "__main__":
    unittest.main()
