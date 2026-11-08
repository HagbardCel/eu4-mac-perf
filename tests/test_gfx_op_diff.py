#!/usr/bin/env python3
import base64
import json
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

import copy  # noqa: E402

import eu4_gfx_op_capture as capture  # noqa: E402
import eu4_gfx_op_diff as diff  # noqa: E402
import eu4_gfx_shadow_worker as shadow  # noqa: E402
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
    (directory / "startup.status").write_text(
        "\n".join(
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
    )


class FakeWorker:
    def __init__(self, *, fail_op=None, unknown_op=None, corrupt=None):
        self.next_seq = 1
        self.fail_op = fail_op
        self.unknown_op = unknown_op
        self.corrupt = corrupt
        self.viewport = [0, 0, 0, 0, 0, 0]
        self.closed = False

    def call(self, op, args=None, seq=None):
        args = args or {}
        seq = self.next_seq if seq is None else seq
        if self.fail_op == op:
            raise TimeoutError("simulated worker timeout")
        if self.unknown_op == op:
            return {
                "schema": "metal-shadow/v0",
                "op": op,
                "seq": seq,
                "next": seq + 1,
                "ok": False,
                "error": "unknown_op",
            }
        self.next_seq = seq + 1
        if op == "settings":
            result = {}
            state = {}
        elif op == "configure_window":
            result = {
                "window_id": args["window_id"],
                "source": args["source"],
                "width": args["width"],
                "height": args["height"],
            }
            state = {}
        elif op == "init":
            result = {"success": 1}
            state = {
                "initialized": True,
                "backend_present": True,
                "width": 800,
                "height": 600,
                "source": 9,
                "primitive": 4,
                "viewport": [0, 0, 0, 0, 0, 0],
                "dirty": 0,
            }
        elif op == "set_viewport":
            self.viewport = list(args["words"])
            result = {"words": args["words"]}
            state = {
                "initialized": True,
                "backend_present": True,
                "width": 800,
                "height": 600,
                "source": 9,
                "primitive": 4,
                "viewport": list(self.viewport),
                "dirty": 0,
            }
            if self.corrupt is not None:
                index, value = self.corrupt
                state["viewport"][index] = value
        elif op == "shutdown":
            result = {}
            state = {}
        else:
            return {
                "schema": "metal-shadow/v0",
                "op": op,
                "seq": seq,
                "next": seq + 1,
                "ok": False,
                "error": "unknown_op",
            }
        return {
            "schema": "metal-shadow/v0",
            "op": op,
            "seq": seq,
            "next": seq + 1,
            "ok": True,
            "result": result,
            "state": state,
        }

    def stderr_text(self):
        return ""

    def close(self):
        self.closed = True


class OpDiffTests(unittest.TestCase):
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

    def _run(self, capture_path: Path, tmp: Path, **kwargs) -> dict:
        output = tmp / f"diff-{len(list(tmp.iterdir()))}"
        result = diff.run(
            capture_path, output, worker_factory=lambda: FakeWorker(**kwargs)
        )
        return result

    def test_matching_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root)
            result = self._run(artifact, root)
            self.assertEqual(result["status"], "pass")
            comparison = json.loads(Path(result["comparison"]).read_text())
            self.assertEqual(comparison["scope"], "function_cpu_state")
            self.assertEqual(comparison["startup"]["status"], "matched")
            self.assertTrue(comparison["shutdown_ok"])
            self.assertEqual(comparison["counts"].get("matched"), 2)

    def test_corrupt_worker_word_is_precise_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root)
            result = self._run(artifact, root, corrupt=(1, 720))
            self.assertEqual(result["status"], "mismatch")
            comparison = json.loads(Path(result["comparison"]).read_text())
            diagnostic = comparison["diagnostics"][0]
            self.assertEqual(diagnostic["symbol"], "_GfxSetViewPort")
            self.assertEqual(diagnostic["field"], "viewport[1]")
            self.assertEqual(diagnostic["original"], 1080)
            self.assertEqual(diagnostic["metal"], 720)

    def test_resource_lifecycle_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root, "observe-buffers")
            output = root / "diff-resources"
            result = diff.run(artifact, output)
            self.assertEqual(result["status"], "pass")
            comparison = json.loads(Path(result["comparison"]).read_text())
            self.assertTrue(comparison["teardown_ok"])
            self.assertTrue(comparison["shutdown_ok"])
            self.assertEqual(comparison["counts"].get("matched"), 3)
            commands = (output / "worker-commands.jsonl").read_text().splitlines()
            self.assertTrue(
                any(json.loads(line).get("harness_teardown") is True for line in commands)
            )

    def test_resource_corruption_is_precise(self):
        class CorruptObserve:
            def __init__(self):
                self.inner = shadow.Worker()

            @property
            def next_seq(self):
                return self.inner.next_seq

            def call(self, op, args=None, seq=None):
                response = self.inner.call(op, args, seq)
                if op == "observe" and args and args.get("resources") == ["vb_1"]:
                    response = copy.deepcopy(response)
                    entry = response["result"]["resources"]["vb_1"]
                    data = bytearray(base64.b64decode(entry["buffer"]["bytes_base64"]))
                    data[0] ^= 0xFF
                    entry["buffer"]["bytes_base64"] = base64.b64encode(data).decode("ascii")
                return response

            def stderr_text(self):
                return self.inner.stderr_text()

            def close(self):
                self.inner.close()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root, "observe-buffers")
            output = root / "diff-corrupt-resource"
            result = diff.run(
                artifact, output, worker_factory=lambda: CorruptObserve()
            )
            self.assertEqual(result["status"], "mismatch")
            comparison = json.loads(Path(result["comparison"]).read_text())
            diagnostic = comparison["diagnostics"][0]
            self.assertEqual(diagnostic["symbol"], "_GfxCreateVertexBuffer")
            self.assertEqual(diagnostic["field"], "contents.contents")
            self.assertIn("first_difference_offset=0", diagnostic.get("note", ""))

    def test_unknown_contents_are_declined(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root, "observe-buffers")
            lines = (artifact / "operations.jsonl").read_text().splitlines()
            record = json.loads(lines[0])
            record["contents_known"] = False
            lines[0] = json.dumps(record)
            (artifact / "operations.jsonl").write_text("\n".join(lines) + "\n")
            output = root / "diff-unknown"
            result = diff.run(artifact, output)
            self.assertEqual(result["status"], "unsupported")
            comparison = json.loads(Path(result["comparison"]).read_text())
            self.assertIn("unknown_original_contents", comparison["operations"][0]["reason"])

    def test_nondefault_binding_offsets_are_declined(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root, "observe-buffers")
            lines = (artifact / "operations.jsonl").read_text().splitlines()
            record = json.loads(lines[2])
            record["offsets"] = [4]
            lines[2] = json.dumps(record)
            (artifact / "operations.jsonl").write_text("\n".join(lines) + "\n")
            output = root / "diff-offsets"
            result = diff.run(artifact, output)
            self.assertEqual(result["status"], "unsupported")
            comparison = json.loads(Path(result["comparison"]).read_text())
            self.assertIn(
                "binding_offsets_not_reproducible",
                comparison["operations"][2]["reason"],
            )

    def test_malformed_worker_response_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root)

            class Malformed(FakeWorker):
                def call(self, op, args=None, seq=None):
                    if op == "set_viewport":
                        raise UnicodeDecodeError(
                            "utf-8", b"\xff", 0, 1, "invalid start byte"
                        )
                    return super().call(op, args, seq)

            result = diff.run(
                artifact, root / "diff-malformed", worker_factory=lambda: Malformed()
            )
            self.assertEqual(result["status"], "unavailable")

    def test_shutdown_failure_is_diagnostic_not_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root)

            class NoShutdown(FakeWorker):
                def call(self, op, args=None, seq=None):
                    if op == "shutdown":
                        raise TimeoutError("simulated shutdown failure")
                    return super().call(op, args, seq)

            output = root / "diff-noshutdown"
            result = diff.run(artifact, output, worker_factory=lambda: NoShutdown())
            self.assertEqual(result["status"], "pass")
            comparison = json.loads(Path(result["comparison"]).read_text())
            self.assertFalse(comparison["shutdown_ok"])

    def test_worker_failure_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root)
            result = self._run(artifact, root, fail_op="set_viewport")
            self.assertEqual(result["status"], "unavailable")

    def test_unknown_operation_is_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root)
            result = self._run(artifact, root, unknown_op="set_viewport")
            self.assertEqual(result["status"], "unsupported")

    def test_missing_adapter_is_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root, "observe-scalar")
            result = self._run(artifact, root)
            self.assertEqual(result["status"], "unsupported")
            comparison = json.loads(Path(result["comparison"]).read_text())
            statuses = [item["status"] for item in comparison["operations"]]
            self.assertIn("matched", statuses)
            self.assertIn("unsupported", statuses)

    def test_startup_mismatch_declines_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root)

            class WrongStartup(FakeWorker):
                def call(self, op, args=None, seq=None):
                    response = super().call(op, args, seq)
                    if op == "init":
                        response["state"]["width"] = 1024
                    return response

            output = root / "diff"
            result = diff.run(
                artifact, output, worker_factory=lambda: WrongStartup()
            )
            self.assertEqual(result["status"], "mismatch")
            comparison = json.loads(Path(result["comparison"]).read_text())
            self.assertEqual(comparison["startup"]["status"], "mismatch")
            self.assertTrue(
                all(
                    item["status"] == "unsupported"
                    for item in comparison["operations"]
                )
            )

    def test_overlap_is_unsupported_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root)
            summary = json.loads((artifact / "operations-summary.json").read_text())
            summary["overlaps"] = 1
            (artifact / "operations-summary.json").write_text(
                json.dumps(summary) + "\n"
            )
            result = self._run(artifact, root)
            self.assertEqual(result["status"], "unsupported")
            comparison = json.loads(Path(result["comparison"]).read_text())
            self.assertEqual(comparison["reason"], "ordering_overlap")

    def test_shape_is_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root)
            lines = (artifact / "operations.jsonl").read_text().splitlines()
            record = json.loads(lines[0])
            record["input_size"] = 20
            record["input_hex"] = record["input_hex"][:40]
            lines[0] = json.dumps(record)
            (artifact / "operations.jsonl").write_text("\n".join(lines) + "\n")
            result = self._run(artifact, root)
            self.assertEqual(result["status"], "incomplete")
            comparison = json.loads(Path(result["comparison"]).read_text())
            self.assertEqual(comparison["reason"], "viewport_shape")

    def test_real_worker_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root)
            output = root / "diff-real"
            result = diff.run(artifact, output)
            self.assertEqual(result["status"], "pass")
            comparison = json.loads(Path(result["comparison"]).read_text())
            self.assertEqual(comparison["startup"]["status"], "matched")
            self.assertTrue(comparison["shutdown_ok"])

    def test_repeated_replay_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._artifact(root)
            first = self._run(artifact, root)
            second = self._run(artifact, root)
            one = json.loads(Path(first["comparison"]).read_text())
            two = json.loads(Path(second["comparison"]).read_text())
            self.assertEqual(one["status"], two["status"])
            self.assertEqual(one["operations"], two["operations"])
            self.assertEqual(one["diagnostics"], two["diagnostics"])


COMMITTED = ROOT / "analysis" / "gfx-ops" / "v0" / "capture"
RESOURCE_CAPTURE = ROOT / "analysis" / "gfx-resources" / "v0" / "capture"


class CommittedCaptureTests(unittest.TestCase):
    def test_committed_capture_loads(self):
        artifact = capture.load_capture(COMMITTED)
        self.assertEqual(artifact.session, "f975f721-ec09-4a57-87b3-4e7a53fb3243")
        self.assertEqual(artifact.coverage["events"], 7)
        self.assertEqual(artifact.coverage["overlaps"], 0)
        self.assertEqual(artifact.operations[0].caller_rva, 0x149B21F)
        self.assertEqual(artifact.operations[1].caller_rva, 0x149D046)
        self.assertEqual(
            artifact.operations[0].thread_id,
            artifact.startup.init_exit["thread_id"],
        )
        self.assertEqual(
            artifact.operations[0].input_words,
            (1117, 1728, 1065353216, 0, 0, 0),
        )
        self.assertEqual(
            artifact.operations[0].input_words,
            artifact.operations[0].output_words,
        )

    @unittest.skipUnless(sys.platform == "darwin", "Metal replay is macOS")
    def test_committed_capture_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "committed"
            result = diff.run(COMMITTED, output)
            self.assertEqual(result["status"], "pass")
            comparison = json.loads(Path(result["comparison"]).read_text())
            self.assertEqual(comparison["startup"]["status"], "matched")
            self.assertEqual(comparison["counts"].get("matched"), 7)
            self.assertTrue(comparison["shutdown_ok"])


@unittest.skipUnless(
    RESOURCE_CAPTURE.is_dir(), "authentic resource capture is absent"
)
class AuthenticResourceTests(unittest.TestCase):
    def test_capture_loads(self):
        artifact = capture.load_capture(RESOURCE_CAPTURE)
        self.assertEqual(len(artifact.operations), 256)
        self.assertEqual(artifact.coverage["overlaps"], 0)
        unknown = [
            record
            for record in artifact.operations
            if record.kind in ("vertex_buffer_create", "vertex_buffer_update")
            and not record.contents_known
        ]
        self.assertEqual(len(unknown), 5)
        self.assertIn("payloads/6-1.bin", artifact.source_hashes)

    @unittest.skipUnless(sys.platform == "darwin", "Metal replay is macOS")
    def test_known_resources_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "resources"
            result = diff.run(RESOURCE_CAPTURE, output, skip_unknown=True)
            self.assertEqual(result["status"], "pass")
            comparison = json.loads(Path(result["comparison"]).read_text())
            self.assertTrue(comparison["skip_unknown"])
            self.assertEqual(comparison["counts"].get("matched"), 250)
            self.assertEqual(comparison["counts"].get("skipped"), 6)
            self.assertTrue(comparison["teardown_ok"])
            self.assertTrue(comparison["shutdown_ok"])


if __name__ == "__main__":
    unittest.main()
