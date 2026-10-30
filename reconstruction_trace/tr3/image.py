"""Hash one Mach-O on disk and one RVA window inside it.

A match is about that file. It does not authenticate the bytes of a later
running process. The live step has to read the loaded image before any patch.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from reverse_engineering.corpus import sha256_file
from reverse_engineering.macho_rva import MachOMapper


def check(path: Path, pin: dict) -> str | None:
    """Return a mismatch reason, or None when the file and the window both match."""
    if sha256_file(path) != pin["file_sha256"]:
        return "file hash mismatch"
    length = pin["end_rva"] - pin["entry_rva"]
    with MachOMapper(path) as mapper:
        window = mapper.read_bytes_at_rva(pin["image_base"], pin["entry_rva"], length)
    if hashlib.sha256(window).hexdigest() != pin["window_sha256"]:
        return "window hash mismatch"
    return None
