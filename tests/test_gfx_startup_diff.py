#!/usr/bin/env python3
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_gfx_startup_diff as diff  # noqa: E402

PINNED = diff.PINNED_SHA


def write_capture(directory: Path, events, summary=None, metadata=None, status_pid=41350):
    (directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
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
                f"session={metadata['session']}",
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
    i_in = reference["init"]["input"]
    i_out = reference["init"]["output"]
    return [
        {
            "schema": "metal-shadow/v0",
            "seq": 1,
            "next": 2,
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
        {
            "schema": "metal-shadow/v0",
            "seq": 2,
            "next": 3,
            "op": "configure_window",
            "ok": True,
            "result": {
                "width": i_out["width"],
                "height": i_out["height"],
                "window_id": i_in["window_id"],
                "source": i_in["source"],
            },
            "state": {"initialized": False},
        },
        {
            "schema": "metal-shadow/v0",
            "seq": 3,
            "next": 4,
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
        {
            "schema": "metal-shadow/v0",
            "seq": 4,
            "next": 5,
            "op": "shutdown",
            "ok": True,
            "result": {},
            "state": {"initialized": False},
        },
    ]


def make_ref():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        hashes = write_capture(directory, base_events())
        return diff.normalize_capture(directory, expected_hashes=hashes)


class NormalizeTests(unittest.TestCase):
    def test_normalize_preserves_source_zero(self):
        ref = make_ref()
        self.assertEqual(ref["init"]["input"]["source"], 0)
        self.assertEqual(ref["init"]["input"]["window_id"], 2)
        ref["source_hashes"] = dict(diff.EXPECTED_HASHES)
        diff.validate_reference(ref)

    def test_extra_events_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            events = base_events() + [dict(base_events()[0], seq=5, call_id=3)]
            hashes = write_capture(
                directory,
                events,
                summary={
                    "complete": True,
                    "state": 2,
                    "settings_calls": 2,
                    "init_calls": 1,
                    "event_count": 5,
                    "overflow": 0,
                    "unmatched": 0,
                    "unavailable": 0,
                    "in_flight": 0,
                    "events_copied": True,
                },
            )
            with self.assertRaisesRegex(diff.DiffError, "unsupported_sequence"):
                diff.normalize_capture(directory, expected_hashes=hashes)

    def test_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_capture(directory, base_events())
            with self.assertRaises(diff.DiffError):
                diff.normalize_capture(directory, expected_hashes=diff.EXPECTED_HASHES)


class ReferenceValidationTests(unittest.TestCase):
    def test_mutated_sha_raises(self):
        ref = make_ref()
        # Make provenance look committed so only SHA fails.
        ref["source_hashes"] = dict(diff.EXPECTED_HASHES)
        ref["executable_sha256"] = "0" * 64
        with self.assertRaises(diff.DiffError):
            diff.validate_reference(ref)

    def test_mutated_source_hash_raises(self):
        ref = make_ref()
        ref["source_hashes"] = dict(diff.EXPECTED_HASHES)
        ref["source_hashes"]["events.jsonl"] = "0" * 64
        with self.assertRaises(diff.DiffError):
            diff.validate_reference(ref)

    def test_bool_as_int_raises(self):
        ref = make_ref()
        ref["source_hashes"] = dict(diff.EXPECTED_HASHES)
        ref["init"]["output"]["width"] = True
        with self.assertRaises(diff.DiffError):
            diff.validate_reference(ref)

    def test_compare_uses_committed_hashes_on_synthetic_ref(self):
        # Synthetic normalize refs use local hashes; validate_reference requires
        # EXPECTED_HASHES. Promote a synthetic ref for compare tests.
        ref = make_ref()
        ref["source_hashes"] = dict(diff.EXPECTED_HASHES)
        diff.validate_reference(ref)


def committed_like_ref():
    ref = make_ref()
    ref["source_hashes"] = dict(diff.EXPECTED_HASHES)
    return ref


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.ref = committed_like_ref()

    def test_contract_pass(self):
        comparison = diff.compare(self.ref, ok_responses(self.ref))
        self.assertTrue(comparison["contract_pass"])
        self.assertEqual(comparison["stream_outcome"], "complete")
        by_field = {row["field"]: row for row in comparison["fields"]}
        self.assertEqual(by_field["settings.return"]["classification"], "abi_unresolved")
        self.assertEqual(
            by_field["init.texture_lod"]["classification"], "capability_difference"
        )
        self.assertEqual(by_field["configure_window.source"]["metal"], 0)

    def test_bool_metal_byte10_fails(self):
        responses = ok_responses(self.ref)
        responses[0]["result"]["byte_10"] = True
        comparison = diff.compare(self.ref, responses)
        self.assertFalse(comparison["contract_pass"])
        row = next(r for r in comparison["fields"] if r["field"] == "settings.byte_10")
        self.assertEqual(row["classification"], "unavailable")

    def test_bool_success_fails(self):
        responses = ok_responses(self.ref)
        responses[2]["result"]["success"] = True
        comparison = diff.compare(self.ref, responses)
        self.assertFalse(comparison["contract_pass"])
        row = next(r for r in comparison["fields"] if r["field"] == "init.success")
        self.assertEqual(row["classification"], "unavailable")

    def test_reordered_responses(self):
        responses = ok_responses(self.ref)
        responses[0], responses[2] = responses[2], responses[0]
        comparison = diff.compare(self.ref, responses)
        self.assertFalse(comparison["contract_pass"])
        self.assertEqual(comparison["stream_outcome"], "invalid_stream")

    def test_duplicate_op(self):
        responses = ok_responses(self.ref)
        responses[1] = copy.deepcopy(responses[0])
        responses[1]["seq"] = 2
        responses[1]["next"] = 3
        comparison = diff.compare(self.ref, responses)
        self.assertFalse(comparison["contract_pass"])
        self.assertEqual(comparison["stream_outcome"], "invalid_stream")

    def test_missing_next(self):
        responses = ok_responses(self.ref)
        del responses[0]["next"]
        comparison = diff.compare(self.ref, responses)
        self.assertFalse(comparison["contract_pass"])
        self.assertEqual(comparison["stream_outcome"], "invalid_stream")

    def test_wrong_configure_source(self):
        responses = ok_responses(self.ref)
        responses[1]["result"]["source"] = 1
        comparison = diff.compare(self.ref, responses)
        self.assertFalse(comparison["contract_pass"])
        row = next(
            r for r in comparison["fields"] if r["field"] == "configure_window.source"
        )
        self.assertEqual(row["classification"], "diverged")

    def test_failed_init_no_shutdown(self):
        responses = ok_responses(self.ref)[:2]
        responses.append(
            {
                "schema": "metal-shadow/v0",
                "seq": 3,
                "next": 4,
                "op": "init",
                "ok": False,
                "error": "init_failed",
            }
        )
        comparison = diff.compare(self.ref, responses)
        self.assertFalse(comparison["contract_pass"])
        self.assertEqual(comparison["stream_outcome"], "execution_failed")
        self.assertFalse(comparison["metal_init_ok"])
        self.assertIn("init_failed", comparison["stream_reason"])

    def test_none_during_init(self):
        responses = ok_responses(self.ref)[:2] + [None]
        comparison = diff.compare(self.ref, responses)
        self.assertFalse(comparison["contract_pass"])
        self.assertEqual(comparison["stream_outcome"], "execution_failed")
        self.assertEqual(comparison["stream_reason"], "missing_response")
        text = diff.render_report(comparison, self.ref)
        self.assertIn("execution_failed", text)

    def test_shutdown_fail_preserves_init(self):
        responses = ok_responses(self.ref)
        responses[3] = {
            "schema": "metal-shadow/v0",
            "seq": 4,
            "next": 5,
            "op": "shutdown",
            "ok": False,
            "error": "already_shutdown",
        }
        comparison = diff.compare(self.ref, responses)
        self.assertFalse(comparison["contract_pass"])
        self.assertTrue(comparison["metal_init_ok"])
        self.assertFalse(comparison["shutdown_ok"])
        by_field = {row["field"]: row for row in comparison["fields"]}
        self.assertEqual(by_field["init.source"]["classification"], "matched")

    def test_missing_max_texture(self):
        responses = ok_responses(self.ref)
        del responses[2]["state"]["max_texture"]
        comparison = diff.compare(self.ref, responses)
        self.assertFalse(comparison["contract_pass"])
        row = next(r for r in comparison["fields"] if r["field"] == "init.max_texture")
        self.assertEqual(row["classification"], "unavailable")

    def test_lod_capability_not_strict(self):
        responses = ok_responses(self.ref)
        responses[2]["state"]["texture_lod"] = 0
        responses[2]["state"]["max_texture"] = self.ref["init"]["output"]["max_texture"]
        comparison = diff.compare(self.ref, responses)
        self.assertTrue(comparison["contract_pass"])
        row = next(r for r in comparison["fields"] if r["field"] == "init.texture_lod")
        self.assertEqual(row["classification"], "capability_difference")


class CommittedReferenceTests(unittest.TestCase):
    def test_committed_reference_validates(self):
        reference = json.loads(diff.REFERENCE_PATH.read_text())
        diff.validate_reference(reference)
        self.assertEqual(reference["init"]["input"]["source"], 0)


if __name__ == "__main__":
    unittest.main()
