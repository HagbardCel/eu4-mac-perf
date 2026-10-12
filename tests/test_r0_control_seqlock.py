import ctypes
import mmap
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import eu4_r0_control as r0_control
import r0_probe


@unittest.skipUnless(sys.platform == "darwin", "R0 control mmap requires macOS")
class R0ControlAtomicTests(unittest.TestCase):
    def test_native_publish_updates_atomic_word(self) -> None:
        r0_probe.build()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "control.bin"
            path.write_bytes(b"\x00" * r0_control.EU4_R0_CONTROL_PAGE_SIZE)
            r0_probe.write_control(path, generation=3, mode=1, scenario_id=9)
            with path.open("rb") as handle:
                with mmap.mmap(handle.fileno(), r0_control.EU4_R0_CONTROL_PAGE_SIZE, access=mmap.ACCESS_READ) as mapping:
                    word = int.from_bytes(mapping[0:8], "little")
            generation, mode, scenario_id, version, valid = r0_control.unpack_control_word(word)
            self.assertTrue(valid)
            self.assertEqual((generation, mode, scenario_id, version), (3, 1, 9, 1))

    def test_atomics_supported_on_host(self) -> None:
        r0_probe.build()
        lib = ctypes.CDLL(str(r0_probe.HOST_CONTROL_LIBRARY))
        supported = lib.eu4_r0_control_atomics_supported
        supported.restype = ctypes.c_bool
        self.assertTrue(supported())

    def test_cross_arch_reader_sees_same_word(self) -> None:
        if platform.machine() != "arm64":
            self.skipTest("cross-arch reader test expects arm64 host")
        r0_probe.build()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "control.bin"
            path.write_bytes(b"\x00" * r0_control.EU4_R0_CONTROL_PAGE_SIZE)
            r0_probe.write_control(path, generation=42, mode=0, scenario_id=7)
            script = (
                "import mmap,sys; from pathlib import Path; "
                "import eu4_r0_control as c; "
                f"p=Path('{path}'); "
                "h=p.open('rb'); m=mmap.mmap(h.fileno(), c.EU4_R0_CONTROL_PAGE_SIZE); "
                "w=int.from_bytes(m[0:8],'little'); "
                "print(c.unpack_control_word(w)[0])"
            )
            result = subprocess.run(
                ["arch", "-x86_64", sys.executable, "-c", script],
                capture_output=True,
                text=True,
                check=False,
                env={**dict(__import__('os').environ), "PYTHONPATH": str(r0_probe.BENCHMARK)},
            )
            if result.returncode:
                self.skipTest(f"x86_64 python unavailable: {result.stderr.strip()}")
            self.assertEqual(int(result.stdout.strip()), 42)


if __name__ == "__main__":
    unittest.main()
