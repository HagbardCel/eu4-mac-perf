"""Evidence records and trusted promotion for instruction-static oracles."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

from automation.macho_map import read_rva
from automation.verification_manifest import (
    EU4_EXECUTABLE_SHA256,
    canonical_json,
    digest_text,
)

PROVENANCE_TAGS = frozenset(
    {"original_execution", "instruction_static", "reconstructed_model"}
)

# GetOpenGLPrimitiveType — Decision 0003 / ShowBasicLoadText path
PRIMITIVE_FN_RVA = 0x15EDFF7
PRIMITIVE_TABLE_RVA = 0x1A79600
PRIMITIVE_TABLE_COUNT = 8
# Expected bytes of the function prologue through the default-return path.
# cmp $8,%edi; ja; push; mov; movsxd; lea table; mov (,%rax,4); pop; ret;
# mov $4,%eax; ret
PRIMITIVE_FN_BYTES_HEX = (
    "83ff087713554889e54863c7488d0df6b548008b04815dc3b804000000c3"
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_record(
    *,
    provenance: str,
    executable_sha256: str,
    function_rva: str,
    inputs: dict,
    controlled_environment: dict,
    output: Any,
    output_hash: str,
    notes: str = "",
) -> dict:
    if provenance not in PROVENANCE_TAGS:
        raise ValueError(f"unknown provenance tag: {provenance}")
    return {
        "schema": "eu4-evidence-record/v1",
        "provenance": provenance,
        "executable_sha256": executable_sha256,
        "function_rva": function_rva,
        "inputs": inputs,
        "controlled_environment": controlled_environment,
        "output": output,
        "output_hash": output_hash,
        "notes": notes,
    }


def extract_primitive_table(
    executable: Path,
    *,
    expected_sha256: str = EU4_EXECUTABLE_SHA256,
) -> dict:
    """Independently extract GetOpenGLPrimitiveType's lookup table from the binary."""
    digest = file_sha256(executable)
    if digest != expected_sha256:
        raise ValueError(
            f"executable sha256 mismatch: expected {expected_sha256}, got {digest}"
        )
    fn = read_rva(executable, PRIMITIVE_FN_RVA, len(bytes.fromhex(PRIMITIVE_FN_BYTES_HEX)))
    if fn.hex() != PRIMITIVE_FN_BYTES_HEX:
        raise ValueError(
            "GetOpenGLPrimitiveType bytes differ from pinned instruction_static pattern: "
            f"got {fn.hex()}"
        )
    raw = read_rva(executable, PRIMITIVE_TABLE_RVA, PRIMITIVE_TABLE_COUNT * 4)
    table = list(struct.unpack(f"<{PRIMITIVE_TABLE_COUNT}I", raw))
    # Out-of-range default is mov eax,4 at the end of the function body.
    mapping = {str(i): table[i] for i in range(PRIMITIVE_TABLE_COUNT)}
    mapping["default_out_of_range"] = 4
    output_hash = digest_text(canonical_json(mapping))
    record = evidence_record(
        provenance="instruction_static",
        executable_sha256=digest,
        function_rva=f"0x{PRIMITIVE_FN_RVA:x}",
        inputs={
            "table_rva": f"0x{PRIMITIVE_TABLE_RVA:x}",
            "engine_enum_domain": list(range(PRIMITIVE_TABLE_COUNT)),
            "show_basic_load_text_enum": 4,
        },
        controlled_environment={
            "method": "macho_rva_read",
            "image_base": "0x100000000",
            "simulated_dependencies": [],
            "verified_instruction_bytes_hex": PRIMITIVE_FN_BYTES_HEX,
        },
        output={
            "table": table,
            "mapping": mapping,
            "engine_enum_4_gl_mode": table[4],
        },
        output_hash=output_hash,
        notes=(
            "ShowBasicLoadText passes engine enum 4; table[4]==5 (GL_TRIANGLE_STRIP); "
            "out-of-range returns 4."
        ),
    )
    return record


def promote_primitive_table(
    executable: Path,
    dest: Path,
    *,
    expected_sha256: str = EU4_EXECUTABLE_SHA256,
) -> dict:
    """Trusted promotion: extract, write frozen evidence, return the record."""
    record = extract_primitive_table(executable, expected_sha256=expected_sha256)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(canonical_json(record), encoding="utf-8")
    return record


def lookup_from_table(table: list[int], engine_enum: int) -> int:
    if engine_enum < 0 or engine_enum >= len(table):
        return 4
    return table[engine_enum]
