import hashlib
import importlib.util
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_accept():
    path = ROOT / "campaigns" / "eu4-vertex-buffer-multiple" / "accept.py"
    spec = importlib.util.spec_from_file_location("vertex_buffer_multiple_accept", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


accept = load_accept()
_PREFIX = r"\x55\x48\x89\xe5\x41\x57"


def _line(name: str, entry: str, end: str, fn: str, prefix: str) -> str:
    return f'GFX_ENTRY({name}, {entry}, {end}, {fn}, "{prefix}")'


def _trusted() -> str:
    return _line("alpha", "0x1000", "0x1100", "eu4_alpha", _PREFIX) + "\n"


def _identity() -> dict:
    return {
        "entry": 0x2000,
        "end": 0x2006,
        "instructions": [{
            "instruction_rva": "0x2000",
            "length": 6,
            "bytes_hex": "554889e54157",
        }],
    }


def _candidate() -> str:
    return _trusted() + _line(
        accept.NAME, "0x2000", "0x2006", "eu4_gfx_update_vertex_buffer_multiple", _PREFIX,
    ) + "\n"


class CheckpointTests(unittest.TestCase):
    def test_first_write_keeps_its_bytes_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            payload = b"observed entry\n"
            path, digest = accept.capture_checkpoint(directory, payload)
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
            self.assertEqual(accept.checkpoint_sha256(path), digest)
            again, second = accept.capture_checkpoint(directory, b"replacement")
            self.assertEqual(again.read_bytes(), payload)
            self.assertEqual(second, digest)
            self.assertEqual(list(directory.glob("research-checkpoint.md")), [path])

    def test_restart_in_another_process_keeps_the_same_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            payload = b"checkpoint\n"
            _path, digest = accept.capture_checkpoint(directory, payload)
            script = textwrap.dedent(
                f"""
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "accept_restart", {str(ROOT / "campaigns" / "eu4-vertex-buffer-multiple" / "accept.py")!r})
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                path, digest = module.capture_checkpoint({str(directory)!r}, b"other")
                print(digest)
                print(path.read_bytes().hex())
                """
            )
            ran = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True, check=False)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            printed_digest, printed_hex = ran.stdout.splitlines()
            self.assertEqual(printed_digest, digest)
            self.assertEqual(bytes.fromhex(printed_hex), payload)

    def test_crash_before_link_leaves_no_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            payload = b"after restart\n"
            temporary = accept.prepare_checkpoint_temp(directory, b"partial")
            final = directory / accept.CHECKPOINT_NAME
            self.assertFalse(final.exists())
            self.assertTrue(temporary.is_file())
            path, digest = accept.capture_checkpoint(directory, payload)
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
            self.assertEqual(list(directory.glob(accept.CHECKPOINT_NAME)), [path])

    def test_modified_prefix_fails_and_the_checkpoint_stays(self) -> None:
        saved = b"## Entry\n0x10\n"
        self.assertIsNone(accept.preservation_problem(saved, saved))
        self.assertIsNone(accept.preservation_problem(saved + b"\n## Follow-up\nlater\n", saved))
        self.assertEqual(
            accept.preservation_problem(b"rewritten\n", saved),
            "research checkpoint was modified",
        )
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            path, digest = accept.capture_checkpoint(directory, saved)
            _again, second = accept.capture_checkpoint(directory, b"rewritten\n")
            self.assertEqual(path.read_bytes(), saved)
            self.assertEqual(second, digest)


class BlockerTests(unittest.TestCase):
    def _workspace(self, parent: Path, text: str) -> Path:
        workspace = parent / "workspace"
        note = workspace / accept.NOTE_REL
        note.parent.mkdir(parents=True)
        note.write_text(text, encoding="utf-8")
        return workspace

    def test_blocker_before_checkpoint_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            workspace = self._workspace(parent, "BLOCKER: shader compiler required\n")
            first = accept.check(workspace)
            second = accept.check(workspace)
            self.assertEqual(first["verdict"], "blocker")
            self.assertEqual(second["verdict"], "blocker")
            self.assertIn("BLOCKER:", first["diagnostics"])
            self.assertIn("shader compiler required", first["diagnostics"])
            self.assertFalse((parent / accept.CHECKPOINT_NAME).exists())

    def test_blocker_after_checkpoint_leaves_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            saved = b"## Entry\n0x10\n"
            path, digest = accept.capture_checkpoint(parent, saved)
            workspace = self._workspace(
                parent, saved.decode() + "\n## Follow-up\nBLOCKER: command encoder required\n")
            result = accept.check(workspace)
            self.assertEqual(result["verdict"], "blocker")
            self.assertIn("BLOCKER:", result["diagnostics"])
            self.assertIn("command encoder required", result["diagnostics"])
            self.assertEqual(path.read_bytes(), saved)
            self.assertEqual(accept.checkpoint_sha256(path), digest)


class RegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "benchmark"))
        import eu4_metal_boot as boot
        self.parse = boot.parse_gfx_entries

    def test_matching_tuple_is_accepted(self) -> None:
        self.assertIsNone(accept.registration_problem(
            _candidate(), _trusted(), _identity(), self.parse))

    def test_changed_prefix_is_rejected(self) -> None:
        lines = _candidate().splitlines()
        lines[-1] = lines[-1].replace(r"\x41\x57", r"\x41\x56")
        problem = accept.registration_problem(
            "\n".join(lines) + "\n", _trusted(), _identity(), self.parse)
        self.assertEqual(problem, "prefix does not match the instruction bytes from research")

    def test_preexisting_entry_must_stay(self) -> None:
        text = _candidate().replace("eu4_alpha", "eu4_beta")
        problem = accept.registration_problem(text, _trusted(), _identity(), self.parse)
        self.assertEqual(problem, "pre-existing gfx entry changed")

    def test_extra_entry_is_rejected(self) -> None:
        text = _candidate() + _line("extra", "0x3000", "0x3100", "eu4_extra", _PREFIX) + "\n"
        problem = accept.registration_problem(text, _trusted(), _identity(), self.parse)
        self.assertIn("unexpected gfx entry", problem)


if __name__ == "__main__":
    unittest.main()
