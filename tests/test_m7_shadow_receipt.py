"""Native shadow receipt / checkpoint-request binding tests (M7 PR3).

The host includes the production bridge and exercises the exact verifier used
by the in-process path.  No EU4 process, GPU replay or Metal device is needed.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
HOST = ROOT / "tests" / "gfx_shadow_receipt_host.mm"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifacts(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name in ("ready", "shader-receipt.json"):
            continue
        digest.update(str(path.relative_to(directory)).encode())
        digest.update(b"\0")
        digest.update(_sha(path).encode())
        digest.update(b"\n")
    return digest.hexdigest()


@unittest.skipUnless(sys.platform == "darwin",
                     "the bridge is an Objective-C macOS target")
class ShadowReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.host = Path(cls.temporary.name) / "shadow_receipt_host"
        built = subprocess.run(
            ["clang", "-arch", "arm64", "-O2", "-fobjc-arc", "-I", str(ROOT),
             "-I", str(BENCH), "-framework", "Foundation",
             "-framework", "Metal", "-framework", "CoreGraphics",
             "-Wl,-dead_strip", "-o", str(cls.host), str(HOST)],
            capture_output=True, text=True, check=False, timeout=180)
        if built.returncode:
            raise unittest.SkipTest(built.stderr.strip())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(dir=self.temporary.name))
        self.shadow = self.base / "shadow"
        self.shaders = self.shadow / "shaders"
        self.shaders.mkdir(parents=True)
        (self.shaders / "shaders.json").write_text(json.dumps({
            "schema": "gfx-shader-translation/v1", "programs": []}))
        (self.shaders / "program.metal").write_text("// translated")
        self.request = self.shadow / "checkpoint-request.json"
        self.request.write_text(json.dumps({
            "schema": "gfx-frame-checkpoint-request/v1",
            "checkpoints": [{"event": 4, "target": "10", "file": "x.bgra"}],
        }))
        self.identity = {
            "capture_frame_id": 7, "pid": 42, "session_id": "session-1",
            "trace_manifest_sha256": "m" * 64,
            "event_stream_sha256": "e" * 64,
            "event_stream_bytes": 1234,
        }
        self.receipt = {
            "schema": "m7-shadow-shader-receipt/v1",
            **self.identity,
            "shaders_json_sha256": _sha(self.shaders / "shaders.json"),
            "artifacts_sha256": _artifacts(self.shaders),
            "checkpoint_request_file": self.request.name,
            "checkpoint_request_sha256": _sha(self.request),
            "checkpoint_count": 1,
        }
        self.write_receipt()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def write_receipt(self):
        (self.shadow / "shader-receipt.json").write_text(
            json.dumps(self.receipt))

    def verify(self, **overrides):
        identity = dict(self.identity)
        identity.update(overrides)
        ran = subprocess.run(
            [str(self.host), str(self.shadow), str(self.shaders),
             str(identity["capture_frame_id"]), str(identity["pid"]),
             identity["session_id"], identity["trace_manifest_sha256"],
             identity["event_stream_sha256"],
             str(identity["event_stream_bytes"])],
            capture_output=True, text=True, check=False, timeout=30)
        if ran.returncode != 0:
            self.fail(ran.stderr or "receipt host failed")
        return json.loads(ran.stdout)

    def test_valid_receipt_binds_the_checkpoint_request(self):
        result = self.verify()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["checkpoint_request_file"],
                         self.request.name)
        self.assertEqual(result["checkpoint_request_sha256"],
                         _sha(self.request))

    def test_stale_receipt_without_checkpoint_fields_is_rejected(self):
        self.receipt.pop("checkpoint_request_file")
        self.receipt.pop("checkpoint_request_sha256")
        self.write_receipt()
        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertIn("checkpoint request file", result["failure"])

    def test_stale_receipt_identity_is_rejected(self):
        result = self.verify(capture_frame_id=8)
        self.assertFalse(result["ok"])
        self.assertIn("identity mismatch", result["failure"])

    def test_mismatched_request_digest_is_rejected(self):
        self.receipt["checkpoint_request_sha256"] = "0" * 64
        self.write_receipt()
        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertIn("digest mismatch", result["failure"])

    def test_missing_request_file_is_rejected(self):
        self.request.unlink()
        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertIn("missing or invalid", result["failure"])

    def test_request_count_mismatch_is_rejected(self):
        self.receipt["checkpoint_count"] = 2
        self.write_receipt()
        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertIn("count", result["failure"])

    def test_path_traversal_request_name_is_rejected(self):
        self.receipt["checkpoint_request_file"] = "../checkpoint-request.json"
        self.write_receipt()
        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertIn("missing or invalid", result["failure"])

    def test_corrupt_shader_artifact_is_rejected(self):
        (self.shaders / "program.metal").write_text("// tampered")
        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertIn("artifact digest", result["failure"])


if __name__ == "__main__":
    unittest.main()
