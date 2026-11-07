"""Map RVAs in the pinned EU4 Mach-O image to file offsets."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
IMAGE_BASE = 0x100000000


@dataclass(frozen=True)
class Segment:
    name: str
    vmaddr: int
    vmsize: int
    fileoff: int
    filesize: int


def load_segments(data: bytes) -> list[Segment]:
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != MH_MAGIC_64:
        raise ValueError("not a little-endian Mach-O 64-bit image")
    ncmds = struct.unpack_from("<I", data, 16)[0]
    offset = 32
    segments: list[Segment] = []
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, offset)
        if cmd == LC_SEGMENT_64:
            name = data[offset + 8 : offset + 24].split(b"\0", 1)[0].decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from(
                "<QQQQ", data, offset + 24
            )
            segments.append(Segment(name, vmaddr, vmsize, fileoff, filesize))
        offset += cmdsize
    return segments


def rva_to_offset(segments: list[Segment], rva: int, *, image_base: int = IMAGE_BASE) -> int:
    addr = image_base + rva
    for segment in segments:
        if segment.vmaddr <= addr < segment.vmaddr + segment.vmsize:
            delta = addr - segment.vmaddr
            if delta >= segment.filesize:
                raise ValueError(f"RVA 0x{rva:x} falls in zero-fill of {segment.name}")
            return segment.fileoff + delta
    raise ValueError(f"RVA 0x{rva:x} not in any segment")


def read_rva(path: Path, rva: int, size: int, *, image_base: int = IMAGE_BASE) -> bytes:
    data = path.read_bytes()
    offset = rva_to_offset(load_segments(data), rva, image_base=image_base)
    return data[offset : offset + size]
