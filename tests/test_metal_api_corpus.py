"""Offline checks for the local Metal API corpus."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "analysis" / "metal-api"
EXCLUDED = {"README.md", "index.md"}
LINK_RE = re.compile(r"\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:")


def _local_target(md: Path, raw: str) -> str | None:
    target = raw.strip().split()[0]
    if not target or target.startswith("#") or target.startswith(SKIP_PREFIXES):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target:
        return None
    return target


def check_links(root: Path, md: Path) -> list[str]:
    errors = []
    rel = md.relative_to(root)
    for line_no, line in enumerate(md.read_text(encoding="utf-8").splitlines(), start=1):
        for match in LINK_RE.finditer(line):
            target = _local_target(md, match.group(1))
            if target is None:
                continue
            resolved = (md.parent / target).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                errors.append(f"{rel}:{line_no}: outside repo: {target}")
                continue
            if not resolved.is_file():
                errors.append(f"{rel}:{line_no}: missing file: {target}")
    return errors


def check_url(url: object) -> str | None:
    if not isinstance(url, str):
        return "url is not a string"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return f"invalid url: {url}"
    return None


def check_corpus(root: Path, corpus: Path) -> list[str]:
    errors = []
    inventory_path = corpus / "sources.json"
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"sources.json: {exc}"]
    records = inventory.get("contracts")
    if not isinstance(records, list):
        return ["sources.json: contracts must be a list"]

    names = []
    for record in records:
        if not isinstance(record, dict):
            errors.append("contract record is not an object")
            continue
        name = record.get("file")
        if not isinstance(name, str):
            errors.append("contract file is missing")
            continue
        names.append(name)
        path = corpus / name
        if not path.is_file():
            errors.append(f"missing contract: {name}")
        if record.get("status") != "reviewed":
            errors.append(f"{name}: status is not reviewed")
        if not record.get("sdk_version") or not record.get("reviewed"):
            errors.append(f"{name}: reviewed record lacks sdk_version or reviewed")
        headers = record.get("headers") or []
        urls = record.get("urls") or []
        if not headers and not urls:
            errors.append(f"{name}: reviewed record has no header or url")
        for url in urls:
            problem = check_url(url)
            if problem:
                errors.append(f"{name}: {problem}")
        if path.is_file():
            errors.extend(check_links(root, path))

    expected = sorted(
        path.name for path in corpus.glob("*.md") if path.name not in EXCLUDED
    )
    if sorted(names) != expected or len(names) != len(set(names)):
        errors.append(f"contract inventory {sorted(names)} != {expected}")

    index = corpus / "index.md"
    if index.is_file():
        text = index.read_text(encoding="utf-8")
        errors.extend(check_links(root, index))
        for name in expected:
            if name not in text:
                errors.append(f"index.md does not name {name}")
    else:
        errors.append("missing index.md")

    readme = corpus / "README.md"
    if readme.is_file():
        errors.extend(check_links(root, readme))
    return errors


class MetalApiCorpusTests(unittest.TestCase):
    def test_repository_corpus(self) -> None:
        self.assertEqual(check_corpus(REPO, CORPUS), [])

    def test_http_links_are_ignored_and_escape_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "analysis" / "metal-api"
            corpus.mkdir(parents=True)
            (corpus / "README.md").write_text("no links\n", encoding="utf-8")
            (corpus / "index.md").write_text(
                "[only.md](only.md)\n", encoding="utf-8")
            (corpus / "only.md").write_text(
                "[web](https://example.com/a) [gone](../../../outside.md)\n",
                encoding="utf-8",
            )
            (corpus / "sources.json").write_text(json.dumps({
                "contracts": [{
                    "file": "only.md",
                    "status": "reviewed",
                    "sdk_version": "27.0",
                    "reviewed": "2026-11-05",
                    "headers": ["Metal.framework/Headers/MTLBuffer.h"],
                    "urls": ["https://example.com/a"],
                }]
            }), encoding="utf-8")
            errors = check_corpus(root, corpus)
            self.assertTrue(any("outside repo" in error for error in errors))
            self.assertFalse(any("example.com" in error for error in errors))

    def test_sibling_link_outside_the_corpus_directory_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "analysis" / "metal-api"
            corpus.mkdir(parents=True)
            header = root / "benchmark"
            header.mkdir()
            (header / "eu4_gfx_buffers.h").write_text("ok\n", encoding="utf-8")
            (corpus / "README.md").write_text("\n", encoding="utf-8")
            (corpus / "index.md").write_text("[buffers.md](buffers.md)\n", encoding="utf-8")
            (corpus / "buffers.md").write_text(
                "[header](../../benchmark/eu4_gfx_buffers.h)\n", encoding="utf-8")
            (corpus / "sources.json").write_text(json.dumps({
                "contracts": [{
                    "file": "buffers.md",
                    "status": "reviewed",
                    "sdk_version": "27.0",
                    "reviewed": "2026-11-05",
                    "headers": [],
                    "urls": ["https://developer.apple.com/documentation/metal/mtlbuffer"],
                }]
            }), encoding="utf-8")
            self.assertEqual(check_corpus(root, corpus), [])

    def test_invalid_url_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "index.md").write_text("note.md\n", encoding="utf-8")
            (corpus / "note.md").write_text("text\n", encoding="utf-8")
            (corpus / "sources.json").write_text(json.dumps({
                "contracts": [{
                    "file": "note.md",
                    "status": "reviewed",
                    "sdk_version": "27.0",
                    "reviewed": "2026-11-05",
                    "headers": [],
                    "urls": ["notaurl"],
                }]
            }), encoding="utf-8")
            errors = check_corpus(root, corpus)
            self.assertTrue(any("invalid url" in error for error in errors))

    def test_bad_url_without_host_fails(self) -> None:
        self.assertIsNotNone(check_url("https://"))
        self.assertIsNone(check_url(
            "https://developer.apple.com/documentation/metal/mtlrendercommandencoder/setscissorrect(_:)"))


if __name__ == "__main__":
    unittest.main()
