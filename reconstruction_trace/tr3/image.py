"""Hash one Mach-O on disk and compare it with the bytes mapped at its real site.

The expected digests come from the caller. This module does not invent them
from the candidate file. A match still is not a running EU4 process.
"""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from reverse_engineering.corpus import sha256_file
from reverse_engineering.macho_rva import MachOMapper

_INT32_MIN = -1 << 31
_INT32_MAX = (1 << 31) - 1


def text_vmaddr(path: Path) -> int:
    """Preferred ``__TEXT`` vmaddr. This is the linked image base, not a slide."""
    data = path.read_bytes()
    magic, cputype, _subtype, _filetype, ncmds, sizeofcmds, _flags, _reserved = struct.unpack_from(
        "<IIIIIIII", data, 0)
    if magic != 0xFEEDFACF or cputype != 0x01000007:
        raise ValueError("not a thin x86-64 Mach-O")
    offset = 32
    end = 32 + sizeofcmds
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, offset)
        if cmd == 0x19 and data[offset + 8:offset + 24].split(b"\x00", 1)[0] == b"__TEXT":
            return struct.unpack_from("<Q", data, offset + 24)[0]
        if cmdsize < 8 or offset + cmdsize > end:
            break
        offset += cmdsize
    raise ValueError("Mach-O has no __TEXT segment")


def runtime_site(preferred: int, slide: int, entry_rva: int, header: int) -> int:
    """Site of an RVA after one application of the dyld slide.

    ``header`` is the loaded Mach-O header. It already includes the slide, so
    the site is not ``header + slide + entry_rva``.
    """
    if header != preferred + slide:
        raise ValueError("header is not the preferred base plus the slide")
    return preferred + slide + entry_rva


def rel32(site: int, stub: int) -> int | None:
    """Signed displacement of a 5-byte ``jmp rel32``, or None when it does not fit."""
    delta = stub - (site + 5)
    if delta < _INT32_MIN or delta > _INT32_MAX:
        return None
    return delta


def apply_rel32(buf: bytearray, site: int, stub: int) -> str | None:
    """Write a ``jmp rel32`` into ``buf``, or leave it untouched when the jump cannot reach."""
    delta = rel32(site, stub)
    if delta is None:
        return "rel32 out of range"
    struct.pack_into("<Bi", buf, 0, 0xE9, delta)
    return None


def _window(path: Path, pin: dict) -> bytes:
    length = pin["end_rva"] - pin["entry_rva"]
    with MachOMapper(path) as mapper:
        return mapper.read_bytes_at_rva(pin["image_base"], pin["entry_rva"], length)


def check(path: Path, pin: dict) -> str | None:
    """Return a mismatch reason, or None when the file and the window both match."""
    if sha256_file(path) != pin["file_sha256"]:
        return "file hash mismatch"
    window = _window(path, pin)
    if hashlib.sha256(window).hexdigest() != pin["window_sha256"]:
        return "window hash mismatch"
    return None


def check_mapped(path: Path, pin: dict, mapped: bytes) -> str | None:
    """Require the file hashes and then the bytes actually mapped at the site."""
    reason = check(path, pin)
    if reason:
        return reason
    if mapped != _window(path, pin):
        return "mapped bytes mismatch"
    return None
