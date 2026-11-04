import hashlib
import json
import os
import unittest
from pathlib import Path

from reverse_engineering.ghidra_query import main
from reverse_engineering.paths import ghidra_project_meta_path, load_target, local_binary_root

ENTRY = "0x00000000015ec28e"
# Ghidra rewrites these local index timestamps on open. Their bytes must stay identical.
_INDEX_NAMES = {"~index.dat", "~index.bak"}


def ghidra_available() -> bool:
    root = os.environ.get("GHIDRA_INSTALL")
    if not root:
        return False
    return (Path(root).expanduser() / "support" / "analyzeHeadless").is_file()


def _project_dir() -> Path | None:
    meta_path = ghidra_project_meta_path(local_binary_root())
    if not meta_path.is_file():
        return None
    project = Path(json.loads(meta_path.read_text())["project_dir"])
    if project.is_dir() and any(project.iterdir()):
        return project
    return None


def _snapshot(root: Path) -> dict[str, tuple[str, int]]:
    rows = {}
    for path in root.rglob("*"):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            rows[str(path.relative_to(root))] = (digest, path.stat().st_mtime_ns)
    return rows


@unittest.skipUnless(ghidra_available() and _project_dir() is not None, "recorded Ghidra project unavailable")
class GhidraQueryLiveTests(unittest.TestCase):
    def test_known_function_keeps_project_bytes(self):
        project = _project_dir()
        assert project is not None
        before = _snapshot(project)
        code, document = _run(["function", "0x15ec28e", "--process-timeout", "180"])
        after = _snapshot(project)
        self.assertEqual(code, 0)
        self.assertEqual(set(before), set(after))
        for rel, (digest, mtime) in before.items():
            got, later = after[rel]
            self.assertEqual(got, digest, rel)
            if mtime != later:
                self.assertIn(Path(rel).name, _INDEX_NAMES, rel)
        self.assertEqual(document["status"], "ok")
        self.assertEqual(document["schema"], "eu4.ghidra_query.v1")
        self.assertEqual(document["evidence"]["presence"], "present")
        self.assertEqual(document["evidence"]["entry_rva"], ENTRY)
        self.assertGreaterEqual(document["evidence"]["instruction_returned"], 1)
        self.assertEqual(document["identity"]["executable_sha256"], load_target()["sha256"])
        self.assertEqual(document["identity"]["image_base"], int(load_target()["image_base"]))
        self.assertNotIn("runtime_value", json.dumps(document))


def _run(argv: list[str]) -> tuple[int, dict]:
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, json.loads(buf.getvalue())


if __name__ == "__main__":
    unittest.main()
