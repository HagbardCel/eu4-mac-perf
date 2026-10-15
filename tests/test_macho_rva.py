import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.intervals import format_rva
from reverse_engineering.macho_rva import (
    ByteMismatchError,
    MachOMapper,
    build_macho_load_command_overflow,
    build_synthetic_macho_64,
    verify_instruction_bytes,
    verify_static_row_bytes,
)
from reverse_engineering.paths import ReError

IMAGE_BASE = 0x100000000


class MachOMapperTests(unittest.TestCase):
    def _write_macho(self, payload: bytes) -> Path:
        path = Path(tempfile.mkstemp(suffix=".macho")[1])
        path.write_bytes(payload)
        return path

    def test_read_with_nonzero_fileoff_and_image_base(self) -> None:
        code = bytes([0x48, 0x89, 0xE5, 0xC3])
        fileoff = 0x200
        payload = build_synthetic_macho_64(
            image_base=IMAGE_BASE,
            segment_fileoff=fileoff,
            code_bytes=code,
        )
        path = self._write_macho(payload)
        try:
            with MachOMapper(path) as mapper:
                got = mapper.read_bytes_at_rva(IMAGE_BASE, 0, len(code))
            self.assertEqual(got, code)
        finally:
            path.unlink(missing_ok=True)

    def test_zero_fill_beyond_filesize_not_readable(self) -> None:
        code = bytes([0x90, 0x90])
        payload = build_synthetic_macho_64(
            image_base=IMAGE_BASE,
            segment_fileoff=0x100,
            code_bytes=code,
        )
        path = self._write_macho(payload)
        try:
            with MachOMapper(path) as mapper:
                tail_va = IMAGE_BASE + len(code)
                with self.assertRaises(ReError) as ctx:
                    mapper.read_bytes_at_va(tail_va, 1)
            self.assertIn("unmapped_or_non_file_backed", str(ctx.exception))
        finally:
            path.unlink(missing_ok=True)

    def test_unmapped_address(self) -> None:
        code = b"\xcc"
        payload = build_synthetic_macho_64(
            image_base=IMAGE_BASE,
            segment_fileoff=0x80,
            code_bytes=code,
        )
        path = self._write_macho(payload)
        try:
            with MachOMapper(path) as mapper:
                with self.assertRaises(ReError):
                    mapper.read_bytes_at_va(0x200000000, 1)
        finally:
            path.unlink(missing_ok=True)

    def test_byte_mismatch(self) -> None:
        code = bytes([0xDE, 0xAD])
        payload = build_synthetic_macho_64(
            image_base=IMAGE_BASE,
            segment_fileoff=0x80,
            code_bytes=code,
        )
        path = self._write_macho(payload)
        try:
            with MachOMapper(path) as mapper:
                with self.assertRaises(ByteMismatchError) as ctx:
                    verify_instruction_bytes(
                        mapper,
                        image_base=IMAGE_BASE,
                        instruction_rva=format_rva(0),
                        length=2,
                        observed_hex="beef",
                    )
            self.assertEqual(ctx.exception.expected_hex, "dead")
        finally:
            path.unlink(missing_ok=True)

    def test_malformed_header_rejected(self) -> None:
        path = self._write_macho(b"\x00" * 16)
        try:
            with self.assertRaises(ReError) as ctx:
                with MachOMapper(path):
                    pass
            self.assertIn("unsupported_macho", str(ctx.exception))
        finally:
            path.unlink(missing_ok=True)

    def test_fat_header_rejected(self) -> None:
        path = self._write_macho(b"\xca\xfe\xba\xbe" + b"\x00" * 28)
        try:
            with self.assertRaises(ReError) as ctx:
                with MachOMapper(path):
                    pass
            self.assertIn("unsupported_macho", str(ctx.exception))
        finally:
            path.unlink(missing_ok=True)

    def test_filesize_exceeds_vmsize_rejected(self) -> None:
        code = b"\x90\x90\x90\x90"
        payload = build_synthetic_macho_64(
            image_base=IMAGE_BASE,
            segment_fileoff=0x100,
            code_bytes=code,
            vmsize_override=2,
        )
        path = self._write_macho(payload)
        try:
            with self.assertRaises(ReError) as ctx:
                with MachOMapper(path):
                    pass
            self.assertIn("filesize exceeds vmsize", str(ctx.exception))
        finally:
            path.unlink(missing_ok=True)

    def test_load_command_beyond_sizeofcmds_rejected(self) -> None:
        payload = build_macho_load_command_overflow()
        path = self._write_macho(payload)
        try:
            with self.assertRaises(ReError) as ctx:
                with MachOMapper(path):
                    pass
            self.assertIn("sizeofcmds", str(ctx.exception))
        finally:
            path.unlink(missing_ok=True)


CALLSITE_MINI_DIR = Path(__file__).resolve().parent / "re_fixtures" / "callsite_mini"
CALLSITE_MINI_BIN = CALLSITE_MINI_DIR / "callsite_mini"


def _nm_symbol_va(binary: Path, symbol: str) -> int:
    out = subprocess.check_output(["nm", "-n", str(binary)], text=True)
    pattern = re.compile(rf"([0-9a-fA-F]+) [Tt] {re.escape(symbol)}$")
    for line in out.splitlines():
        match = pattern.search(line.strip())
        if match:
            return int(match.group(1), 16)
    raise AssertionError(f"symbol {symbol} not found in nm output")


@unittest.skipUnless(sys.platform == "darwin", "callsite_mini build requires macOS clang")
class CallsiteMiniIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not CALLSITE_MINI_BIN.is_file():
            subprocess.run(
                ["bash", str(CALLSITE_MINI_DIR / "build_callsite_mini.sh")],
                cwd=CALLSITE_MINI_DIR,
                check=True,
            )

    def test_start_bytes_match_assembly_encoding(self) -> None:
        start_va = _nm_symbol_va(CALLSITE_MINI_BIN, "_start")
        target_va = _nm_symbol_va(CALLSITE_MINI_BIN, "_target_direct")
        displacement = target_va - (start_va + 5)
        expected = (
            b"\xe8"
            + struct.pack("<i", displacement)
            + b"\xff\xd0\xff\x17\xc3"
        )
        with MachOMapper(CALLSITE_MINI_BIN) as mapper:
            image_base = mapper.primary_vmaddr
            actual = mapper.read_bytes_at_va(start_va, len(expected))
            self.assertEqual(actual, expected)
            self.assertEqual(actual[0], 0xE8)
            ins_specs = [
                (0, 5),
                (5, 2),
                (7, 2),
                (9, 1),
            ]
            instructions = []
            for offset, length in ins_specs:
                chunk = expected[offset : offset + length]
                rva = start_va + offset - image_base
                instructions.append(
                    {
                        "instruction_rva": format_rva(rva),
                        "length": length,
                        "bytes_hex": chunk.hex(),
                    }
                )
            row = {
                "entry_rva": format_rva(start_va - image_base),
                "status": "success",
                "instructions": instructions,
            }
            self.assertEqual(verify_static_row_bytes(mapper, row, image_base), (4, 10))


if __name__ == "__main__":
    unittest.main()
