import tempfile
import unittest
from pathlib import Path

import r0_probe


class R0ProbeTests(unittest.TestCase):
    def test_parse_log_round_trip(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("ACK,2,10,1000\n")
            handle.write("F,10,1,0,1,1,0,64,64,0,1,99,1\n")
            path = Path(handle.name)
        parsed = r0_probe.parse_log(path)
        self.assertEqual(parsed["acks"][0]["generation"], 2)
        self.assertEqual(parsed["frames"][0]["crc64"], 99)
        path.unlink()


if __name__ == "__main__":
    unittest.main()
