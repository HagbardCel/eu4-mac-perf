"""G1 function-package provenance checker (reviewer-owned).

Automated checks cover YAML front matter, census uniqueness, manifest pin,
required headings, and sibling ``instructions.json`` when cited. Other evidence
paths cited in the contract require independent human review.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FRONT_MATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n", re.MULTILINE | re.DOTALL)
FIELD_RE = re.compile(r"^([a-z_]+):\s*(.+)$", re.MULTILINE)
HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
PASS_FAIL = frozenset({"PASS", "FAIL"})

DEFAULT_CENSUS = (
    Path(__file__).resolve().parents[2]
    / "analysis/renderer-corpus/scope/v1/renderer-functions.jsonl"
)


def _parse_front_matter(text: str) -> dict[str, str]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        m = FIELD_RE.match(line.strip())
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def _headings(text: str) -> set[str]:
    return {m.group(1).strip().lower() for m in HEADING_RE.finditer(text)}


def _parse_census_lines(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in text.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def unique_census_row(
        rows: list[dict],
        function_mangled: str,
        entry_rva: str) -> tuple[dict | None, str | None]:
    """Return (row, problem) where problem is 'missing' or 'duplicate'."""
    matches = [
        row for row in rows
        if row.get("name_mangled") == function_mangled
        and row.get("entry_rva", "").lower() == entry_rva.lower()
    ]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, "missing"
    return None, "duplicate"


def load_census_row(
        function_mangled: str,
        entry_rva: str,
        *,
        census_path: Path | None = None) -> dict | None:
    path = census_path or DEFAULT_CENSUS
    rows = _parse_census_lines(path.read_text(encoding="utf-8"))
    row, problem = unique_census_row(rows, function_mangled, entry_rva)
    if problem:
        return None
    return row


def check_package_contract(
        path: Path,
        root: Path,
        *,
        census_path: Path | None = None) -> list[str]:
    errors: list[str] = []
    if not path.is_file():
        return ["missing contract.md"]
    text = path.read_text(encoding="utf-8")
    meta = _parse_front_matter(text)
    required = ("function_mangled", "entry_rva", "gate_verdict", "blockers")
    for key in required:
        if key not in meta:
            errors.append(f"missing front-matter field: {key}")
    if errors:
        return errors

    verdict = meta["gate_verdict"].upper()
    if verdict not in PASS_FAIL:
        errors.append("gate_verdict must be PASS or FAIL")
    if verdict == "PASS" and meta["blockers"] not in ("[]", ""):
        errors.append("PASS requires empty blockers")

    census_file = census_path or DEFAULT_CENSUS
    rows = _parse_census_lines(census_file.read_text(encoding="utf-8"))
    _, problem = unique_census_row(
        rows, meta["function_mangled"], meta["entry_rva"])
    if problem == "missing":
        errors.append("no census row matches function_mangled + entry_rva")
    elif problem == "duplicate":
        errors.append("multiple census rows match function_mangled + entry_rva")

    target = json.loads((root / "reverse_engineering/target.json").read_text())
    summary = json.loads((root / "analysis/renderer-corpus/scope/v1"
                          / "manifest-summary.json").read_text())
    if summary["binary_sha256"] != target["sha256"]:
        errors.append("census manifest binary_sha256 does not match target.json")

    for heading in ("preconditions", "postconditions", "modified state",
                    "dependencies", "unobserved"):
        if heading not in _headings(text):
            errors.append(f"missing section heading: {heading}")

    if "instructions.json" in text:
        rel = path.parent / "instructions.json"
        if not rel.is_file():
            errors.append("cites instructions.json but file is missing")
    return errors


def check_decision_record(path: Path, root: Path) -> list[str]:
    errors: list[str] = []
    if not path.is_file():
        return ["missing decision record"]
    text = path.read_text(encoding="utf-8")
    meta = _parse_front_matter(text)
    if meta.get("gate_verdict", "").upper() != "FAIL":
        errors.append("decision record must have gate_verdict: FAIL")
    if "candidates_considered" not in meta:
        errors.append("decision record must list candidates_considered")
    target = json.loads((root / "reverse_engineering/target.json").read_text())
    summary = json.loads((root / "analysis/renderer-corpus/scope/v1"
                          / "manifest-summary.json").read_text())
    if summary["binary_sha256"] != target["sha256"]:
        errors.append("census manifest binary_sha256 does not match target.json")
    return errors
