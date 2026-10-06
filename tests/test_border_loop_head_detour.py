#!/usr/bin/env python3
import platform
import signal
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
TESTS = ROOT / "tests"


class BorderLoopHeadDetourTests(unittest.TestCase):
    def _clang(self, out: Path, *sources: str, extra: list[str] | None = None) -> None:
        cmd = [
            "clang",
            "-arch",
            "x86_64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-mno-avx",
            "-I",
            str(BENCH),
            "-o",
            str(out),
            *sources,
        ]
        if extra:
            cmd.extend(extra)
        subprocess.run(cmd, check=True)

    def _run(self, out: Path, *args: str) -> subprocess.CompletedProcess[str]:
        runner = [str(out), *args]
        if platform.machine() != "x86_64":
            runner = ["arch", "-x86_64", *runner]
        return subprocess.run(runner, capture_output=True, text=True, check=False)

    def test_gateway_runtime_harness(self) -> None:
        if platform.system() != "Darwin":
            self.skipTest("requires macOS")
        out = BENCH / ".build" / "border_loop_head_detour_harness"
        self._clang(
            out,
            str(BENCH / "eu4_submission_border_loop_head_gateway.S"),
            str(TESTS / "border_loop_head_gateway_observe_stub.S"),
            str(TESTS / "border_loop_head_gateway_test_enter.S"),
            str(TESTS / "border_loop_head_gateway_continuation.S"),
            str(TESTS / "border_loop_head_detour_harness.c"),
            extra=["-I", str(TESTS)],
        )
        result = self._run(out)
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_install_harness_success_and_rollback(self) -> None:
        if platform.system() != "Darwin":
            self.skipTest("requires macOS")
        out = BENCH / ".build" / "border_loop_head_install_harness"
        self._clang(
            out,
            str(BENCH / "eu4_submission_border_loop_head_install.c"),
            str(TESTS / "border_loop_head_install_harness.c"),
        )
        for mode in ("success", "rx_fail", "fatal"):
            result = self._run(out, mode)
            self.assertEqual(result.returncode, 0, msg=f"{mode}: {result.stderr}")

    def test_install_fatal_aborts_on_install_ex(self) -> None:
        if platform.system() != "Darwin":
            self.skipTest("requires macOS")
        out = BENCH / ".build" / "border_loop_head_install_fatal_probe"
        self._clang(
            out,
            str(BENCH / "eu4_submission_border_loop_head_install.c"),
            str(TESTS / "border_loop_head_install_fatal_probe.c"),
        )
        runner = [str(out)]
        if platform.machine() != "x86_64":
            runner = ["arch", "-x86_64", *runner]
        proc = subprocess.run(runner, capture_output=True, text=True, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(proc.returncode, (-signal.SIGABRT, 134, 6))

    def test_gateway_asm_preserves_r11_push_pop(self) -> None:
        text = (BENCH / "eu4_submission_border_loop_head_gateway.S").read_text()
        self.assertIn("_g_eu4_border_observer_active", text)
        self.assertIn("cmpb $0, _g_eu4_border_observer_active", text)
        self.assertIn("active_observer", text)
        self.assertIn("pushq %r11", text)
        self.assertIn("popq %r11", text)
        self.assertIn("jne", text)
        self.assertIn("_border_loop_skip_target", text)
        self.assertIn("_border_loop_draw_target", text)
        self.assertIn("fxsave64", text)

    def test_install_uses_ff25_rip_indirect_jmp(self) -> None:
        text = (BENCH / "eu4_submission_border_loop_head_install.c").read_text()
        self.assertIn("p[0] = 0xff", text)
        self.assertIn("p[1] = 0x25", text)
        self.assertNotIn("trampoline_ptr", text)


if __name__ == "__main__":
    unittest.main()
