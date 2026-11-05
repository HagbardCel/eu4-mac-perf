import base64
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_gfx_shadow_worker as shadow


def b64(data):
    return base64.b64encode(data).decode("ascii")


def flags_for(samples, supports):
    threshold = 2
    flags = []
    for sample, supported in zip(samples, supports):
        if sample > threshold and threshold <= 0x3FFFFFFF:
            threshold <<= 1
        flags.append(1 if sample == threshold and supported else 0)
    return flags


def create_args(ident, count, stride, data):
    payload = {"resource_id": ident, "count": count, "stride": stride, "usage": 0}
    payload["data_base64"] = None if data is None else b64(data)
    return payload


@unittest.skipUnless(sys.platform == "darwin", "Metal shadow worker runs on macOS")
class ShadowWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binary = shadow.build()
        info = subprocess.run(["lipo", "-info", str(binary)], capture_output=True, text=True, check=False)
        if info.returncode or "x86_64" not in info.stdout:
            raise AssertionError(info.stdout + info.stderr)

    def setUp(self):
        self.worker = shadow.Worker()

    def tearDown(self):
        if self.worker is not None:
            self.worker.close()
            self.worker = None

    def call(self, op, args=None, seq=None):
        resp = self.worker.call(op, args, seq)
        self.assertIsNotNone(resp, self.worker.stderr_text())
        self.assertEqual(resp.get("schema"), "metal-shadow/v0")
        return resp

    def ok(self, op, args=None):
        resp = self.call(op, args)
        self.assertTrue(resp.get("ok"), resp)
        self.assertNotIn("bytes_base64", str(resp.get("state")))
        return resp

    def boot(self):
        samples = [1, 2, 3, 4, 8]
        settings = self.ok("settings", {"display": 1, "windowed": 0, "samples": samples})
        result = settings["result"]
        self.assertFalse(settings["state"]["initialized"])
        self.assertEqual(settings["state"]["vertex_records"], 0)
        self.assertEqual(settings["state"]["vertex_gpus"], 0)
        self.assertEqual(result["return"], 0)
        self.assertEqual(result["byte_10"], 1)
        self.assertEqual(result["anisotropy"], 0)
        self.assertEqual(result["span"], 0x30000000300)
        self.assertEqual(result["constant"], 0x400)
        self.assertEqual(result["flags"], flags_for(samples, result["device_supports"]))
        self.ok("configure_window", {"width": 800, "height": 600, "window_id": 7, "source": 9})
        init = self.ok("init")
        state = init["state"]
        self.assertTrue(state["initialized"])
        self.assertEqual(state["phase"], 2)
        self.assertTrue(state["backend_present"])
        self.assertEqual(state["width"], 800)
        self.assertEqual(state["height"], 600)
        self.assertEqual(state["source"], 9)
        self.assertEqual(state["primitive"], 4)
        self.assertEqual(state["texture_lod"], 0)
        self.assertEqual(state["dirty"], 0)
        self.assertEqual(state["viewport"], [0, 0, 0, 0, 0, 0])
        self.assertIn(state["max_texture"], (8192, 16384))
        self.assertEqual(len(state["blend_snap"]), 72)
        self.assertEqual(len(state["depth_snap"]), 72)
        self.assertEqual(len(state["raster_snap"]), 32)
        self.assertEqual(state["live_count"], 0)
        self.assertNotIn("live_ids", state)
        return state

    def observe(self, resources, slots):
        resp = self.ok("observe", {"resources": resources, "slots": slots})
        return resp["result"]

    def test_persistent_sequence(self):
        initial = self.boot()
        snaps = (initial["blend_snap"], initial["depth_snap"], initial["raster_snap"])
        releases = initial["backend_releases"]
        original = bytes([0x11]) * 16
        created = self.ok("create_vertex_buffer", create_args("A", 4, 4, original))
        self.assertEqual(created["result"]["header"], {"stride": 4, "count": 4, "token": 1})
        self.assertTrue(created["result"]["contents_known"])
        self.assertEqual(created["result"]["buffer"]["length"], 16)
        self.assertTrue(created["result"]["buffer"]["shared"])
        self.assertNotIn("bytes_base64", created["result"]["buffer"])
        self.assertEqual(created["state"]["live_count"], 1)
        self.assertEqual(created["state"]["vertex_records"], 1)
        self.assertEqual(created["state"]["vertex_gpus"], 1)
        bound = self.ok("set_vertex_buffers", {"start": 0, "resources": ["A"]})
        self.assertEqual(bound["state"]["dirty"], 1)
        self.assertEqual(bound["state"]["slots"][0]["resource_id"], "A")
        self.assertEqual(bound["state"]["slots"][0]["length"], 16)
        self.assertNotIn("bytes_base64", bound["state"]["slots"][0])

        inplace = bytes([0x22]) * 16
        self.ok("update_vertex_buffer", {
            "resource_id": "A", "count": 4, "offset": 0, "partial": 0, "data_base64": b64(inplace),
        })
        seen = self.observe(["A"], [0])
        self.assertEqual(seen["live_ids"], ["A"])
        self.assertEqual(seen["slots"][0]["resource_id"], "A")
        self.assertEqual(base64.b64decode(seen["slots"][0]["bytes_base64"]), inplace)
        self.assertEqual(base64.b64decode(seen["resources"]["A"]["buffer"]["bytes_base64"]), inplace)
        self.assertEqual(seen["resources"]["A"]["header"]["token"], 1)

        mixed = bytes([0x33]) * 8 + bytes([0x44]) * 8
        self.ok("update_vertex_buffer_multiple", {
            "resource_id": "A",
            "ranges": [
                {"count": 2, "data_base64": b64(mixed[:8])},
                {"count": 2, "data_base64": b64(mixed[8:])},
            ],
        })
        seen = self.observe(["A"], [0])
        self.assertEqual(base64.b64decode(seen["slots"][0]["bytes_base64"]), mixed)

        replaced = bytes([0x55]) * 8
        self.ok("update_vertex_buffer", {
            "resource_id": "A", "count": 2, "offset": 0, "partial": 0, "data_base64": b64(replaced),
        })
        replaced_obs = self.ok("observe", {"resources": ["A"], "slots": [0]})
        seen = replaced_obs["result"]
        self.assertEqual(replaced_obs["state"]["live_count"], 1)
        self.assertEqual(replaced_obs["state"]["vertex_records"], 1)
        self.assertEqual(replaced_obs["state"]["vertex_gpus"], 1)
        self.assertEqual(seen["slots"][0]["resource_id"], "A")
        self.assertEqual(seen["slots"][0]["length"], 8)
        self.assertEqual(base64.b64decode(seen["slots"][0]["bytes_base64"]), replaced)
        self.assertEqual(base64.b64decode(seen["resources"]["A"]["buffer"]["bytes_base64"]), replaced)
        self.assertEqual(seen["resources"]["A"]["buffer"]["length"], 8)
        self.assertEqual(seen["resources"]["A"]["header"]["token"], 1)

        words = [11, 22, 33, 44, 55, 66]
        view = self.ok("set_viewport", {"words": words})
        self.assertEqual(view["state"]["viewport"], words)
        reset = self.ok("reset")
        self.assertEqual(reset["result"]["return"], 1)
        self.assertEqual(reset["state"]["viewport"], words)
        self.assertEqual(reset["state"]["dirty"], 1)
        after_reset = (reset["state"]["blend_snap"], reset["state"]["depth_snap"], reset["state"]["raster_snap"])
        self.assertEqual(after_reset, snaps)
        again = self.ok("on_reset_master")
        self.assertEqual(
            (again["state"]["blend_snap"], again["state"]["depth_snap"], again["state"]["raster_snap"]),
            snaps,
        )
        for op in ("set_blend", "set_depth", "set_raster"):
            applied = self.ok(op)
            self.assertEqual(
                (applied["state"]["blend_snap"], applied["state"]["depth_snap"], applied["state"]["raster_snap"]),
                snaps,
            )

        early = self.call("shutdown")
        self.assertFalse(early["ok"])
        self.assertEqual(early["error"], "live_resources")
        followed = self.ok("observe", {"resources": ["A"], "slots": [0]})
        self.assertEqual(followed["state"]["live_count"], 1)
        self.assertEqual(followed["state"]["backend_releases"], releases)
        self.assertEqual(base64.b64decode(followed["result"]["slots"][0]["bytes_base64"]), replaced)

        destroyed = self.ok("destroy_vertex_buffer", {"resource_id": "A"})
        self.assertEqual(destroyed["state"]["live_count"], 0)
        self.assertEqual(destroyed["state"]["vertex_records"], 0)
        self.assertEqual(destroyed["state"]["vertex_gpus"], 0)
        stale = self.call("set_vertex_buffers", {"start": 0, "resources": ["A"]})
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["error"], "stale_resource")
        shut = self.ok("shutdown")
        self.assertTrue(shut["result"]["slot_null"])
        self.assertFalse(shut["result"]["initialized"])
        self.assertEqual(shut["result"]["backend_releases"], releases + 1)
        self.assertFalse(shut["state"]["initialized"])
        self.assertEqual(shut["state"]["live_count"], 0)
        self.assertEqual(shut["state"]["vertex_records"], 0)
        self.assertEqual(shut["state"]["vertex_gpus"], 0)
        rejected = self.call("create_vertex_buffer", create_args("B", 1, 4, b"\x01\x02\x03\x04"))
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["error"], "already_shutdown")
        self.assertEqual(self.worker.close(), 0)
        self.worker = None

    def test_validation_keeps_running(self):
        self.boot()
        short = self.call("create_vertex_buffer", {
            "resource_id": "A", "count": 4, "stride": 4, "usage": 0, "data_base64": b64(b"\x01"),
        })
        self.assertFalse(short["ok"])
        self.assertEqual(short["error"], "size")
        original = bytes([0x11]) * 16
        self.ok("create_vertex_buffer", create_args("A", 4, 4, original))
        self.ok("set_vertex_buffers", {"start": 0, "resources": ["A"]})
        patched = bytes([0x22]) * 16
        self.ok("update_vertex_buffer", {
            "resource_id": "A", "count": 0, "offset": 0, "partial": 0, "data_base64": b64(patched),
        })
        seen = self.observe(["A"], [0])
        self.assertEqual(base64.b64decode(seen["slots"][0]["bytes_base64"]), patched)

        over = self.call("update_vertex_buffer", {
            "resource_id": "A", "count": 4, "offset": 8, "partial": 1, "data_base64": b64(bytes([0x99]) * 16),
        })
        self.assertFalse(over["ok"])
        self.assertEqual(over["error"], "bounds")
        wide = self.call("update_vertex_buffer_multiple", {
            "resource_id": "A",
            "ranges": [
                {"count": 4, "data_base64": b64(bytes([0xAB]) * 16)},
                {"count": 4, "data_base64": b64(bytes([0xCD]) * 16)},
            ],
        })
        self.assertFalse(wide["ok"])
        self.assertEqual(wide["error"], "bounds")
        seen = self.observe(["A"], [0])
        self.assertEqual(base64.b64decode(seen["slots"][0]["bytes_base64"]), patched)
        nxt = bytes([0x33]) * 4 + patched[4:]
        self.ok("update_vertex_buffer", {
            "resource_id": "A", "count": 1, "offset": 0, "partial": 1, "data_base64": b64(nxt[:4]),
        })
        seen = self.observe(["A"], [0])
        self.assertEqual(base64.b64decode(seen["slots"][0]["bytes_base64"]), nxt)

        self.ok("destroy_vertex_buffer", {"resource_id": "A"})
        blank = self.ok("create_vertex_buffer", create_args("B", 4, 4, None))
        self.assertFalse(blank["result"]["contents_known"])
        self.ok("set_vertex_buffers", {"start": 0, "resources": ["B"]})
        hidden = self.observe(["B"], [0])
        self.assertFalse(hidden["resources"]["B"]["buffer"]["contents_known"])
        self.assertNotIn("bytes_base64", hidden["resources"]["B"]["buffer"])
        self.assertNotIn("bytes_base64", hidden["slots"][0])
        filled = bytes(range(16))
        self.ok("update_vertex_buffer", {
            "resource_id": "B", "count": 4, "offset": 0, "partial": 0, "data_base64": b64(filled),
        })
        shown = self.observe(["B"], [0])
        self.assertTrue(shown["resources"]["B"]["buffer"]["contents_known"])
        self.assertEqual(base64.b64decode(shown["slots"][0]["bytes_base64"]), filled)
        self.ok("destroy_vertex_buffer", {"resource_id": "B"})

        self.ok("create_vertex_buffer", create_args("C", 1, 4, b"\x01\x02\x03\x04"))
        again = self.call("create_vertex_buffer", create_args("C", 1, 4, b"\x01\x02\x03\x04"))
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"], "duplicate_id")
        self.ok("destroy_vertex_buffer", {"resource_id": "C"})
        reused = self.call("create_vertex_buffer", create_args("C", 1, 4, b"\x01\x02\x03\x04"))
        self.assertFalse(reused["ok"])
        self.assertEqual(reused["error"], "id_reused")
        missing = self.call("create_vertex_buffer", {
            "resource_id": "D", "count": 1, "stride": 4, "usage": 0,
        })
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error"], "missing_field")

    def test_protocol_seq(self):
        bad = self.worker.line("not json")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"], "malformed")
        self.assertEqual(bad["next"], 1)
        skipped = self.call("settings", {"display": 1, "windowed": 0, "samples": []}, seq=9)
        self.assertFalse(skipped["ok"])
        self.assertEqual(skipped["error"], "bad_seq")
        self.assertEqual(skipped["next"], 1)
        floated = self.worker.line(
            '{"schema":"metal-shadow/v0","seq":1.0,"op":"settings","args":{"display":1,"windowed":0,"samples":[]}}'
        )
        self.assertFalse(floated["ok"])
        self.assertEqual(floated["next"], 1)
        good = self.ok("settings", {"display": 1, "windowed": 0, "samples": []})
        self.assertEqual(good["seq"], 1)
        self.assertEqual(good["next"], 2)
        unknown = self.call("nope")
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["error"], "unknown_op")
        self.assertEqual(unknown["seq"], 2)
        self.assertEqual(unknown["next"], 3)

    def test_init_failure_exits(self):
        self.ok("configure_window", {"width": 100, "height": 100, "window_id": 0, "source": 1})
        failed = self.call("init")
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error"], "init_failed")
        self.assertEqual(self.worker.proc.wait(timeout=5), 1)

    def test_failed_read_stays_up(self):
        self.worker.close()
        self.worker = shadow.Worker(env={"EU4_SHADOW_FAIL_READ": "1"})
        self.boot()
        self.ok("create_vertex_buffer", create_args("A", 4, 4, bytes([0x11]) * 16))
        self.ok("set_vertex_buffers", {"start": 0, "resources": ["A"]})
        resource = self.call("observe", {"resources": ["A"]})
        self.assertFalse(resource["ok"])
        self.assertEqual(resource["error"], "observation_failed")
        self.assertNotIn("executed", resource)
        slot = self.call("observe", {"slots": [0]})
        self.assertFalse(slot["ok"])
        self.assertEqual(slot["error"], "observation_failed")
        followed = self.ok("set_viewport", {"words": [1, 2, 3, 4, 5, 6]})
        self.assertEqual(followed["state"]["viewport"], [1, 2, 3, 4, 5, 6])

    def test_state_length_failure_exits(self):
        self._state_failure("length")

    def test_state_map_failure_exits(self):
        self._state_failure("map")

    def _state_failure(self, mode):
        self.worker.close()
        self.worker = shadow.Worker(env={"EU4_SHADOW_FAIL_STATE": mode})
        self.boot()
        self.ok("create_vertex_buffer", create_args("A", 4, 4, bytes([0x11]) * 16))
        failed = self.call("set_vertex_buffers", {"start": 0, "resources": ["A"]})
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error"], "observation_failed")
        self.assertIs(failed.get("executed"), True)
        self.assertNotIn("state", failed)
        self.assertEqual(self.worker.proc.wait(timeout=5), 1)

    def test_reader_keeps_surplus(self):
        one = b'{"schema":"metal-shadow/v0","ok":true,"seq":1,"next":2}\n'
        two = b'{"schema":"metal-shadow/v0","ok":true,"seq":2,"next":3}\n'
        self.worker._buf = bytearray(one + two)
        first = self.worker.read(timeout=0.05)
        second = self.worker.read(timeout=0.05)
        self.assertEqual(first["seq"], 1)
        self.assertEqual(second["seq"], 2)

    def test_reader_incomplete_eof(self):
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        self.worker.proc.stdout.close()
        self.worker.proc.stdout = os.fdopen(read_fd, "rb", buffering=0)
        self.worker._buf = bytearray(b'{"ok":')
        with self.assertRaises(RuntimeError) as caught:
            self.worker.read(timeout=0.2)
        self.assertIn("incomplete", str(caught.exception))

    def test_embedded_nul_rejected(self):
        prefix = json.dumps({
            "schema": "metal-shadow/v0",
            "seq": 1,
            "op": "settings",
            "args": {"display": 1, "windowed": 0, "samples": []},
        }).encode()
        self.worker.write(prefix + b"\0garbage\n")
        bad = self.worker.read()
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"], "malformed")
        self.assertEqual(bad["next"], 1)
        self.assertEqual(self.worker.next_seq, 1)
        good = self.ok("settings", {"display": 1, "windowed": 0, "samples": []})
        self.assertEqual(good["seq"], 1)

    def test_pipeline_keeps_sequence(self):
        args = {"display": 1, "windowed": 0, "samples": []}
        for seq in (1, 2):
            body = {"schema": "metal-shadow/v0", "seq": seq, "op": "settings", "args": args}
            self.worker.write((json.dumps(body) + "\n").encode())
        first = self.worker.read()
        second = self.worker.read()
        self.assertTrue(first["ok"])
        self.assertEqual(first["seq"], 1)
        self.assertTrue(second["ok"])
        self.assertEqual(second["seq"], 2)
        self.assertEqual(self.worker.next_seq, 3)
        third = self.worker.call("settings", args)
        self.assertTrue(third["ok"])
        self.assertEqual(third["seq"], 3)

    def test_write_all_short_writes(self):
        class ShortWriter:
            def __init__(self):
                self.chunks = []

            def write(self, view):
                data = bytes(view[:3])
                self.chunks.append(data)
                return len(data)

            def flush(self):
                return None

        fake = ShortWriter()
        real = self.worker.proc.stdin
        self.worker.proc.stdin = fake
        try:
            payload = b"abcdefghijklmnop"
            self.worker.write(payload)
            self.assertEqual(b"".join(fake.chunks), payload)
        finally:
            self.worker.proc.stdin = real
