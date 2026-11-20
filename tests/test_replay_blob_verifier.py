"""Repeated content-addressed blob references in the sealed event stream.

A checkpoint image can be byte-identical to the frame seed, so content-addressed
storage legitimately returns the seed's blob name for the checkpoint.  A
reference that omits ``bytes`` states no size and must not be treated as zero;
two declared sizes that disagree are still a conflict.  This exercises the
production verifier used by the in-process shadow bridge, without a Metal device
or an EU4 launch.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
CORE = BENCH / "eu4_gfx_frame_replay.mm"
HOST = ROOT / "tests" / "gfx_replay_blob_host.mm"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import native_build  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@unittest.skipUnless(sys.platform == "darwin", "the replay core is macOS")
class BlobReferenceVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temporary.name) / "gfx_replay_blob_host"
        native_build.require_build(
            [
                "clang", "-arch", "arm64", "-O1", "-fobjc-arc", "-lc++",
                "-framework", "Foundation", "-framework", "Metal",
                "-I", str(BENCH), "-o", str(cls.binary),
                str(HOST), str(CORE),
            ],
            label="replay-blob-host", sources=[HOST, CORE],
            frameworks=("Foundation", "Metal"), require_arm64=True,
            timeout=300)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def frame(self, events: list[dict], payload: bytes) -> Path:
        directory = Path(self.temporary.name) / f"frame-{_sha(payload)[:8]}"
        (directory / "blobs").mkdir(parents=True, exist_ok=True)
        name = f"seed-{_sha(payload)}.bin"
        (directory / "blobs" / name).write_bytes(payload)
        (directory / "frame-events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n")
        return directory

    def run_host(self, directory: Path) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.binary), str(directory)],
                              capture_output=True, text=True, check=False,
                              timeout=60)

    def reference(self, name: str, sha: str, *, size=None) -> dict:
        value = {"file": name, "sha256": sha}
        if size is not None:
            value["bytes"] = size
        return value

    def test_unknown_size_is_not_zero_and_does_not_conflict(self):
        payload = b"identical-seed-and-checkpoint-pixels"
        name = f"seed-{_sha(payload)}.bin"
        events = [
            {"index": 0, "seed": self.reference(name, _sha(payload))},
            {"index": 1, "default_checkpoint": self.reference(
                name, _sha(payload), size=len(payload))},
        ]
        ran = self.run_host(self.frame(events, payload))
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("blob_references=0", ran.stdout)

    def test_declared_size_is_verified_after_an_unknown_size(self):
        payload = b"size-is-wrong-once-declared-here"
        name = f"seed-{_sha(payload)}.bin"
        events = [
            {"index": 0, "seed": self.reference(name, _sha(payload))},
            {"index": 1, "default_checkpoint": self.reference(
                name, _sha(payload), size=len(payload) + 1)},
        ]
        ran = self.run_host(self.frame(events, payload))
        self.assertEqual(ran.returncode, 1)
        self.assertIn("size mismatch", ran.stderr)

    def test_conflicting_declared_sizes_are_still_a_conflict(self):
        payload = b"two-different-declared-sizes"
        name = f"seed-{_sha(payload)}.bin"
        events = [
            {"index": 0, "seed": self.reference(
                name, _sha(payload), size=len(payload))},
            {"index": 1, "default_checkpoint": self.reference(
                name, _sha(payload), size=len(payload) + 1)},
        ]
        ran = self.run_host(self.frame(events, payload))
        self.assertEqual(ran.returncode, 1)
        self.assertIn("conflicting repeated blob reference", ran.stderr)

    def test_conflicting_digests_are_still_a_conflict(self):
        payload = b"two-different-digests"
        name = f"seed-{_sha(payload)}.bin"
        events = [
            {"index": 0, "seed": self.reference(
                name, _sha(payload), size=len(payload))},
            {"index": 1, "default_checkpoint": self.reference(
                name, "0" * 64, size=len(payload))},
        ]
        ran = self.run_host(self.frame(events, payload))
        self.assertEqual(ran.returncode, 1)
        self.assertIn("conflicting repeated blob reference", ran.stderr)

    def test_nested_compressed_level_blob_is_verified(self):
        pixel = b"decoded-level-pixels"
        compressed = b"original-s3tc-blocks"
        decoded_name = f"tex-{_sha(pixel)}.bin"
        compressed_name = f"texc-{_sha(compressed)}.bin"
        directory = Path(self.temporary.name) / "frame-compressed"
        (directory / "blobs").mkdir(parents=True, exist_ok=True)
        (directory / "blobs" / decoded_name).write_bytes(pixel)
        (directory / "blobs" / compressed_name).write_bytes(compressed)
        events = [{
            "index": 0,
            "textures": [{
                "levels": [{
                    "file": decoded_name, "sha256": _sha(pixel),
                    "bytes": len(pixel),
                    "compressed": {
                        "format": "DXT5", "file": compressed_name,
                        "sha256": _sha(compressed),
                        "bytes": len(compressed),
                    },
                }],
            }],
        }]
        (directory / "frame-events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n")
        ran = self.run_host(directory)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("blob_references=0", ran.stdout)
        (directory / "blobs" / compressed_name).write_bytes(b"X" * len(compressed))
        ran = self.run_host(directory)
        self.assertEqual(ran.returncode, 1)
        self.assertIn("digest mismatch", ran.stderr)

    def test_disk_digest_is_still_verified(self):
        payload = b"on-disk-content-does-not-match"
        name = f"seed-{_sha(payload)}.bin"
        events = [{"index": 0, "seed": self.reference(name, "a" * 64)}]
        ran = self.run_host(self.frame(events, payload))
        self.assertEqual(ran.returncode, 1)
        self.assertIn("digest mismatch", ran.stderr)


if __name__ == "__main__":
    unittest.main()
