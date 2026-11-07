#!/usr/bin/env python3
"""A0.1 trusted-validation checker: frozen oracle vs candidate primitive lookup."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow `python3 accept.py` without PYTHONPATH when run from campaign dir via cli.
_REPO_CANDIDATES = list(Path(__file__).resolve().parents)


def trusted_root() -> Path:
    for parent in _REPO_CANDIDATES:
        if (parent / ".git").exists() and (parent / "automation").is_dir():
            return parent
    raise SystemExit("trusted repository not found")


ROOT = trusted_root()
sys.path.insert(0, str(ROOT))

from automation.evidence import extract_primitive_table, lookup_from_table  # noqa: E402
from automation.verification_manifest import (  # noqa: E402
    EU4_EXECUTABLE_SHA256,
    digest_manifest,
    load_manifest,
    verify_manifest,
)

ORACLE = ROOT / "analysis" / "qualification" / "v0"
MANIFEST_PATH = ORACLE / "verification-manifest.json"
EVIDENCE_PATH = ORACLE / "primitive-table.evidence.json"
CANDIDATE_PATH = ORACLE / "primitive-lookup.candidate.json"
DEFAULT_EXE = Path(
    "/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4"
)


def emit(verdict: str, diagnostics: str) -> None:
    print(json.dumps({"verdict": verdict, "diagnostics": diagnostics}))


def main(argv: list[str]) -> int:
    _workspace = Path(argv[1]) if len(argv) > 1 else ROOT
    if not MANIFEST_PATH.is_file() or not EVIDENCE_PATH.is_file():
        emit(
            "unavailable",
            "frozen oracle missing; run: python3 -m automation.promote_evidence",
        )
        return 0
    if not DEFAULT_EXE.is_file():
        emit(
            "unavailable",
            f"EU4 executable not found at {DEFAULT_EXE}; "
            "original-code / instruction_static promotion requires the pinned install",
        )
        return 0

    manifest = load_manifest(MANIFEST_PATH)
    errors = verify_manifest(ROOT, manifest)
    if errors:
        emit("unavailable", "verification manifest integrity failure: " + "; ".join(errors))
        return 0
    if manifest.get("eu4_executable_sha256") != EU4_EXECUTABLE_SHA256:
        emit("unavailable", "manifest eu4_executable_sha256 does not match pin")
        return 0
    if "repo_baseline_commit" not in manifest:
        emit("unavailable", "manifest missing repo_baseline_commit")
        return 0

    # Independently re-extract and compare to frozen evidence (promotion fidelity).
    try:
        live = extract_primitive_table(DEFAULT_EXE)
    except ValueError as exc:
        emit("unavailable", f"live extraction failed: {exc}")
        return 0
    frozen = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    if frozen.get("provenance") != "instruction_static":
        emit("fail", "frozen evidence provenance must be instruction_static")
        return 0
    if live["output_hash"] != frozen.get("output_hash"):
        emit(
            "unavailable",
            "live extraction output_hash differs from frozen evidence "
            "(oracle drift or wrong executable)",
        )
        return 0
    if live["output"]["engine_enum_4_gl_mode"] != 5:
        emit("fail", "live table[4] is not GL mode 5")
        return 0

    if not CANDIDATE_PATH.is_file():
        emit("fail", f"missing candidate reconstruction: {CANDIDATE_PATH}")
        return 0
    candidate = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))
    table = candidate.get("table")
    if not isinstance(table, list) or len(table) != 8:
        emit("fail", "candidate.table must be an 8-int list matching the original table")
        return 0
    expected = frozen["output"]["table"]
    if table != expected:
        emit(
            "fail",
            "counterexample: candidate.table != frozen instruction_static table; "
            f"expected {expected}, got {table}; "
            f"engine_enum_4 -> expected {lookup_from_table(expected, 4)}, "
            f"got {lookup_from_table(table, 4)}; "
            f"oracle_manifest_sha256={digest_manifest(manifest)}",
        )
        return 0
    if candidate.get("engine_enum_4_gl_mode") != 5:
        emit(
            "fail",
            "counterexample: candidate.engine_enum_4_gl_mode must be 5 "
            f"(got {candidate.get('engine_enum_4_gl_mode')})",
        )
        return 0
    # Mutation surface: out-of-range default
    if lookup_from_table(table, 99) != 4:
        emit("fail", "candidate out-of-range mapping must return 4")
        return 0

    emit(
        "pass",
        "primitive-table candidate matches frozen instruction_static oracle; "
        f"manifest_sha256={digest_manifest(manifest)}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
