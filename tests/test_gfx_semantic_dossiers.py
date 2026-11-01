"""Identity and shape checks for the semantic `_Gfx*` dossiers.

The checks do not prove that a sentence was written independently, or that a
claim matches the sealed batch. They reject a missing function, a mismatched
inventory row, and a fenced decompiler or instruction dump.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TSV = ROOT / "analysis" / "renderer-corpus" / "scope" / "v1" / "renderer-functions.tsv"
TREE = ROOT / "analysis" / "reconstruction" / "gfx-semantic" / "v1"
SUBSYSTEMS = ("init", "buffers", "shaders", "textures", "state", "draw")
HEADER_KEYS = (
    "entry_rva",
    "name",
    "instruction_count",
    "source_batch",
    "static_status",
    "decomp_status",
    "semantic_status",
    "migration_role",
)
SECTIONS = ("ABI", "Fields", "Dependencies", "Uncertainties", "Behavior")
STATUSES = {"reconstructed", "partial", "blocked"}
ROLES = {"retain-original", "replace-metal", "undetermined"}
EVIDENCE = {"success", "failed", "timeout", "skipped"}
FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
BANNED_LANG = {
    "asm",
    "nasm",
    "x86",
    "x86asm",
    "disassembly",
    "decomp",
    "ghidra",
    "pseudocode",
}
BANNED_BODY = (
    "undefined4",
    "undefined8",
    "undefined1",
    "CONCAT31",
    "CONCAT44",
    "Could not recover jumptable",
    "WARNING: Subroutine does not return",
)
INSTR_LINE = re.compile(r"^\s*(?:[0-9a-fA-F]{6,}:|(?:PUSH|MOV|CALL|RET|JMP)\b)", re.M)


def gfx_rows() -> list[dict[str, str]]:
    lines = TSV.read_text().splitlines()
    header = lines[0].split("\t")
    index = {name: pos for pos, name in enumerate(header)}
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        cols = line.split("\t")
        demangled = cols[index["name_demangled"]]
        mangled = cols[index["name_mangled"]]
        if demangled.startswith("_Gfx") or mangled.startswith("_Gfx"):
            rows.append(
                {
                    "entry_rva": cols[index["entry_rva"]],
                    "name": demangled,
                    "instruction_count": cols[index["instruction_count"]],
                }
            )
    return rows


def header(text: str) -> dict[str, str]:
    found = {}
    for key in HEADER_KEYS:
        match = re.search(rf"^{key}: `?([^`\n]+)`?\s*$", text, re.M)
        if match:
            found[key] = match.group(1).strip()
    return found


def fences(text: str) -> list[str]:
    problems = []
    for lang, body in FENCE.findall(text):
        label = lang.strip().split()[0].lower() if lang.strip() else ""
        if label in BANNED_LANG:
            problems.append(f"fenced {label or 'dump'}")
        if any(token in body for token in BANNED_BODY):
            problems.append("fenced decompiler text")
        if len(INSTR_LINE.findall(body)) > 8:
            problems.append("fenced instruction listing")
    return problems


class GfxSemanticDossierTest(unittest.TestCase):
    def test_tsv_partition_and_headers(self) -> None:
        rows = gfx_rows()
        self.assertEqual(len(rows), 92)
        by_rva = {int(row["entry_rva"], 16): row for row in rows}
        self.assertEqual(len(by_rva), 92)

        paths = sorted(TREE.glob("*/*.md"))
        self.assertEqual(len(paths), 92)
        dossiers = {}
        for path in paths:
            rva = int(header(path.read_text())["entry_rva"], 16)
            self.assertNotIn(rva, dossiers, path.name)
            dossiers[rva] = path
        self.assertEqual(set(dossiers), set(by_rva))
        parents = {path.parent.name for path in dossiers.values()}
        self.assertEqual(parents, set(SUBSYSTEMS))

        for rva, path in dossiers.items():
            text = path.read_text()
            fields = header(text)
            self.assertEqual(set(HEADER_KEYS), set(fields), path.name)
            row = by_rva[rva]
            self.assertEqual(fields["name"], row["name"])
            self.assertEqual(int(fields["instruction_count"]), int(row["instruction_count"]))
            self.assertEqual(int(fields["entry_rva"], 16), int(row["entry_rva"], 16))
            self.assertRegex(fields["source_batch"], r"^gfx-batch-\d{5}\.zip$")
            self.assertIn(fields["static_status"], {"success", "failed"})
            self.assertIn(fields["decomp_status"], EVIDENCE)
            self.assertIn(fields["semantic_status"], STATUSES)
            self.assertIn(fields["migration_role"], ROLES)
            for section in SECTIONS:
                self.assertIn(f"## {section}", text, path.name)
            self.assertIn("`static-verified`", text, path.name)
            self.assertEqual(fences(text), [], path.name)

        inventory = (TREE / "inventory.md").read_text()
        for row in rows:
            self.assertEqual(inventory.count(f"`{row['name']}`"), 1)
        for status in STATUSES:
            self.assertIn(f"`{status}`:", inventory)

        by_name = {}
        for path in dossiers.values():
            fields = header(path.read_text())
            self.assertNotIn(fields["name"], by_name, path.name)
            by_name[fields["name"]] = fields
        listed = re.findall(
            r"^\| `0x[0-9a-f]+` \| `(_Gfx[^`]+)` \| \d+ \| [a-z]+ \| `(reconstructed|partial|blocked)` \| `(retain-original|replace-metal|undetermined)` \|",
            inventory,
            re.M,
        )
        self.assertEqual(len(listed), 92)
        seen = {}
        for name, status, role in listed:
            self.assertNotIn(name, seen, name)
            seen[name] = (status, role)
            fields = by_name[name]
            self.assertEqual(fields["semantic_status"], status, name)
            self.assertEqual(fields["migration_role"], role, name)
        for key, field in (("semantic_status", STATUSES), ("migration_role", ROLES)):
            for label in field:
                count = sum(1 for fields in by_name.values() if fields[key] == label)
                self.assertRegex(inventory, rf"(?m)^- `{label}`: {count}$", label)

    def test_shared_pages_have_no_decompiler_fence(self) -> None:
        for path in TREE.glob("*.md"):
            self.assertEqual(fences(path.read_text()), [], path.name)


if __name__ == "__main__":
    unittest.main()
