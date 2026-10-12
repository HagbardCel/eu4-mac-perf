import subprocess
import sys
import unittest
from pathlib import Path

from r0_crc64 import crc64_ecma


class R0Crc64Tests(unittest.TestCase):
    def test_empty_and_known_answer(self) -> None:
        self.assertEqual(crc64_ecma(b""), 0)
        self.assertEqual(crc64_ecma(b"123456789"), 0x2B9C7EE4E2780C8A)

    @unittest.skipUnless(sys.platform == "darwin", "C CRC parity links OpenGL readback.c")
    def test_matches_c_implementation(self) -> None:
        benchmark = Path(__file__).resolve().parents[1] / "benchmark"
        build_dir = benchmark / ".build"
        build_dir.mkdir(exist_ok=True)
        harness = build_dir / "crc64_smoke.c"
        harness.write_text(
            '#include <stdio.h>\n#include <stdint.h>\n#include "eu4_r0_readback.h"\n'
            "int main(void){const uint8_t d[]={1,2,3,4};"
            'printf("%llu\\n",(unsigned long long)eu4_r0_crc64_ecma(d,sizeof d));return 0;}\n'
        )
        binary = build_dir / "crc64_smoke"
        subprocess.run(
            [
                "clang",
                "-O2",
                "-I",
                str(benchmark),
                "-o",
                str(binary),
                str(harness),
                str(benchmark / "eu4_r0_readback.c"),
                "-framework",
                "OpenGL",
            ],
            check=True,
        )
        output = subprocess.run([str(binary)], capture_output=True, text=True, check=True)
        expected = int(output.stdout.strip())
        self.assertEqual(crc64_ecma(bytes([1, 2, 3, 4])), expected)


if __name__ == "__main__":
    unittest.main()
