"""Corpus identity checks for agent tools (fail-closed on normal paths)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from reverse_engineering.corpus import load_provenance, script_hash, verify_binary
from reverse_engineering.export_identity import inventory_complete, read_ghidra_version, validate_export_identity
from reverse_engineering.manifest import inventory_bundle_digest
from reverse_engineering.paths import ReError, binary_sha256, executable_path, ghidra_project_meta_path, load_target


def _fixture_export_mode() -> bool:
    return bool(os.environ.get("EU4_RE_EXPORT"))


def assert_corpus_consistency(exp: Path, expected_binary_sha: str | None = None) -> dict[str, Any]:
    """Expected identity + inventory/provenance consistency (graph/index tools)."""
    expected_binary_sha = expected_binary_sha or binary_sha256()
    inv = exp / "inventory"
    if not inventory_complete(inv):
        raise ReError(f"inventory bundle incomplete under {exp}")
    bundle = inventory_bundle_digest(inv)
    prov = load_provenance(exp)
    pinned = prov.get("inventory_bundle_digest")
    if pinned and pinned != bundle:
        raise ReError(
            f"inventory_bundle_digest drift: provenance has {pinned}, inventory computes {bundle}"
        )
    prov_path = exp / "provenance.json"
    if prov_path.is_file() and not pinned:
        raise ReError("provenance.json missing inventory_bundle_digest")
    return {
        "binary_sha256_expected": expected_binary_sha,
        "inventory_bundle_digest": bundle,
        "provenance": prov,
    }


def assert_authoritative_acquisition(exp: Path, binary_root: Path) -> dict[str, Any]:
    """Full checks before new Ghidra/decompile acquisition."""
    ctx = assert_corpus_consistency(exp)
    if _fixture_export_mode():
        return {**ctx, "on_disk_binary_verified": False, "fixture_mode": True}
    verify_binary()
    meta_path = ghidra_project_meta_path(binary_root)
    if not meta_path.is_file():
        raise ReError(f"missing Ghidra project metadata: {meta_path}")
    meta = json.loads(meta_path.read_text())
    ghidra_root = Path(meta.get("ghidra_root", ""))
    version = read_ghidra_version(ghidra_root) if ghidra_root.is_dir() else "unknown"
    qfe = script_hash("QueryFunctionEvidence.java")
    exp_decomp = script_hash("ExportDecompilation.java")
    prov = ctx["provenance"]
    validate_export_identity(
        exp,
        prov,
        "agent_ghidra",
        exporter_script_hash=exp_decomp,
    )
    return {
        **ctx,
        "on_disk_binary_verified": True,
        "ghidra_project": meta.get("project_name"),
        "ghidra_version": version,
        "script_hashes": {"QueryFunctionEvidence.java": qfe, "ExportDecompilation.java": exp_decomp},
    }


def assert_executable_matches_target_if_present() -> None:
    """Block acquisition when the on-disk executable was tampered with."""
    if _fixture_export_mode():
        return
    exe = executable_path()
    if not exe.is_file():
        return
    verify_binary()


def tamper_check_for_tests(exp: Path, binary_root: Path) -> None:
    """Used by tests: always enforce on-disk hash when EU4_EXE points at a file."""
    if os.environ.get("EU4_EXE"):
        target = load_target()["sha256"]
        from reverse_engineering.corpus import sha256_file

        digest = sha256_file(executable_path())
        if digest != target:
            raise ReError("executable SHA-256 mismatch")
