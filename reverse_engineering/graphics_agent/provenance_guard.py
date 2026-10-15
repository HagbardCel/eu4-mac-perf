"""Corpus identity checks for agent tools (fail-closed on normal paths)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from reverse_engineering.corpus import load_provenance, script_hash, verify_binary
from reverse_engineering.export_identity import inventory_complete, read_ghidra_version, validate_export_identity
from reverse_engineering.manifest import inventory_bundle_digest
from reverse_engineering.paths import ReError, binary_sha256, executable_path, ghidra_project_meta_path


def fixture_mode() -> bool:
    return os.environ.get("EU4_AGENT_FIXTURE_MODE", "").strip() in ("1", "true", "yes")


def assert_corpus_consistency(exp: Path, expected_binary_sha: str | None = None) -> dict[str, Any]:
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


def load_ghidra_identity(binary_root: Path) -> dict[str, Any]:
    meta_path = ghidra_project_meta_path(binary_root)
    if not meta_path.is_file():
        if fixture_mode():
            return {
                "ghidra_version": "fixture",
                "project_name": "fixture",
                "project_meta_hash": hashlib.sha256(b"fixture").hexdigest(),
                "program_name": "fixture",
            }
        raise ReError(f"missing Ghidra project metadata: {meta_path}")
    meta = json.loads(meta_path.read_text())
    ghidra_root = Path(meta.get("ghidra_root", ""))
    version = read_ghidra_version(ghidra_root) if ghidra_root.is_dir() else meta.get("ghidra_version", "unknown")
    return {
        "ghidra_version": version,
        "project_name": meta.get("project_name", "unknown"),
        "project_meta_hash": hashlib.sha256(meta_path.read_bytes()).hexdigest(),
        "program_name": meta.get("program_name", "unknown"),
        "project_dir": str(meta.get("project_dir", "")),
    }


def assert_ghidra_environment(exp: Path, identity: dict[str, Any]) -> None:
    prov = load_provenance(exp)
    pinned_ver = prov.get("ghidra_version")
    if pinned_ver and identity.get("ghidra_version") not in (pinned_ver, "fixture", "unknown"):
        raise ReError(
            f"ghidra_version {identity.get('ghidra_version')} conflicts with provenance pin {pinned_ver}"
        )
    pinned_proj = prov.get("ghidra_project_name")
    if pinned_proj and identity.get("project_name") not in (pinned_proj, "fixture", "unknown"):
        raise ReError("ghidra project_name conflicts with provenance")


def assert_authoritative_acquisition(exp: Path, binary_root: Path) -> dict[str, Any]:
    ctx = assert_corpus_consistency(exp)
    if not fixture_mode():
        verify_binary()
    identity = load_ghidra_identity(binary_root)
    assert_ghidra_environment(exp, identity)
    qfe = script_hash("QueryFunctionEvidence.java")
    exp_decomp = script_hash("ExportDecompilation.java")
    if not fixture_mode():
        validate_export_identity(
            exp,
            ctx["provenance"],
            "agent_ghidra",
            exporter_script_hash=exp_decomp,
        )
    return {
        **ctx,
        **identity,
        "on_disk_binary_verified": not fixture_mode(),
        "script_hashes": {"QueryFunctionEvidence.java": qfe, "ExportDecompilation.java": exp_decomp},
    }
