import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))
import eu4_metal_boot as boot  # noqa: E402
import eu4_metal_triage as triage  # noqa: E402


def load_accept():
    path = ROOT / "campaigns" / "eu4-index-buffer" / "accept.py"
    spec = importlib.util.spec_from_file_location("index_buffer_accept", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


accept = load_accept()
_PREFIX = r"\x55\x48\x89\xe5\x41\x57"


def _line(name: str, entry: str, end: str, fn: str, prefix: str) -> str:
    return f'GFX_ENTRY({name}, {entry}, {end}, {fn}, "{prefix}")'


def _trusted() -> str:
    return _line("alpha", "0x1000", "0x1100", "eu4_alpha", _PREFIX) + "\n"


def _candidate() -> str:
    lines = [_trusted().rstrip("\n")]
    for name, entry, end, fn, prefix in accept.REQUIRED:
        encoded = "".join(f"\\x{byte:02x}" for byte in prefix)
        lines.append(_line(name, hex(entry), hex(end), fn, encoded))
    return "\n".join(lines) + "\n"


class IndexRegistryTests(unittest.TestCase):
    def test_missing_registration_fails(self) -> None:
        problem = accept.registry_problems(_trusted(), _trusted(), boot.parse_gfx_entries)
        self.assertIn("missing index registration", problem)

    def test_contract_registration_matches(self) -> None:
        self.assertIsNone(accept.registry_problems(_candidate(), _trusted(), boot.parse_gfx_entries))

    def test_changed_prefix_is_not_the_contract(self) -> None:
        text = _candidate().replace(r"\x48\x85\xff\x74\x50", r"\x48\x85\xff\x74\x2f")
        problem = accept.registry_problems(text, _trusted(), boot.parse_gfx_entries)
        self.assertIn("missing index registration", problem)

    def test_preexisting_entry_must_stay(self) -> None:
        text = _candidate().replace("eu4_alpha", "eu4_beta")
        problem = accept.registry_problems(text, _trusted(), boot.parse_gfx_entries)
        self.assertEqual(problem, "pre-existing gfx entry changed")


class IndexReceiptTests(unittest.TestCase):
    def _create(self, **overrides) -> dict:
        body = {
            "success": 1,
            "symbol": "_GfxCreateIndexBuffer",
            "rva": "0x14bfa60",
            "pid": 10,
            "thread": 20,
            "resource": "0x1000",
            "bytes": 8,
            "count": 4,
        }
        body.update(overrides)
        return body

    def test_create_log_alone_is_not_a_return(self) -> None:
        self.assertEqual(
            triage.index_buffer_receipt(
                self._create(), None, {"pid": 10, "thread": 20}, image_ok=True),
            "unvalidated",
        )

    def test_matching_pid_without_a_later_stop_is_unvalidated(self) -> None:
        self.assertEqual(
            triage.index_buffer_receipt(
                self._create(), None, {"pid": 10, "thread": 20}, image_ok=True),
            "unvalidated",
        )

    def test_mismatched_init_pid_is_unvalidated(self) -> None:
        self.assertEqual(
            triage.index_buffer_receipt(
                self._create(),
                {"pid": 10, "reason": "missing"},
                {"pid": 11, "thread": 20},
                image_ok=True,
            ),
            "unvalidated",
        )

    def test_later_stop_from_the_same_process_is_a_return(self) -> None:
        self.assertEqual(
            triage.index_buffer_receipt(
                self._create(),
                {"pid": 10, "reason": "missing"},
                {"pid": 10, "thread": 20},
                image_ok=True,
            ),
            "returned",
        )

    def test_later_original_gl_call_is_distinct(self) -> None:
        self.assertEqual(
            triage.index_buffer_receipt(
                self._create(),
                {"pid": 10, "reason": "gl", "symbol": "_GfxCreateVertexBuffer"},
                {"pid": 10, "thread": 20},
                image_ok=True,
            ),
            "unrelated_gl",
        )

    def test_absent_receipt(self) -> None:
        self.assertEqual(triage.index_buffer_receipt(None, None, None, image_ok=True), "absent")


@unittest.skipUnless(sys.platform == "darwin", "Metal index-buffer checker runs on macOS")
class IndexCheckerTests(unittest.TestCase):
    def test_candidate_header_edit_is_visible(self) -> None:
        observed, detail = accept.header_edit_observed(ROOT / "benchmark" / "eu4_gfx_buffers.h")
        self.assertTrue(observed, detail)

    def test_unmodified_worktree_fails_for_missing_registration(self) -> None:
        candidate_rev = json.loads(
            (ROOT / "campaigns" / "eu4-index-buffer" / "campaign.json").read_text()
        )["workspace"]["commit"]
        trusted_rev = "ea5b89d037028fa5cf0bd6c4c8a4ee9629ae5ecb"
        parent = Path(tempfile.mkdtemp(prefix="eu4-ib-wt-"))
        trusted = parent / "trusted"
        candidate = parent / "candidate"
        registered: list[Path] = []
        try:
            for path, rev in ((trusted, trusted_rev), (candidate, candidate_rev)):
                added = subprocess.run(
                    ["git", "worktree", "add", "--detach", str(path), rev],
                    cwd=ROOT, capture_output=True, text=True, check=False,
                )
                self.assertEqual(added.returncode, 0, added.stderr)
                registered.append(path)
            script = trusted / "campaigns" / "eu4-index-buffer" / "accept.py"
            ran = subprocess.run(
                [sys.executable, str(script), str(candidate)],
                cwd=trusted, capture_output=True, text=True, check=False,
            )
            self.assertEqual(ran.returncode, 1, ran.stderr or ran.stdout)
            result = json.loads(ran.stdout)
            self.assertEqual(result["verdict"], "fail", result)
            self.assertIn("missing index registration", result["diagnostics"])
        finally:
            errors = []
            for path in registered:
                removed = subprocess.run(
                    ["git", "worktree", "remove", "--force", str(path)],
                    cwd=ROOT, capture_output=True, text=True, check=False,
                )
                if removed.returncode != 0:
                    errors.append(removed.stderr.strip() or f"worktree remove failed: {path}")
            if not errors:
                shutil.rmtree(parent, ignore_errors=True)
            if errors:
                message = "; ".join(errors)
                if sys.exc_info()[1] is None:
                    self.fail(message)
                raise AssertionError(message)
