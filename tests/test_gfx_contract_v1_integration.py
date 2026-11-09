"""A7: execute an independently authorized v1 validator from staged M1."""

from __future__ import annotations

import hashlib
import json
import struct
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation.gfx_draw_accept_v2 import build_candidate  # noqa: E402
from automation.gfx_l32b_hooks import L32bHooks  # noqa: E402
from tests.test_gfx_draw_packet import (  # noqa: E402
    CALLER,
    DRAW_RVA,
    _draw_row,
    _master,
    _resource_row,
    _write_capture,
)

V1_MODULE = '''"""Test-only v1 shader identity validator."""
from __future__ import annotations

import os
from pathlib import Path


def validate_shader_identity(value, ctx):
    marker = os.environ.get("EU4_V1_MARKER")
    if marker:
        Path(marker).write_text("v1", encoding="utf-8")
    resource = str(value.get("resource_id") or "")
    pointer = str(value.get("shader_pointer") or "")
    master = ctx.master.get("shader")
    if not resource or not isinstance(master, int) or master == 0:
        return "shader identity has no resource or selected shader pointer"
    if pointer != f"0x{master:x}":
        return "shader pointer differs from the selected draw"
    records = [
        record for record in ctx.artifact.operations
        if record.symbol == "_GfxSetShader" and record.resource_id == resource
    ]
    if not records:
        return "shader resource observation is missing"
    record = records[-1]
    if record.output_hex and int(record.output_hex, 16) != master:
        return "shader header differs from the selected draw"
    return None


VALIDATORS = {"shader_identity": validate_shader_identity}
'''

PACKET_DELTA = '''    shader_record = next(
        (
            record for record in artifact.operations
            if record.symbol == "_GfxSetShader" and record.resource_id
        ),
        None,
    )
    if shader_record is not None:
        inputs["shader_identity"] = _input(
            "established",
            [
                snapshot_evidence,
                _resource_evidence(shader_record),
            ],
        )
        inputs["shader_identity"]["value"] = {
            "resource_id": shader_record.resource_id,
            "shader_pointer": f"0x{master['shader']:x}",
            "call_id": shader_record.call_id,
        }

'''


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _make_m1(tmp: Path) -> tuple[str, str, Path]:
    head = _git(ROOT, "rev-parse", "HEAD")
    worktree = tmp / "m1"
    subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach",
         str(worktree), head],
        check=True, capture_output=True,
    )
    packet = worktree / "benchmark" / "eu4_gfx_draw_packet.py"
    text = packet.read_text(encoding="utf-8")
    marker = '    primitive_enum = target.meta.get("primitive_enum")\n'
    if marker not in text:
        raise AssertionError("packet-builder insertion marker is missing")
    packet.write_text(text.replace(marker, PACKET_DELTA + marker, 1), encoding="utf-8")
    contracts = worktree / "automation" / "gfx_contracts"
    (contracts / "v1.py").write_text(V1_MODULE, encoding="utf-8")
    (contracts / "v1.spec.json").write_text(
        json.dumps({"version": "v1", "fields": ["shader_identity"]}, indent=2) + "\n",
        encoding="utf-8",
    )
    tests = worktree / "tests" / "test_gfx_contract_v1.py"
    tests.write_text(
        "import unittest\n\n"
        "class V1Tests(unittest.TestCase):\n"
        "    def test_placeholder(self):\n"
        "        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(worktree), "add",
         "benchmark/eu4_gfx_draw_packet.py",
         "automation/gfx_contracts/v1.py",
         "automation/gfx_contracts/v1.spec.json",
         "tests/test_gfx_contract_v1.py"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "-c", "user.name=A7",
         "-c", "user.email=a7@local", "commit", "-m", "A7 test-only v1"],
        check=True, capture_output=True,
    )
    return head, _git(worktree, "rev-parse", "HEAD"), worktree


def _capture(tmp: Path, *, header: int = 0x1234, corrupt: bool = False) -> Path:
    primitive = _resource_row(
        3, "_GfxSetPrimitiveType", "0x15ea76f", primitive_enum=4,
        kind="primitive_set",
    )
    shader = _resource_row(
        4, "_GfxSetShader", "0x15ea700",
        output_size=8, output_hex=f"{header:016x}",
        resource_id="shader-1", kind="resource",
        payload={"size": 8, "file": "payloads/shader-1.bin"},
    )
    shader["completion_seq"] = 2
    draw = _draw_row(5, payload="payloads/4-4.bin")
    draw["completion_seq"] = 3
    capture = _write_capture(
        tmp,
        rows=[primitive, shader, draw],
        payload=_master(),
        payloads={"payloads/shader-1.bin": struct.pack("<Q", header)},
        targets=[
            {"symbol": "_GfxDraw", "rva": DRAW_RVA, "length": 6,
             "relocation": "safe"},
            {"symbol": "_GfxSetShader", "rva": "0x15ea700", "length": 5,
             "relocation": "safe"},
            {"symbol": "_GfxSetPrimitiveType", "rva": "0x15ea76f", "length": 5,
             "relocation": "safe"},
        ],
        summary_overrides={
            "target_draw_call_id": 5,
            "primitive_sets": 1,
        },
    )
    if corrupt:
        (capture / "payloads" / "4-4.bin").write_bytes(b"\x00" * 16)
    return capture


def _state(m0: str, m1: str, worktree: Path, capture: Path) -> dict:
    v1 = worktree / "automation" / "gfx_contracts" / "v1.py"
    v0 = worktree / "automation" / "gfx_contracts" / "v0.py"
    manifest = subprocess.run(
        ["git", "-C", str(ROOT), "show",
         f"{m0}:campaigns/eu4-l32b-complete/l32b-manifest.json"],
        capture_output=True, check=True,
    ).stdout
    return {
        "promotion_kind": "contract",
        "contract_version": "v1",
        "contract_sha256": _sha(v1),
        "contract": {
            "changed_paths": [
                "automation/gfx_contracts/v1.py",
                "automation/gfx_contracts/v1.spec.json",
                "tests/test_gfx_contract_v1.py",
            ]
        },
        "trusted_contract": {
            "commit": m0,
            "contract_version": "v0",
            "contract_sha256": _sha(v0),
        },
        "checker_freeze_commit": m0,
        "checker_freeze_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "_integration_base": m0,
        "_integration_kind": "contract",
        "capture_reference": {"capture": str(capture), "selection": {}},
    }


@unittest.skipUnless(sys.platform == "darwin", "A7 requires macOS native build")
class ContractV1IntegrationTests(unittest.TestCase):
    def test_v1_executes_and_validates_shader_identity(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            m0, m1, worktree = _make_m1(tmp)
            try:
                capture = _capture(tmp)
                marker = tmp / "v1.marker"
                os.environ["EU4_V1_MARKER"] = str(marker)
                state = _state(m0, m1, worktree, capture)
                workflow = json.loads(
                    (ROOT / "campaigns/eu4-l32b-complete/workflow.json").read_text()
                )
                hooks = L32bHooks(repo=ROOT, workflow=workflow, run_root=tmp / "run")
                (capture / "capture-request.json").write_text(
                    json.dumps({"library_sha256": "0" * 64}) + "\n",
                    encoding="utf-8",
                )
                first = hooks._post_integration(Path(m1), state)
                self.assertEqual(first.get("status"), "pending_capture", first)
                (capture / "capture-request.json").write_text(
                    json.dumps({
                        "library_sha256": first["observer_library_sha256"]
                    }) + "\n",
                    encoding="utf-8",
                )
                result = hooks._post_integration(Path(m1), state)
                print("A7_M1_SHA", m1)
                print("A7_SHADER_IDENTITY", "shader_identity" in (result.get("established_fields") or []))
                self.assertEqual(result.get("status"), "verified", result)
                self.assertNotEqual(result.get("checker"), "fail", result)
                self.assertIn("shader_identity", result.get("established_fields") or [])
                self.assertTrue(marker.is_file(), result)
                self.assertEqual(marker.read_text(encoding="utf-8"), "v1")
            finally:
                os.environ.pop("EU4_V1_MARKER", None)
                subprocess.run(
                    ["git", "-C", str(ROOT), "worktree", "remove", "--force",
                     str(tmp / "run" / "requalification" / m1)],
                    check=False, capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", str(ROOT), "worktree", "remove", "--force",
                     str(worktree)],
                    check=False, capture_output=True,
                )

    def _run_negative(self, tmp: Path, m0: str, m1: str, worktree: Path,
                      capture: Path, *, state_overrides: dict | None = None,
                      capture_corrupt: bool = False) -> dict:
        if capture_corrupt:
            (capture / "payloads" / "4-4.bin").write_bytes(b"\x00" * 16)
        state = _state(m0, m1, worktree, capture)
        if state_overrides:
            state.update(state_overrides)
        workflow = json.loads(
            (ROOT / "campaigns/eu4-l32b-complete/workflow.json").read_text()
        )
        hooks = L32bHooks(repo=ROOT, workflow=workflow, run_root=tmp / "run")
        (capture / "capture-request.json").write_text(
            json.dumps({"library_sha256": "0" * 64}) + "\n",
            encoding="utf-8",
        )
        first = hooks._post_integration(Path(m1), state)
        if first.get("status") == "pending_capture":
            (capture / "capture-request.json").write_text(
                json.dumps({
                    "library_sha256": first["observer_library_sha256"]
                }) + "\n",
                encoding="utf-8",
            )
            return hooks._post_integration(Path(m1), state)
        return first

    def test_tampered_v1_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            m0, m1, worktree = _make_m1(tmp)
            try:
                capture = _capture(tmp)
                v1 = worktree / "automation" / "gfx_contracts" / "v1.py"
                old_sha = _sha(v1)
                v1.write_text(v1.read_text() + "\n# tampered\n", encoding="utf-8")
                subprocess.run(
                    ["git", "-C", str(worktree), "add",
                     "automation/gfx_contracts/v1.py"],
                    check=True, capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", str(worktree), "-c", "user.name=A7",
                     "-c", "user.email=a7@local", "commit", "-m", "tamper"],
                    check=True, capture_output=True,
                )
                m2 = _git(worktree, "rev-parse", "HEAD")
                result = self._run_negative(
                    tmp, m0, m2, worktree, capture,
                    state_overrides={"contract_sha256": old_sha},
                )
                self.assertEqual(result.get("status"), "failed", result)
                self.assertIn("qualified contract module differs",
                              str(result.get("detail")))
            finally:
                subprocess.run(
                    ["git", "-C", str(ROOT), "worktree", "remove", "--force",
                     str(worktree)], check=False, capture_output=True,
                )

    def test_extra_contract_file_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            m0, m1, worktree = _make_m1(tmp)
            try:
                capture = _capture(tmp)
                extra = worktree / "automation" / "gfx_contracts" / "v1_extra.py"
                extra.write_text("VALIDATORS = {}\n", encoding="utf-8")
                subprocess.run(
                    ["git", "-C", str(worktree), "add",
                     "automation/gfx_contracts/v1_extra.py"],
                    check=True, capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", str(worktree), "-c", "user.name=A7",
                     "-c", "user.email=a7@local", "commit", "-m", "extra"],
                    check=True, capture_output=True,
                )
                m2 = _git(worktree, "rev-parse", "HEAD")
                result = self._run_negative(tmp, m0, m2, worktree, capture)
                self.assertEqual(result.get("status"), "failed", result)
                self.assertIn("outside the allowlist", str(result.get("detail")))
            finally:
                subprocess.run(
                    ["git", "-C", str(ROOT), "worktree", "remove", "--force",
                     str(worktree)], check=False, capture_output=True,
                )

    def test_modified_v0_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            m0, m1, worktree = _make_m1(tmp)
            try:
                capture = _capture(tmp)
                v0 = worktree / "automation" / "gfx_contracts" / "v0.py"
                v0.write_text(v0.read_text() + "\n# tampered\n", encoding="utf-8")
                subprocess.run(
                    ["git", "-C", str(worktree), "add",
                     "automation/gfx_contracts/v0.py"],
                    check=True, capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", str(worktree), "-c", "user.name=A7",
                     "-c", "user.email=a7@local", "commit", "-m", "v0"],
                    check=True, capture_output=True,
                )
                m2 = _git(worktree, "rev-parse", "HEAD")
                result = self._run_negative(tmp, m0, m2, worktree, capture)
                self.assertEqual(result.get("status"), "failed", result)
                self.assertIn("trusted checker modified", str(result.get("detail")))
            finally:
                subprocess.run(
                    ["git", "-C", str(ROOT), "worktree", "remove", "--force",
                     str(worktree)], check=False, capture_output=True,
                )

    def test_incorrect_contract_version_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            m0, m1, worktree = _make_m1(tmp)
            try:
                capture = _capture(tmp)
                result = self._run_negative(
                    tmp, m0, m1, worktree, capture,
                    state_overrides={"contract_version": "v2"},
                )
                self.assertEqual(result.get("status"), "failed", result)
            finally:
                subprocess.run(
                    ["git", "-C", str(ROOT), "worktree", "remove", "--force",
                     str(worktree)], check=False, capture_output=True,
                )

    def test_corrupt_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            m0, m1, worktree = _make_m1(tmp)
            try:
                capture = _capture(tmp)
                result = self._run_negative(
                    tmp, m0, m1, worktree, capture, capture_corrupt=True
                )
                self.assertEqual(result.get("status"), "failed", result)
            finally:
                subprocess.run(
                    ["git", "-C", str(ROOT), "worktree", "remove", "--force",
                     str(worktree)], check=False, capture_output=True,
                )

    def test_v1_rejects_a_mismatched_shader_header(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            m0, m1, worktree = _make_m1(tmp)
            try:
                capture = _capture(tmp, header=0x9999)
                state = _state(m0, m1, worktree, capture)
                workflow = json.loads(
                    (ROOT / "campaigns/eu4-l32b-complete/workflow.json").read_text()
                )
                hooks = L32bHooks(repo=ROOT, workflow=workflow, run_root=tmp / "run")
                (capture / "capture-request.json").write_text(
                    json.dumps({"library_sha256": "0" * 64}) + "\n",
                    encoding="utf-8",
                )
                first = hooks._post_integration(Path(m1), state)
                self.assertEqual(first.get("status"), "pending_capture", first)
                (capture / "capture-request.json").write_text(
                    json.dumps({
                        "library_sha256": first["observer_library_sha256"]
                    }) + "\n",
                    encoding="utf-8",
                )
                result = hooks._post_integration(Path(m1), state)
                self.assertEqual(result.get("status"), "failed", result)
                self.assertEqual(result.get("checker"), "fail", result)
            finally:
                subprocess.run(
                    ["git", "-C", str(ROOT), "worktree", "remove", "--force",
                     str(tmp / "run" / "requalification" / m1)],
                    check=False, capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", str(ROOT), "worktree", "remove", "--force",
                     str(worktree)],
                    check=False, capture_output=True,
                )


if __name__ == "__main__":
    unittest.main()
