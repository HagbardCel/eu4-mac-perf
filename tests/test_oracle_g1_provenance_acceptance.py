"""G1 independent provenance checker (reviewer-owned, not on G1 implementation branch).

An all-FAIL selection may use ``analysis/reconstruction/second-function-re0-decision.md``
instead of ``analysis/reconstruction/<function>/``.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CENSUS = ROOT / "analysis/renderer-corpus/scope/v1/renderer-functions.jsonl"
MANIFEST_SUMMARY = ROOT / "analysis/renderer-corpus/scope/v1/manifest-summary.json"
TARGET = ROOT / "reverse_engineering/target.json"
RECON_ROOT = ROOT / "analysis/reconstruction"

HEX_RVA = re.compile(r"\A0x[0-9a-f]+\Z", re.I)
PASS_FAIL = frozenset({"PASS", "FAIL"})


def load_census_row(name_mangled: str) -> dict | None:
    for line in CENSUS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("name_mangled") == name_mangled:
            return row
    return None


def check_package_root(package_dir: Path) -> list[str]:
    errors: list[str] = []
    contract = package_dir / "contract.md"
    if not contract.is_file():
        errors.append("missing contract.md")
        return errors
    text = contract.read_text(encoding="utf-8")
    target = json.loads(TARGET.read_text(encoding="utf-8"))
    summary = json.loads(MANIFEST_SUMMARY.read_text(encoding="utf-8"))
    if summary["binary_sha256"] != target["sha256"]:
        errors.append("census manifest binary_sha256 does not match target.json")

    m = re.search(r"name_mangled[\"']?\s*\|\s*`([^`]+)`", text)
    if not m:
        m = re.search(r"Symbol\s*\|\s*`([^`]+)`", text)
    if not m:
        errors.append("contract does not name the function symbol")
        return errors
    symbol = m.group(1).lstrip("_") if "Gfx" in m.group(1) else m.group(1)
    row = None
    for candidate in (m.group(1), symbol, f"_{symbol}"):
        row = load_census_row(candidate)
        if row:
            break
    if row is None:
        errors.append(f"{m.group(1)!r} not found in renderer census")
        return errors

    if "Entry RVA" in text or "entry_rva" in text:
        rva_match = re.search(r"`(0x[0-9a-f]+)`", text)
        if rva_match and rva_match.group(1).lower() != row["entry_rva"].lower():
            errors.append("entry RVA in contract disagrees with census")

    for section in ("precondition", "postcondition", "modified", "dependency",
                    "unobserved", "RE0", "gate"):
        if section.lower() not in text.lower():
            errors.append(f"contract missing section hint for {section}")

    verdict = re.search(r"RE0→RE1[^`]*`?(PASS|FAIL)`?", text, re.I)
    if not verdict:
        errors.append("RE0→RE1 gate verdict not recorded")
    elif verdict.group(1).upper() not in PASS_FAIL:
        errors.append("invalid gate verdict")

    blockers = re.search(r"blockers:\s*\[(.*?)\]", text, re.S | re.I)
    if verdict and verdict.group(1).upper() == "PASS" and blockers:
        inner = blockers.group(1).strip()
        if inner and inner != "":
            errors.append("PASS with non-empty blockers list")

    if re.search(r"instructions\.json", text) and not (package_dir / "instructions.json").is_file():
        errors.append("cites instructions.json but file is missing")
    return errors


class G1ProvenanceCheckerTests(unittest.TestCase):
    def test_census_binary_matches_target_pin(self):
        target = json.loads(TARGET.read_text(encoding="utf-8"))
        summary = json.loads(MANIFEST_SUMMARY.read_text(encoding="utf-8"))
        self.assertEqual(summary["binary_sha256"], target["sha256"])

    def test_all_fail_decision_path_is_allowed(self):
        decision = RECON_ROOT / "second-function-re0-decision.md"
        self.assertFalse(decision.is_file(), "fixture not committed yet")

    def test_negative_fixture_missing_address(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp)
            (pkg / "contract.md").write_text(
                "Symbol | `_TotallyFake`\nRE0→RE1 gate: `PASS`\n"
                "precondition postcondition modified dependency unobserved\n",
                encoding="utf-8",
            )
            errors = check_package_root(pkg)
            self.assertTrue(any("census" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
