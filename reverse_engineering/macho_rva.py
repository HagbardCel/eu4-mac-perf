"""Map Mach-O virtual addresses to file-backed bytes (x86_64 thin binaries only)."""

from __future__ import annotations

import mmap
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reverse_engineering.corpus import sha256_file
from reverse_engineering.paths import ReError, load_target

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_X86_64 = 0x01000007
LC_SEGMENT_64 = 0x19
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF

MACH_HEADER_64_SIZE = 32
SEGMENT_COMMAND_64_SIZE = 72


@dataclass(frozen=True)
class _Segment:
    vmaddr: int
    filesize: int
    fileoff: int


class MachOMapper:
    """Parse segment table once; read file-backed bytes at VA = image_base + RVA."""

    def __init__(self, binary_path: Path) -> None:
        self._path = binary_path
        self._file = None
        self._mmap: mmap.mmap | None = None
        try:
            try:
                self._file = binary_path.open("rb")
            except OSError as exc:
                raise ReError(f"binary_io_error: {exc}") from exc
            try:
                self._mmap = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            except OSError as exc:
                raise ReError(f"binary_io_error: {exc}") from exc
            except ValueError as exc:
                raise ReError(f"unsupported_macho: {exc}") from exc
            if len(self._mmap) == 0:
                raise ReError("unsupported_macho: empty file")
            self._segments = self._parse_segments()
        except ReError:
            self._release()
            raise

    def _release(self) -> None:
        if self._mmap is not None:
            self._mmap.close()
            self._mmap = None
        if self._file is not None:
            self._file.close()
            self._file = None

    def close(self) -> None:
        self._release()

    def __enter__(self) -> MachOMapper:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def primary_vmaddr(self) -> int:
        """Lowest vmaddr among file-backed segments (for fixture tests)."""
        return min(seg.vmaddr for seg in self._segments)

    def _parse_segments(self) -> list[_Segment]:
        if self._mmap is None:
            raise ReError("unsupported_macho: mapper not initialized")
        data = self._mmap
        if len(data) < MACH_HEADER_64_SIZE:
            raise ReError("unsupported_macho: file too small for Mach-O header")
        magic, _cputype, _cpusubtype, _filetype, ncmds, sizeofcmds, _flags, _reserved = struct.unpack_from(
            "<IIIIIIII", data, 0
        )
        if magic in (FAT_MAGIC, FAT_MAGIC_64):
            raise ReError("unsupported_macho: fat Mach-O not supported")
        if magic != MH_MAGIC_64:
            raise ReError("unsupported_macho: expected 64-bit little-endian Mach-O")
        if _cputype != CPU_TYPE_X86_64:
            raise ReError("unsupported_macho: expected x86_64 cputype")
        header_end = MACH_HEADER_64_SIZE + sizeofcmds
        if header_end > len(data):
            raise ReError("unsupported_macho: sizeofcmds exceeds file size")
        offset = MACH_HEADER_64_SIZE
        segments: list[_Segment] = []
        for _ in range(ncmds):
            if offset + 8 > header_end:
                raise ReError("unsupported_macho: load command header outside sizeofcmds")
            cmd, cmdsize = struct.unpack_from("<II", data, offset)
            if cmdsize < 8 or offset + cmdsize > header_end:
                raise ReError("unsupported_macho: load command exceeds sizeofcmds")
            if cmd == LC_SEGMENT_64:
                if cmdsize < SEGMENT_COMMAND_64_SIZE:
                    raise ReError("unsupported_macho: LC_SEGMENT_64 too small")
                vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, offset + 24)
                if filesize > 0:
                    if filesize > vmsize:
                        raise ReError("unsupported_macho: segment filesize exceeds vmsize")
                    if fileoff + filesize > len(data):
                        raise ReError("unsupported_macho: segment extends past file end")
                    segments.append(_Segment(vmaddr=vmaddr, filesize=filesize, fileoff=fileoff))
            offset += cmdsize
        if offset != header_end:
            raise ReError("unsupported_macho: ncmds/sizeofcmds mismatch")
        if not segments:
            raise ReError("unsupported_macho: no file-backed segments")
        return segments

    def read_bytes_at_va(self, va: int, length: int) -> bytes:
        if length <= 0:
            raise ReError("unmapped_or_non_file_backed: invalid length")
        end = va + length
        matches = [
            seg
            for seg in self._segments
            if seg.vmaddr <= va and end <= seg.vmaddr + seg.filesize
        ]
        if not matches:
            raise ReError("unmapped_or_non_file_backed: address not in file-backed segment range")
        if len(matches) > 1:
            raise ReError("unmapped_or_non_file_backed: ambiguous overlapping segment mapping")
        seg = matches[0]
        file_offset = seg.fileoff + (va - seg.vmaddr)
        if self._mmap is None:
            raise ReError("unmapped_or_non_file_backed: mapper closed")
        return self._mmap[file_offset : file_offset + length]

    def read_bytes_at_rva(self, image_base: int, rva: int, length: int) -> bytes:
        return self.read_bytes_at_va(image_base + rva, length)


def assert_pinned_executable(binary_path: Path) -> None:
    target = load_target()
    digest = sha256_file(binary_path)
    if digest != target["sha256"]:
        raise ReError("binary_identity_mismatch: executable SHA-256 does not match target.json")


def assert_image_base_matches_target(image_base: int) -> None:
    target = load_target()
    expected = int(target["image_base"])
    if int(image_base) != expected:
        raise ReError(
            f"image_base_mismatch: Ghidra metadata {image_base} vs target.json {expected}"
        )


def verify_instruction_bytes(
    mapper: MachOMapper,
    *,
    image_base: int,
    instruction_rva: str,
    length: int,
    observed_hex: str,
) -> None:
    from reverse_engineering.intervals import parse_rva

    rva = parse_rva(instruction_rva)
    expected = mapper.read_bytes_at_rva(image_base, rva, length)
    expected_hex = expected.hex()
    observed = observed_hex.lower()
    if expected_hex != observed:
        raise ByteMismatchError(
            instruction_rva=instruction_rva,
            expected_hex=expected_hex,
            observed_hex=observed,
        )


class ByteMismatchError(Exception):
    def __init__(self, *, instruction_rva: str, expected_hex: str, observed_hex: str) -> None:
        self.instruction_rva = instruction_rva
        self.expected_hex = expected_hex
        self.observed_hex = observed_hex
        super().__init__(instruction_rva)


def verify_static_row_bytes(
    mapper: MachOMapper,
    row: dict[str, Any],
    image_base: int,
) -> tuple[int, int]:
    """Return (instruction_count, byte_count) verified for a static success row."""
    if row.get("status") != "success":
        return 0, 0
    for ins in row.get("instructions") or []:
        length = int(ins.get("length", 0))
        observed_hex = ins.get("bytes_hex") or ""
        if length <= 0:
            raise ReError(f"unmapped_or_non_file_backed: invalid length at {ins.get('instruction_rva')}")
        try:
            verify_instruction_bytes(
                mapper,
                image_base=image_base,
                instruction_rva=ins["instruction_rva"],
                length=length,
                observed_hex=observed_hex,
            )
        except ByteMismatchError:
            raise
        except ReError:
            raise
    count = len(row.get("instructions") or [])
    nbytes = sum(int(ins.get("length", 0)) for ins in row.get("instructions") or [])
    return count, nbytes


def build_synthetic_macho_64(
    *,
    image_base: int,
    segment_fileoff: int,
    code_bytes: bytes,
    vmaddr: int | None = None,
    vmsize_override: int | None = None,
) -> bytes:
    """Build a minimal thin x86_64 Mach-O for unit tests (one file-backed __TEXT segment)."""
    vmaddr = vmaddr if vmaddr is not None else image_base
    filesize = len(code_bytes)
    vmsize = vmsize_override if vmsize_override is not None else filesize + 0x1000
    ncmds = 1
    sizeofcmds = SEGMENT_COMMAND_64_SIZE
    header = struct.pack(
        "<IIIIIIII",
        MH_MAGIC_64,
        CPU_TYPE_X86_64,
        0x80000003,
        0x02,
        ncmds,
        sizeofcmds,
        0,
        0,
    )
    segname = b"__TEXT" + b"\x00" * 10
    segment = struct.pack("<II", LC_SEGMENT_64, SEGMENT_COMMAND_64_SIZE)
    segment += segname
    segment += struct.pack(
        "<QQQQIIII",
        vmaddr,
        vmsize,
        segment_fileoff,
        filesize,
        7,
        7,
        0,
        0,
    )
    prefix = header + segment
    if segment_fileoff < len(prefix):
        raise ValueError("segment_fileoff must be >= header + load command size")
    out = bytearray(segment_fileoff)
    out[0 : len(prefix)] = prefix
    out[segment_fileoff : segment_fileoff + filesize] = code_bytes
    return bytes(out)


def build_macho_load_command_overflow(*, padding_after_header_end: int = 128) -> bytes:
    """Mach-O with declared sizeofcmds smaller than embedded second load command."""
    sizeofcmds = SEGMENT_COMMAND_64_SIZE
    fileoff = 0x100
    header = struct.pack(
        "<IIIIIIII",
        MH_MAGIC_64,
        CPU_TYPE_X86_64,
        0x80000003,
        0x02,
        2,
        sizeofcmds,
        0,
        0,
    )
    seg1 = _segment_command(vmaddr=0x100000000, vmsize=0x1000, fileoff=fileoff, filesize=4)
    seg2 = _segment_command(vmaddr=0x200000000, vmsize=0x1000, fileoff=fileoff + 8, filesize=4)
    header_end = MACH_HEADER_64_SIZE + sizeofcmds
    total = max(header_end + padding_after_header_end, fileoff + 16)
    out = bytearray(total)
    out[0:MACH_HEADER_64_SIZE] = header
    out[MACH_HEADER_64_SIZE : MACH_HEADER_64_SIZE + SEGMENT_COMMAND_64_SIZE] = seg1
    out[header_end : header_end + SEGMENT_COMMAND_64_SIZE] = seg2
    out[fileoff : fileoff + 4] = b"\xcc\xcc\xcc\xcc"
    return bytes(out)


def _segment_command(*, vmaddr: int, vmsize: int, fileoff: int, filesize: int) -> bytes:
    segname = b"__TEXT" + b"\x00" * 10
    segment = struct.pack("<II", LC_SEGMENT_64, SEGMENT_COMMAND_64_SIZE)
    segment += segname
    segment += struct.pack(
        "<QQQQIIII",
        vmaddr,
        vmsize,
        fileoff,
        filesize,
        7,
        7,
        0,
        0,
    )
    return segment
