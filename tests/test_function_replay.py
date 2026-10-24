"""PR C: replay an A2 function fixture through the RE1 reconstruction.

The reference is A2's captured pre/post state, not a second arithmetic model. The
candidate is the tracked RE1 C++ reconstruction. The tests cover both paths, the
word count A2 deliberately does not validate, reference refusal, candidate
failure, the strict harness protocol, and candidate-side preservation.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from oracle.function import fixture, replay, synthetic
from reverse_engineering import reconstruction_diff as diff

ROOT = Path(__file__).resolve().parents[1]
RECON_DIR = (ROOT / "analysis" / "reconstruction" / "_GfxSetConstantBuffer"
             / "reconstruction")
GOLDEN = ROOT / "oracle" / "function" / "synthetic" / "golden.txt"
COMPILER_FALLBACKS = ("c++", "clang++", "g++")
SANITIZER_FLAGS = ("-fsanitize=address,undefined", "-fno-sanitize-recover=all")

WORKDIR: Path | None = None
COMPILER: str | None = None
HARNESS: Path | None = None
BUILD_NOTE = "build not attempted"


def _find_compiler() -> str | None:
    for candidate in COMPILER_FALLBACKS:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _compile(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise AssertionError(f"compile failed:\n{result.stderr}")


def _compile_harness(source_dir: Path, name: str) -> Path:
    assert COMPILER is not None and WORKDIR is not None
    binary = WORKDIR / f"{name}-bin"
    _compile([
        COMPILER, "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-Werror",
        *SANITIZER_FLAGS,
        str(source_dir / "gfx_set_constant_buffer.cpp"),
        str(source_dir / "harness.cpp"),
        str(source_dir / "arith_literals.cpp"),
        "-o", str(binary),
    ])
    return binary


def _mutated_harness(name: str, old: str, new: str) -> Path:
    assert WORKDIR is not None
    target = WORKDIR / name
    shutil.copytree(RECON_DIR, target)
    source = target / "gfx_set_constant_buffer.cpp"
    text = source.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"{name}: mutation anchor is not in the tracked source")
    source.write_text(text.replace(old, new, 1), encoding="utf-8")
    return _compile_harness(target, name)


def setUpModule() -> None:
    global WORKDIR, COMPILER, HARNESS, BUILD_NOTE
    COMPILER = _find_compiler()
    if COMPILER is None:
        BUILD_NOTE = "no C++ compiler on PATH"
        return
    WORKDIR = Path(tempfile.mkdtemp(prefix="eu4-prc-"))
    HARNESS = _compile_harness(RECON_DIR, "harness")
    BUILD_NOTE = "sanitized host build of the tracked RE1 reconstruction"
    print(f"PR C gate: {BUILD_NOTE}", flush=True)


def tearDownModule() -> None:
    if WORKDIR is not None:
        shutil.rmtree(WORKDIR, ignore_errors=True)


class NeedsHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if HARNESS is None:
            raise unittest.SkipTest(f"reconstruction not built: {BUILD_NOTE}")


def _golden() -> str:
    return GOLDEN.read_text(encoding="utf-8")


def _rewrite_exit_ctx(text: str, slot: int, value: int) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("EXIT name=ctx "):
            continue
        parts = line.split()[1:]
        fields = dict(part.split("=", 1) for part in parts)
        data = bytearray.fromhex(fields["bytes"])
        start = slot * 16
        data[start:start + 4] = value.to_bytes(4, "little")
        fields["bytes"] = data.hex()
        lines[index] = "EXIT " + " ".join(f"{key}={value}"
                                          for key, value in fields.items())
        return "\n".join(lines) + "\n"
    raise AssertionError("the transcript has no EXIT ctx region")


def _flip_exit_canary_after(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("EXIT name=canary-after "):
            continue
        fields = dict(part.split("=", 1) for part in line.split()[1:])
        data = bytearray.fromhex(fields["bytes"])
        data[0] ^= 0xFF
        fields["bytes"] = data.hex()
        lines[index] = "EXIT " + " ".join(f"{key}={value}"
                                          for key, value in fields.items())
        return "\n".join(lines) + "\n"
    raise AssertionError("the transcript has no EXIT canary-after region")


def _candidate_report() -> tuple[str, dict, bytes, int, int, int]:
    descriptor = fixture.load_descriptor()
    record = fixture.materialize_capture(_golden(), descriptor)["record"]
    prestate, slot, selector, buffer_dword, ctx_base = replay._build_prestate(
        record, descriptor)
    fields = diff.run_harness(HARNESS, slot, selector, buffer_dword, prestate)
    if fields["returncode"] != "0":
        raise AssertionError(f"the tracked reconstruction failed: {fields['stderr']}")
    return fields["stdout"], record, prestate, slot, selector, ctx_base


def _replace_field(stdout: str, name: str, value: str) -> str:
    lines = []
    replaced = False
    for line in stdout.splitlines():
        key, _, _ = line.partition("=")
        if key == name:
            lines.append(f"{name}={value}")
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        raise AssertionError(f"the candidate report has no {name!r}")
    return "\n".join(lines) + "\n"


class ReplayTests(NeedsHarness):
    def test_committed_s03_matches_and_preserves_provenance(self) -> None:
        result = replay.replay_capture(_golden(), HARNESS)
        self.assertEqual(result["verdict"], "match", result["failures"])
        self.assertEqual(result["failures"], [])
        self.assertIs(result["synthetic"], True)
        self.assertEqual(result["candidate_domain"], {"start": 0xB8, "end": 0x130})
        self.assertEqual(result["reference_domain"], {"start": 0xB8, "end": 0x139})
        self.assertIsInstance(result["record_digest"], str)
        self.assertIsInstance(result["domain_digest"], str)

    def test_s10_matches_on_the_explicit_path(self) -> None:
        descriptor = fixture.load_descriptor()
        result = replay.replay_capture(
            synthetic.capture_text(descriptor, "s10"), HARNESS)
        self.assertEqual(result["verdict"], "match", result["failures"])
        self.assertEqual(result["failures"], [])
        self.assertIs(result["synthetic"], True)

    def test_a_changed_word_count_is_a_mismatch(self) -> None:
        descriptor = fixture.load_descriptor()
        text = _rewrite_exit_ctx(_golden(), slot=2, value=5)
        materialized = fixture.materialize_capture(text, descriptor)
        self.assertEqual(materialized["manifest"]["verdict"], "qualified",
                         "A2 closure is not supposed to validate the word count")
        result = replay.replay_capture(text, HARNESS)
        self.assertEqual(result["verdict"], "mismatch", result["failures"])
        self.assertIs(result["synthetic"], True)
        self.assertTrue(any("word_count" in failure for failure in result["failures"]),
                        result["failures"])


class ReferenceRefusalTests(NeedsHarness):
    def test_an_unqualified_capture_is_refused_before_launch(self) -> None:
        text = _flip_exit_canary_after(_golden())
        descriptor = fixture.load_descriptor()
        self.assertEqual(
            fixture.materialize_capture(text, descriptor)["manifest"]["verdict"],
            "unqualified")
        result = replay.replay_capture(text, Path("/does/not/exist"))
        self.assertEqual(result["verdict"], "refused")
        self.assertIs(result["synthetic"], True)
        self.assertTrue(any("reference:unqualified" in failure
                            for failure in result["failures"]), result["failures"])

    def test_a_foreign_function_is_refused(self) -> None:
        text = _golden().replace("function=gfx:set-constant-buffer",
                                 "function=gfx:other")
        result = replay.replay_capture(text, Path("/does/not/exist"))
        self.assertEqual(result["verdict"], "refused")
        self.assertTrue(any("reference:unqualified" in failure
                            for failure in result["failures"]), result["failures"])

    def test_a_missing_harness_is_an_infrastructure_refusal(self) -> None:
        result = replay.replay_capture(_golden(), Path("/does/not/exist"))
        self.assertEqual(result["verdict"], "refused")
        self.assertIs(result["synthetic"], True)
        self.assertTrue(any("infrastructure:harness-unavailable" in failure
                            for failure in result["failures"]), result["failures"])


class CandidateFailureTests(NeedsHarness):
    def test_invalid_hex_is_malformed(self) -> None:
        stdout, record, prestate, slot, selector, ctx_base = _candidate_report()
        bad = _replace_field(stdout, "size", "zzzzzzzzzzzzzzzz")
        fields, failures = replay._parse_candidate(bad, fixture.load_descriptor(), slot)
        self.assertIsNone(fields)
        self.assertTrue(any("invalid form" in failure for failure in failures), failures)

    def test_a_short_post_is_malformed(self) -> None:
        stdout, record, prestate, slot, selector, ctx_base = _candidate_report()
        bad = _replace_field(stdout, "post", "00")
        fields, failures = replay._parse_candidate(bad, fixture.load_descriptor(), slot)
        self.assertIsNone(fields)
        self.assertTrue(any("invalid form" in failure for failure in failures), failures)

    def test_inconsistent_word_count_is_malformed(self) -> None:
        stdout, record, prestate, slot, selector, ctx_base = _candidate_report()
        bad = _replace_field(stdout, "word_count", "00000005")
        fields, failures = replay._parse_candidate(bad, fixture.load_descriptor(), slot)
        self.assertIsNone(fields)
        self.assertTrue(any("disagrees with post" in failure for failure in failures),
                        failures)

    def test_duplicate_field_is_malformed(self) -> None:
        stdout, record, prestate, slot, selector, ctx_base = _candidate_report()
        bad = stdout + "size=0000000000000130\n"
        fields, failures = replay._parse_candidate(bad, fixture.load_descriptor(), slot)
        self.assertIsNone(fields)
        self.assertTrue(any("duplicate" in failure for failure in failures), failures)

    def test_pointer_and_rax_roles_are_checked(self) -> None:
        stdout, record, prestate, slot, selector, ctx_base = _candidate_report()
        descriptor = fixture.load_descriptor()
        fields, failures = replay._parse_candidate(stdout, descriptor, slot)
        self.assertEqual(failures, [])
        assert fields is not None
        for name, value, expected in (
                ("buffer", "0x123456789abcdef0", "buffer-role"),
                ("rax", "0x123456789abcdef0", "rax-role")):
            with self.subTest(field=name):
                doctored = dict(fields)
                doctored[name] = value
                failures = replay.compare_replay(
                    record, descriptor, prestate, doctored, slot, selector, ctx_base)
                self.assertTrue(any(expected in failure for failure in failures), failures)

    def test_a_preserved_context_byte_is_checked(self) -> None:
        stdout, record, prestate, slot, selector, ctx_base = _candidate_report()
        descriptor = fixture.load_descriptor()
        fields, failures = replay._parse_candidate(stdout, descriptor, slot)
        self.assertEqual(failures, [])
        assert fields is not None
        for offset in (0, descriptor["layout"]["canary_before_start"]):
            with self.subTest(offset=offset):
                post = bytearray.fromhex(fields["post"])
                post[offset] ^= 0xFF
                doctored = dict(fields)
                doctored["post"] = post.hex()
                failures = replay.compare_replay(
                    record, descriptor, prestate, doctored, slot, selector, ctx_base)
                self.assertTrue(any("context-mismatch" in failure for failure in failures),
                                failures)

    @unittest.skipUnless(Path("/bin/sh").exists(), "/bin/sh is not present")
    def test_malformed_output_is_a_candidate_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "malformed"
            probe.write_text("#!/bin/sh\nprintf 'size=0000000000000130\\n'\n")
            probe.chmod(0o755)
            result = replay.replay_capture(_golden(), probe)
        self.assertEqual(result["verdict"], "mismatch")
        self.assertTrue(any("candidate:malformed-output" in failure
                            for failure in result["failures"]), result["failures"])

    @unittest.skipUnless(Path("/bin/sh").exists(), "/bin/sh is not present")
    def test_nonzero_exit_is_a_candidate_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "fail"
            probe.write_text("#!/bin/sh\nexit 1\n")
            probe.chmod(0o755)
            result = replay.replay_capture(_golden(), probe)
        self.assertEqual(result["verdict"], "mismatch")
        self.assertTrue(any("candidate:nonzero-exit" in failure
                            for failure in result["failures"]), result["failures"])

    @unittest.skipUnless(Path("/bin/sh").exists(), "/bin/sh is not present")
    def test_candidate_timeout_is_a_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "slow"
            probe.write_text("#!/bin/sh\nexec sleep 1\n")
            probe.chmod(0o755)
            with mock.patch.object(diff, "HARNESS_TIMEOUT", 0.05):
                result = replay.replay_capture(_golden(), probe)
        self.assertEqual(result["verdict"], "mismatch")
        self.assertTrue(any("candidate:timeout" in failure
                            for failure in result["failures"]), result["failures"])

    @unittest.skipUnless(Path("/bin/sh").exists(), "/bin/sh is not present")
    def test_a_setup_error_is_an_infrastructure_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "setup-error"
            probe.write_text(
                "#!/bin/sh\nprintf 'error=absent-buffer-unavailable\\n'\nexit 2\n")
            probe.chmod(0o755)
            result = replay.replay_capture(_golden(), probe)
        self.assertEqual(result["verdict"], "refused")
        self.assertTrue(any("infrastructure:absent-buffer-unavailable" in failure
                            for failure in result["failures"]), result["failures"])


class MutationTests(NeedsHarness):
    def test_a_corrupted_handle_is_a_mismatch(self) -> None:
        harness = _mutated_harness(
            "handle-mutated",
            "    return ctx;  // what the original leaves in RAX\n",
            "    *const_cast<void **>(handle) = nullptr;\n"
            "    return ctx;  // what the original leaves in RAX\n")
        result = replay.replay_capture(_golden(), harness)
        self.assertEqual(result["verdict"], "mismatch", result["failures"])
        self.assertTrue(any("handle-preservation" in failure
                            for failure in result["failures"]), result["failures"])

    def test_a_modified_buffer_is_a_mismatch(self) -> None:
        harness = _mutated_harness(
            "buffer-mutated",
            "    ctx->dirty = kDirtyValue;                              "
            "// MOV byte [RAX+0x128],1\n",
            "    ctx->dirty = kDirtyValue;                              "
            "// MOV byte [RAX+0x128],1\n"
            "    *const_cast<std::uint32_t *>(\n"
            "        static_cast<const std::uint32_t *>(buffer)) = 0xDEADBEEFu;\n")
        result = replay.replay_capture(_golden(), harness)
        self.assertEqual(result["verdict"], "mismatch", result["failures"])
        self.assertTrue(any("buffer-preservation" in failure
                            for failure in result["failures"]), result["failures"])

    def test_a_buffer_read_on_the_explicit_path_is_a_mismatch(self) -> None:
        harness = _mutated_harness(
            "buffer-read-mutated",
            "    auto *ctx = static_cast<Context *>(*handle);  // MOV RAX,[RDI]\n",
            "    auto *ctx = static_cast<Context *>(*handle);  // MOV RAX,[RDI]\n"
            "    const volatile std::uint32_t probe =\n"
            "        *static_cast<const volatile std::uint32_t *>(buffer);\n"
            "    static_cast<void>(probe);\n")
        descriptor = fixture.load_descriptor()
        result = replay.replay_capture(
            synthetic.capture_text(descriptor, "s10"), harness)
        self.assertEqual(result["verdict"], "mismatch", result["failures"])
        self.assertTrue(any("candidate:nonzero-exit" in failure
                            for failure in result["failures"]), result["failures"])


if __name__ == "__main__":
    unittest.main()
