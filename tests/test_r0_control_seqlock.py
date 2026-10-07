import ctypes
import mmap
import tempfile
import unittest
from pathlib import Path

import r0_probe


class R0ControlSeqlockTests(unittest.TestCase):
    def test_native_publish_updates_page(self) -> None:
        r0_probe.build()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "control.bin"
            path.write_bytes(b"\x00" * 4096)
            r0_probe.write_control(path, generation=3, mode=1, scenario_id=9)
            with path.open("rb") as handle:
                with mmap.mmap(handle.fileno(), 4096, access=mmap.ACCESS_READ) as mapping:
                    seq = int.from_bytes(mapping[0:4], "little")
                    magic = int.from_bytes(mapping[4:8], "little")
                    generation = int.from_bytes(mapping[8:12], "little")
            self.assertEqual(seq % 2, 0)
            self.assertEqual(generation, 3)


if __name__ == "__main__":
    unittest.main()
