#!/usr/bin/env python3
"""P4.10 B: _GfxSetConstantBuffer call-through capture into the A2 transcript."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from oracle.function import fixture
from reverse_engineering import paths
from reverse_engineering.macho_rva import (
    MachOMapper,
    assert_pinned_executable,
)

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
TESTS = ROOT / "tests"


class GfxSetConstantBufferCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if platform.system() != "Darwin":
            raise unittest.SkipTest("requires macOS")
        cls.out = BENCH / ".build" / "gfx_set_constant_buffer_capture_harness"
        cls.out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra",
                "-Werror", "-mno-avx", "-I", str(BENCH), "-I", str(TESTS),
                "-o", str(cls.out),
                str(BENCH / "eu4_gfx_capture_detour.c"),
                str(BENCH / "eu4_gfx_capture_gateway.S"),
                str(BENCH / "eu4_gfx_set_constant_buffer_capture.c"),
                str(TESTS / "gfx_capture_observation.S"),
                str(TESTS / "gfx_set_constant_buffer_contract_stub.S"),
                str(TESTS / "gfx_set_constant_buffer_capture_harness.c"),
            ],
            check=True,
        )

    def _run(self, *args: str, prefix: Path | None = None) -> subprocess.CompletedProcess[str]:
        runner = [str(self.out), *args]
        if platform.machine() != "x86_64":
            runner = ["arch", "-x86_64", *runner]
        env = os.environ.copy()
        if prefix is not None:
            env["EU4_GFX_CAPTURE_PATH"] = str(prefix)
        return subprocess.run(runner, capture_output=True, text=True,
                              env=env, check=False)

    def _capture(self, prefix: Path) -> str:
        return Path(f"{prefix}.0").read_text(encoding="utf-8")

    def test_contract_stub_scenarios_close(self) -> None:
        descriptor = fixture.load_descriptor()
        policy = fixture.capture_policy_digest(descriptor)
        scenarios = {
            "s03": (2, 0xDEADBEEF00000002, 0xFEEDFACEFFFFFFFF),
            "s10": (3, 0xDEADBEEF00000003, 0xFEEDFACE0000000D),
        }
        for scenario, (slot, rcx, r9) in scenarios.items():
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                prefix = Path(tmp) / "capture"
                result = self._run("hook", "contract-stub", scenario,
                                   prefix=prefix)
                self.assertEqual(result.returncode, 0,
                                 msg=result.stderr or result.stdout)
                self.assertIn(
                    f"PRB-RECEIPT subject=contract-stub scenario={scenario} "
                    "synthetic=1 refusals=0",
                    result.stdout)

                text = self._capture(prefix)
                parsed = fixture.parse_capture(text)
                capture = parsed["capture"]
                self.assertTrue(capture["synthetic"])
                self.assertEqual(capture["function"], "gfx:set-constant-buffer")
                self.assertEqual(capture["scenario"],
                                 "s03:length-from-buffer" if scenario == "s03"
                                 else "s10:explicit-length")
                self.assertEqual(capture["module"], descriptor["binary"]["sha256"])
                self.assertEqual(capture["policy"], policy)
                self.assertEqual(capture["thread"], 0)
                self.assertEqual(capture["sequence"], 1)
                self.assertEqual(parsed["carriers"]["RCX"]["value"], rcx)
                self.assertEqual(parsed["carriers"]["R9"]["value"], r9)

                materialized = fixture.materialize_capture(text, descriptor)
                self.assertEqual(materialized["manifest"]["verdict"], "qualified")
                self.assertIs(materialized["record"]["raw"]["synthetic"], True)
                coverage = materialized["manifest"]["coverage"]
                if scenario == "s03":
                    self.assertIn("read:buffer", coverage)
                else:
                    self.assertNotIn("read:buffer", coverage)

                regions = {(region["phase"], region["name"]): region["bytes"]
                           for region in parsed["regions"]}
                post_ctx = regions[("post", "ctx")]
                word_start = 0xC8 + slot * 16 - 200
                pointer_start = 0xD0 + slot * 16 - 200
                self.assertEqual(int.from_bytes(post_ctx[word_start:word_start + 4],
                                                "little"), 4)
                self.assertEqual(int.from_bytes(
                    post_ctx[pointer_start:pointer_start + 8], "little"),
                    parsed["carriers"]["RSI"]["value"])
                self.assertEqual(post_ctx[0x128 - 200], 1)

    def test_explicit_path_does_not_read_prot_none_buffer(self) -> None:
        descriptor = fixture.load_descriptor()
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "capture"
            result = self._run("hook", "contract-stub", "s10", prefix=prefix)
            self.assertEqual(result.returncode, 0,
                             msg=result.stderr or result.stdout)
            materialized = fixture.materialize_capture(
                self._capture(prefix), descriptor)
            self.assertEqual(materialized["manifest"]["verdict"], "qualified")
            self.assertNotIn("read:buffer", materialized["manifest"]["coverage"])

    def test_original_code_window_closes(self) -> None:
        executable = paths.executable_path()
        if not executable.is_file():
            self.skipTest("pinned EU IV executable is not present")
        assert_pinned_executable(executable)

        target = paths.load_target()
        window = paths.code_window("_GfxSetConstantBuffer")
        image_base = int(target["image_base"])
        entry = int(window["entry_rva"], 16)
        end = int(window["end_rva"], 16)
        with MachOMapper(executable) as mapper:
            code = mapper.read_bytes_at_rva(image_base, entry, end - entry)
        self.assertEqual(hashlib.sha256(code).hexdigest(), window["sha256"])

        descriptor = fixture.load_descriptor()
        with tempfile.TemporaryDirectory() as tmp:
            code_path = Path(tmp) / "code.bin"
            code_path.write_bytes(code)
            for scenario in ("s03", "s10"):
                with self.subTest(scenario=scenario):
                    prefix = Path(tmp) / f"capture-{scenario}"
                    result = self._run(
                        "hook", "original-code-window", scenario,
                        str(code_path), prefix=prefix)
                    self.assertEqual(result.returncode, 0,
                                     msg=result.stderr or result.stdout)
                    self.assertIn(
                        f"PRB-RECEIPT subject=original-code-window "
                        f"scenario={scenario} synthetic=1 refusals=0",
                        result.stdout)
                    materialized = fixture.materialize_capture(
                        self._capture(prefix), descriptor)
                    self.assertEqual(materialized["manifest"]["verdict"],
                                     "qualified")
                    self.assertIs(
                        materialized["record"]["raw"]["synthetic"], True)
                    if scenario == "s10":
                        self.assertNotIn(
                            "read:buffer",
                            materialized["manifest"]["coverage"])

    def test_tls_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "capture"
            result = self._run("tls", prefix=prefix)
            self.assertEqual(result.returncode, 0,
                             msg=result.stderr or result.stdout)
            self.assertIn("PRB-TLS synthetic=1", result.stdout)
            captures = {}
            for thread in (0, 1):
                path = Path(f"{prefix}.{thread}")
                self.assertTrue(path.is_file(), msg=f"missing {path}")
                parsed = fixture.parse_capture(path.read_text(encoding="utf-8"))
                self.assertEqual(parsed["capture"]["thread"], thread)
                self.assertEqual(parsed["capture"]["sequence"], 1)
                self.assertEqual(parsed["capture"]["scenario"],
                                 "s10:explicit-length")
                captures[thread] = parsed
            rdi0 = captures[0]["carriers"]["RDI"]["value"]
            rdi1 = captures[1]["carriers"]["RDI"]["value"]
            self.assertNotEqual(rdi0, rdi1)
            ctx0 = next(region["address"] for region in captures[0]["regions"]
                        if region["phase"] == "pre" and region["name"] == "ctx")
            ctx1 = next(region["address"] for region in captures[1]["regions"]
                        if region["phase"] == "pre" and region["name"] == "ctx")
            self.assertNotEqual(ctx0, ctx1)
            handle0 = next(region["bytes"] for region in captures[0]["regions"]
                           if region["phase"] == "pre" and region["name"] == "handle")
            handle1 = next(region["bytes"] for region in captures[1]["regions"]
                           if region["phase"] == "pre" and region["name"] == "handle")
            self.assertEqual(int.from_bytes(handle0, "little"), ctx0 - 200)
            self.assertEqual(int.from_bytes(handle1, "little"), ctx1 - 200)

    def test_nested_refusal_keeps_outer_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "capture"
            result = self._run("busy", prefix=prefix)
            self.assertEqual(result.returncode, 0,
                             msg=result.stderr or result.stdout)
            match = re.search(
                r"PRB-BUSY accepted_handle=(0x[0-9a-f]+) "
                r"refused_handle=(0x[0-9a-f]+) "
                r"accepted_ctx=(0x[0-9a-f]+) "
                r"refused_ctx=(0x[0-9a-f]+) refusals=([0-9]+)",
                result.stdout)
            self.assertIsNotNone(match, msg=result.stdout)
            accepted_handle = int(match.group(1), 16)
            refused_handle = int(match.group(2), 16)
            accepted_ctx = int(match.group(3), 16)
            refused_ctx = int(match.group(4), 16)
            self.assertEqual(int(match.group(5)), 1)

            parsed = fixture.parse_capture(self._capture(prefix))
            self.assertEqual(parsed["capture"]["sequence"], 1)
            self.assertEqual(parsed["carriers"]["RDI"]["value"], accepted_handle)
            self.assertNotEqual(parsed["carriers"]["RDI"]["value"], refused_handle)
            ctx_region = next(region for region in parsed["regions"]
                              if region["phase"] == "pre" and region["name"] == "ctx")
            self.assertEqual(ctx_region["address"], accepted_ctx + 200)
            self.assertNotEqual(ctx_region["address"], refused_ctx + 200)
            self.assertEqual(parsed["outcome"]["value"], accepted_ctx)
            self.assertFalse(Path(f"{prefix}.1").exists())

    def test_null_refusal_creates_no_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "capture"
            result = self._run("refusal", prefix=prefix)
            self.assertEqual(result.returncode, 0,
                             msg=result.stderr or result.stdout)
            self.assertIn("PRB-REFUSAL synthetic=1 refusals=3 flush=0",
                          result.stdout)
            self.assertFalse(Path(f"{prefix}.0").exists())

    def test_unregistered_thread_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "capture"
            result = self._run("unregistered", prefix=prefix)
            self.assertEqual(result.returncode, 0,
                             msg=result.stderr or result.stdout)
            self.assertIn("PRB-UNREGISTERED synthetic=1 refusals=1 flush=0",
                          result.stdout)
            self.assertFalse(Path(f"{prefix}.0").exists())

    def test_source_encoding_and_pins(self) -> None:
        installer = (BENCH / "eu4_gfx_capture_detour.c").read_text()
        gateway = (BENCH / "eu4_gfx_capture_gateway.S").read_text()
        adapter = (BENCH / "eu4_gfx_set_constant_buffer_capture.c").read_text()
        descriptor = fixture.load_descriptor()
        self.assertIn("0xe9", installer)
        self.assertIn("memset(patch + 5, 0x90, 8)", installer)
        self.assertIn("eu4_gfx_capture_rel32_fits", installer)
        self.assertNotIn("0x49", installer)
        self.assertNotIn("0xbb", installer)
        self.assertIn("callq _eu4_gfx_set_constant_buffer_capture_entry", gateway)
        self.assertIn("callq _eu4_gfx_set_constant_buffer_capture_exit", gateway)
        self.assertIn("callq *_eu4_gfx_capture_trampoline_ptr(%rip)", gateway)
        self.assertIn("OBSERVATION_LENGTH 97u", adapter)
        self.assertIn("CANARY_AFTER_START 297u", adapter)
        self.assertIn("synthetic=1", adapter)
        self.assertNotIn("synthetic=0", adapter)
        self.assertIn("EU4_GFX_CAPTURE_PATH", adapter)
        self.assertIn("eu4_gfx_set_constant_buffer_capture_thread_init",
                      adapter)
        self.assertIn("eu4_gfx_set_constant_buffer_capture_flush", adapter)
        self.assertIn("eu4_gfx_set_constant_buffer_capture_refusals", adapter)
        callbacks = adapter[
            adapter.index("void eu4_gfx_set_constant_buffer_capture_entry"):
            adapter.index("bool eu4_gfx_set_constant_buffer_capture_flush")]
        for forbidden in ("fopen", "fprintf", "getenv", "write_capture",
                          "atomic_fetch_add"):
            self.assertNotIn(forbidden, callbacks)
        self.assertIn("if (!thread_ready)", callbacks)
        self.assertIn(descriptor["binary"]["sha256"], adapter)
        self.assertIn(fixture.capture_policy_digest(descriptor), adapter)


if __name__ == "__main__":
    unittest.main()
