"""RE1 faithful reconstruction of `_GfxSetConstantBuffer` (entry RVA 0x15ec36a).

The reconstruction under test is
`analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/`, a handwritten C++20
reimplementation of the documented contract. These tests build it and compare it against two
independent references:

* the RE0 instruction model, restated here as literal CMOVNS/SAR and ADD/SHR sequences and a
  documented proof of the division identity - this part needs no compiler and always runs;
* the frozen pilot scenarios (inputs and hand-derived expectations only, no emulator run) -
  each of the 13 rows is replayed through the compiled reconstruction.

With a C++ compiler present the reconstruction is also built with ASan/UBSan and driven on the
host; with an x86-64 build that can be executed, the six-position ABI adapter is probed through
a register-level trampoline. Neither CI nor these tests launch, attach to, or read the game.

Passing here proves the reconstruction implements the documented contract on the tested inputs.
It does not prove hardware-level equivalence with the original, does not establish the true
return type, and does not validate anything outside `slot in [0, 6)`.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from reverse_engineering import reconstruction_pilot as pilot

RECON_DIR = pilot.FUNCTION_DIR / "reconstruction"
HEADER = RECON_DIR / "gfx_set_constant_buffer.hpp"
IMPLEMENTATION = RECON_DIR / "gfx_set_constant_buffer.cpp"
HARNESS_SOURCE = RECON_DIR / "harness.cpp"
ARITH_LITERALS = RECON_DIR / "arith_literals.cpp"
ABI_SOURCE = RECON_DIR / "abi_check.cpp"
ABI_ASM = RECON_DIR / "abi_call6.S"

MASK32 = 0xFFFFFFFF
CONTEXT_SIZE = 0x130
WORD_COUNT_BASE = 0xC8
BUFFER_BASE = 0xD0
DIRTY_OFFSET = 0x128
SLOT_STRIDE = 0x10
SLOT_COUNT = 6

COMPILER_FALLBACKS = ("c++", "clang++", "g++")
EXHAUSTIVE_ENV = "EU4_RE1_EXHAUSTIVE"

# Values chosen around every wrap and sign boundary of both paths, plus the pilot's own cases.
BOUNDARY_VALUES = (
    0x00000000, 0x00000001, 0x00000002, 0x00000003, 0x00000004, 0x00000005,
    0x00000006, 0x00000007, 0x0000000C, 0x0000000D, 0x0000000E, 0x0000000F,
    0x00000010, 0x00000011, 0x7FFFFFFC, 0x7FFFFFFD, 0x7FFFFFFE, 0x7FFFFFFF,
    0x80000000, 0x80000001, 0x80000002, 0x80000003, 0x80000004,
    0xFFFFFFF4, 0xFFFFFFF5, 0xFFFFFFF8, 0xFFFFFFF9, 0xFFFFFFFA, 0xFFFFFFFB,
    0xFFFFFFFC, 0xFFFFFFFD, 0xFFFFFFFE, 0xFFFFFFFF,
)

WORKDIR: Path | None = None
HARNESS: Path | None = None
ABI_BINARY: Path | None = None
BUILD_NOTE = "build not attempted"


# ------------------------------------------------------------------ the instruction model


def model_words_from_selector(selector: int) -> int:
    """The explicit-length path as the instructions literally read, not as the C++ writes it.

    LEA EDX,[R9+3]; ADD R9D,6; TEST EDX,EDX; CMOVNS R9D,EDX; SAR R9D,2. Signals are 32-bit and
    wrap modulo 2^32; the conditional move selects on the sign of the wrapped sum; the final
    shift is arithmetic.
    """
    edx = (selector + 3) & MASK32
    r9d = (selector + 6) & MASK32
    if edx < 0x80000000:  # TEST EDX,EDX / CMOVNS: sign clear, so select the +3 form
        r9d = edx
    if r9d & 0x80000000:  # SAR R9D,2
        return ((r9d - 0x100000000) >> 2) & MASK32
    return r9d >> 2


def model_words_from_buffer(first_dword: int) -> int:
    """MOV R9D,[RSI]; ADD R9D,3; SHR R9D,2 - a logical shift, so no sign interpretation."""
    return ((first_dword + 3) & MASK32) >> 2


def trunc_div4(value: int) -> int:
    """Signed division by four, truncated toward zero (what the C++ `/` does)."""
    return -((-value) // 4) if value < 0 else value // 4


def proof_words_from_selector(selector: int) -> int:
    """The reconstructed identity: `(int32_t)(selector + 3) / 4` with a wrapping add.

    Documented proof for the whole 32-bit domain. Let `t` be the signed interpretation of the
    wrapped sum `selector + 3`.
    * If `t >= 0`, CMOVNS selects `t` and `SAR 2` is `t / 4` exactly.
    * If `t < 0`, the fallback value `selector + 6` is the signed `t + 3`, which cannot overflow
      (`t + 3 <= 2`), and the arithmetic shift gives `floor((t + 3) / 4)`, equal to `t / 4`
      truncated toward zero for every negative `t`.
    Hence the sequence always computes truncating signed division of the wrapped sum by four.
    """
    return trunc_div4(static_cast_signed32((selector + 3) & MASK32)) & MASK32


def static_cast_signed32(value: int) -> int:
    """C++20 `static_cast<int32_t>` of an unsigned 32-bit value: congruent modulo 2^32."""
    return value - 0x100000000 if value & 0x80000000 else value


def naive_selector_words(selector: int) -> int:
    """Near miss 1: dividing the selector itself instead of the wrapped `+3` sum."""
    return trunc_div4(static_cast_signed32(selector)) & MASK32


def naive_buffer_words(first_dword: int) -> int:
    """Near miss 2: a signed division on the buffer path instead of the logical shift."""
    return trunc_div4(static_cast_signed32(first_dword)) & MASK32


def sample_values(count: int = 4096) -> list[int]:
    """Deterministic spread over the domain (glibc's LCG constants), not a random source."""
    values: list[int] = []
    value = 0x12345678
    for _ in range(count):
        value = (value * 1103515245 + 12345) & MASK32
        values.append(value)
    return values


def modelled_exhaustive_digest() -> int:
    """The whole-domain sums of both paths in closed form, from the instruction model.

    Buffer path: `t = (x + 3) mod 2^32` runs over the whole domain as `x` does, so the sum is
    `sum_{t < 2^32} (t >> 2) = 4 * sum_{q < 2^30} q = 2 * (2^30 - 1) * 2^30`.

    Selector path: `u = (x + 3) mod 2^32` also runs over the whole domain.
    * `u < 2^31` gives `u // 4`, summed as `P = 4 * sum_{q < 2^29} q = 2 * (2^29 - 1) * 2^29`.
    * `u >= 2^31` gives `trunc((u - 2^32) / 4)` as a 32-bit value; with `a = 2^32 - u` in
      `[1, 2^31]` that is `(2^32 - a // 4) mod 2^32`, and `sum_{a=1}^{2^31} (a // 4)` is
      `P + 2^29` (the extra term is `a = 2^31`), so the two `P` contributions cancel. The three
      `a < 4` values contribute `2^32 mod 2^32 = 0`, leaving `2^63 - 2^29 - 3 * 2^32`.

    Both sums are modular additions, so they are order-independent and platform-independent.
    """
    buffer_sum = 2 * ((1 << 30) - 1) * (1 << 30)
    selector_sum = (1 << 63) - (1 << 29) - 3 * (1 << 32)
    return (selector_sum ^ buffer_sum) & 0xFFFFFFFFFFFFFFFF


def pilot_rows() -> list[dict]:
    return pilot.load_scenarios()["scenarios"]


# ---------------------------------------------------------------------------- build harness


def _find_compiler() -> str | None:
    for candidate in COMPILER_FALLBACKS:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _compile(command: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as error:  # pragma: no cover - environment
        return False, str(error)
    return result.returncode == 0, (result.stderr or "").strip()


def _build_harness(workdir: Path, compiler: str) -> tuple[Path | None, str]:
    """Return (binary, note). Falls back to a plain build if the sanitizer build or the
    sanitized binary does not work in this environment, and says so in the note."""
    sources = [str(IMPLEMENTATION), str(HARNESS_SOURCE), str(ARITH_LITERALS)]
    sanitized = workdir / "harness_san"
    ok, error = _compile([
        compiler, "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", *sources, "-o", str(sanitized),
    ])
    if ok:
        probe = subprocess.run([str(sanitized), "words", "selector"], input="0000000d\n",
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return sanitized, "host build with ASan/UBSan (-O0)"
        error = f"sanitized binary failed to run: {probe.stdout}{probe.stderr}"
    plain = workdir / "harness"
    ok, plain_error = _compile([
        compiler, "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-Werror",
        *sources, "-o", str(plain),
    ])
    if ok:
        return plain, f"host build without sanitizers ({_first_line(error)})"
    return None, f"build failed: {plain_error or error}"


def _first_line(text: str) -> str:
    return "; ".join(text.splitlines()[:1]) or "no diagnostic"


def _abi_command(compiler: str, workdir: Path) -> list[str] | None:
    """The x86-64 ABI probe build, or None when this host cannot produce one."""
    machine = platform.machine().lower()
    base = [
        compiler, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror",
        str(IMPLEMENTATION), str(ABI_SOURCE), str(ABI_ASM), "-o", str(workdir / "abi_check"),
    ]
    if machine in ("x86_64", "amd64"):
        return base
    if platform.system() == "Darwin" and machine in ("arm64", "aarch64"):
        return [compiler, "-arch", "x86_64", *base[1:]]
    return None


def setUpModule() -> None:
    global WORKDIR, HARNESS, ABI_BINARY, BUILD_NOTE
    compiler = _find_compiler()
    if compiler is None:
        BUILD_NOTE = "no C++ compiler on PATH"
        return
    WORKDIR = Path(tempfile.mkdtemp(prefix="eu4-re1-"))
    HARNESS, BUILD_NOTE = _build_harness(WORKDIR, compiler)
    if HARNESS is None:
        # A compiler exists and the tracked sources do not build: that is a defect here, not an
        # environment that cannot test. Fail rather than skip.
        raise RuntimeError(f"reconstruction did not build with {compiler}: {BUILD_NOTE}")

    # A native x86-64 host is expected to build and run the probe; only a cross-built probe
    # (Apple Silicon through `-arch x86_64`) may be unrunnable here because of a missing x86-64
    # runtime. Anything that runs and fails is a failure on any host.
    native = platform.machine().lower() in ("x86_64", "amd64")
    abi_command = _abi_command(compiler, WORKDIR)
    if abi_command is None:
        BUILD_NOTE += "; no x86-64 ABI probe build for this host"
        return
    ok, error = _compile(abi_command)
    if not ok:
        if native:
            raise RuntimeError(f"x86-64 ABI probe did not build: {error}")
        BUILD_NOTE += f"; x86-64 ABI probe build failed: {_first_line(error)}"
        return
    try:
        probe = subprocess.run([str(WORKDIR / "abi_check")], capture_output=True, text=True,
                               timeout=120)
    except OSError as exec_error:
        if native:
            message = f"x86-64 ABI probe could not be executed: {exec_error}"
            raise RuntimeError(message) from exec_error
        BUILD_NOTE += "; x86-64 ABI probe could not be executed here (no x86-64 runtime)"
        return
    if probe.returncode != 0:
        if native or "check=" in probe.stdout:
            # It ran and failed its own checks, or this host is expected to be able to run it.
            raise RuntimeError(f"x86-64 ABI probe failed: {probe.stdout}{probe.stderr}")
        BUILD_NOTE += "; x86-64 ABI probe did not run here (no x86-64 runtime)"
        return
    ABI_BINARY = WORKDIR / "abi_check"
    BUILD_NOTE += "; x86-64 ABI probe built and executed"


def tearDownModule() -> None:
    if WORKDIR is not None:
        shutil.rmtree(WORKDIR, ignore_errors=True)


class NeedsHarness(unittest.TestCase):
    """Skips (rather than silently passing) when this host has no usable C++ toolchain."""

    @classmethod
    def setUpClass(cls) -> None:
        if HARNESS is None:
            raise unittest.SkipTest(f"reconstruction not built: {BUILD_NOTE}")


class NeedsAbiProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if ABI_BINARY is None:
            raise unittest.SkipTest(f"x86-64 ABI probe unavailable: {BUILD_NOTE}")


# ------------------------------------------------------------------------- harness drivers


def run_words(path: str, values: list[int]) -> list[int]:
    stdin = "".join(f"{value:08x}\n" for value in values)
    result = subprocess.run([str(HARNESS), "words", path], input=stdin, capture_output=True,
                            text=True, timeout=60)
    if result.returncode != 0:
        raise AssertionError(f"harness words {path} failed: {result.stdout}{result.stderr}")
    return [int(line, 16) for line in result.stdout.split()]


def run_scenario(slot: int, selector: int, buffer_dword: int | None, prestate: bytes,
                 check: bool = True) -> dict:
    argument = "absent" if buffer_dword is None else f"{buffer_dword:08x}"
    command = [str(HARNESS), "scenario", str(slot), f"{selector:x}", argument]
    result = subprocess.run(command, input=prestate.hex() + "\n", capture_output=True,
                            text=True, timeout=60)
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        fields[key] = value
    fields["returncode"] = str(result.returncode)
    if check and (result.returncode != 0 or "error" in fields):
        raise AssertionError(f"harness scenario failed: {result.stdout}{result.stderr}")
    return fields


def declared_write_offsets(slot: int) -> set[int]:
    words = WORD_COUNT_BASE + slot * SLOT_STRIDE
    pointer = BUFFER_BASE + slot * SLOT_STRIDE
    return {DIRTY_OFFSET, *range(words, words + 4), *range(pointer, pointer + 8)}


def masked_post(post_hex: str, slot: int) -> bytes:
    """The reported context with the stored buffer pointer blanked: it is a host address, and
    the contract compares it as a role (the buffer argument), not as a value."""
    post = bytearray(bytes.fromhex(post_hex))
    start = BUFFER_BASE + slot * SLOT_STRIDE
    post[start:start + 8] = bytes(8)
    return bytes(post)


# ----------------------------------------------------------------------------- model tests


class InstructionModelTests(unittest.TestCase):
    """No compiler needed: the model and the documented proof, against RE0's frozen numbers."""

    def test_model_reproduces_the_frozen_pilot_expectations(self) -> None:
        for row in pilot_rows():
            selector = pilot.hexint(row["r9d"])
            expected = pilot.hexint(row["expected_words"])
            with self.subTest(row=row["id"]):
                if row["buffer_first_dword"] is None:
                    self.assertEqual(model_words_from_selector(selector), expected)
                else:
                    model = model_words_from_buffer(pilot.hexint(row["buffer_first_dword"]))
                    self.assertEqual(model, expected)
                # The pilot freezes the zero-extended words in R9 as well.
                self.assertEqual(pilot.hexint(row["expected_r9"]) & MASK32, expected)

    def test_proof_identity_holds_over_the_boundary_corpus(self) -> None:
        """The instruction-level model and the C++ expression must agree by the proof above."""
        for value in BOUNDARY_VALUES:
            with self.subTest(value=hex(value)):
                self.assertEqual(model_words_from_selector(value), proof_words_from_selector(value))

    def test_proof_identity_holds_over_the_sample(self) -> None:
        for value in sample_values():
            self.assertEqual(model_words_from_selector(value), proof_words_from_selector(value))

    def test_boundary_corpus_discriminates_the_naive_selector_implementation(self) -> None:
        """A corpus that cannot tell the two apart would be evidence of nothing."""
        discriminating = [v for v in BOUNDARY_VALUES
                          if model_words_from_selector(v) != naive_selector_words(v)]
        self.assertTrue(discriminating, "corpus does not exercise the +3 wrap")
        # Not every negative selector differs: -7 (t = -4) has the same quotient either way.
        self.assertNotIn(0xFFFFFFF9, discriminating)

    def test_boundary_corpus_discriminates_the_naive_buffer_implementation(self) -> None:
        discriminating = [v for v in BOUNDARY_VALUES
                          if model_words_from_buffer(v) != naive_buffer_words(v)]
        self.assertTrue(discriminating, "corpus does not exercise the unsigned shift")


# -------------------------------------------------------------------------- compiled tests


class ReconstructionSourcesTests(unittest.TestCase):
    """Runs whether or not a compiler is present: a deleted or renamed source must fail here
    rather than turn the compiled tests into a silent skip."""

    def test_sources_are_present_and_non_empty(self) -> None:
        for path in (HEADER, IMPLEMENTATION, HARNESS_SOURCE, ARITH_LITERALS, ABI_SOURCE, ABI_ASM):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), f"missing reconstruction source: {path}")
                self.assertTrue(path.read_text().strip(), f"empty reconstruction source: {path}")


class ReconstructionArithmeticTests(NeedsHarness):
    def test_compiled_selector_path_matches_the_instruction_model(self) -> None:
        values = list(BOUNDARY_VALUES) + sample_values()
        self.assertEqual(run_words("selector", values),
                         [model_words_from_selector(value) for value in values])

    def test_compiled_buffer_path_matches_the_instruction_model(self) -> None:
        values = list(BOUNDARY_VALUES) + sample_values()
        self.assertEqual(run_words("buffer", values),
                         [model_words_from_buffer(value) for value in values])

    def test_compiled_paths_agree_on_the_pilots_own_values(self) -> None:
        """Both paths through the compiled code, on RE0's own frozen values (s01, s10-s13)."""
        rows = {row["id"]: row for row in pilot_rows()}
        for row_id in ("s01", "s10", "s11", "s12", "s13"):
            row = rows[row_id]
            path = "buffer" if row["buffer_first_dword"] is not None else "selector"
            value = row["buffer_first_dword"] if path == "buffer" else row["r9d"]
            with self.subTest(row=row_id):
                self.assertEqual(run_words(path, [pilot.hexint(value)]),
                                 [pilot.hexint(row["expected_words"])])


class ReconstructedScenarioTests(NeedsHarness):
    def setUp(self) -> None:
        scenarios = pilot.load_scenarios()
        self.prestate = pilot.fill_pattern(scenarios["memory"], CONTEXT_SIZE)

    def test_reconstructs_every_frozen_pilot_scenario(self) -> None:
        for row in pilot_rows():
            selector = pilot.hexint(row["r9d"])
            dword = None if row["buffer_first_dword"] is None else pilot.hexint(
                row["buffer_first_dword"])
            with self.subTest(row=row["id"]):
                fields = run_scenario(row["slot"], selector, dword, self.prestate)
                expected_words = pilot.hexint(row["expected_words"])

                self.assertEqual(fields["size"], f"{CONTEXT_SIZE:016x}")
                self.assertEqual(int(fields["word_count"], 16), expected_words)
                self.assertEqual(fields["buffer"], "ARG")
                self.assertEqual(int(fields["dirty"], 16), 1)
                self.assertEqual(fields["rax"], "ctx")

                post = bytes.fromhex(fields["post"])
                self.assertEqual(len(post), CONTEXT_SIZE)
                changed = {index for index, (before, after) in enumerate(zip(self.prestate, post))
                           if before != after}
                declared = declared_write_offsets(row["slot"])
                self.assertTrue(changed.issubset(declared),
                                f"undeclared writes at {sorted(changed - declared)}")
                self.assertTrue(changed, "the scenario must write something")

    def test_scenarios_are_deterministic(self) -> None:
        first = run_scenario(3, 0xFFFFFFF8, None, self.prestate)
        second = run_scenario(3, 0xFFFFFFF8, None, self.prestate)
        # The stored buffer pointer is a host address, so it is compared as a role
        # (`buffer=ARG` below) and blanked out of the byte comparison.
        self.assertEqual(masked_post(first["post"], 3), masked_post(second["post"], 3))
        self.assertEqual(first["word_count"], second["word_count"])

    def test_the_write_set_is_confined_to_the_three_declared_fields(self) -> None:
        """Every byte of all six slots is seeded, so a stray write cannot hide in a zero."""
        for slot in range(SLOT_COUNT):
            with self.subTest(slot=slot):
                fields = run_scenario(slot, 0x0000000D, None, self.prestate)
                post = bytes.fromhex(fields["post"])
                changed = {index for index, (before, after) in enumerate(zip(self.prestate, post))
                           if before != after}
                self.assertTrue(changed.issubset(declared_write_offsets(slot)))
                # ... and the three fields really were written (a byte can legitimately keep
                # its seed value, so this is asserted by role, not by byte difference).
                self.assertEqual(int(fields["word_count"], 16), 4)
                self.assertEqual(fields["buffer"], "ARG")
                self.assertEqual(int(fields["dirty"], 16), 1)

    def test_slot_domain_is_enforced(self) -> None:
        for slot in (SLOT_COUNT, SLOT_COUNT + 1, 7, 0xFFFFFFFF):
            with self.subTest(slot=slot):
                fields = run_scenario(slot, 0x0000000D, None, self.prestate, check=False)
                self.assertEqual(fields["error"], "slot-out-of-domain")
                self.assertNotEqual(fields["returncode"], "0")

    def test_a_buffer_reading_selector_without_buffer_contents_is_refused(self) -> None:
        """Fail closed rather than read an input the scenario does not provide."""
        fields = run_scenario(0, 0xFFFFFFFF, None, self.prestate, check=False)
        self.assertEqual(fields["error"], "missing-buffer-input")
        self.assertNotEqual(fields["returncode"], "0")

    def test_prestate_size_is_enforced(self) -> None:
        fields = run_scenario(0, 0x0000000D, None, self.prestate[:-1], check=False)
        self.assertEqual(fields["error"], "prestate-size")
        self.assertNotEqual(fields["returncode"], "0")


class ExhaustiveSweepTests(NeedsHarness):
    @unittest.skipUnless(
        os.environ.get(EXHAUSTIVE_ENV) == "1",
        f"set {EXHAUSTIVE_ENV}=1 to sweep all 2^32 inputs (slow, and not a CI gate)")
    def test_whole_domain_agrees_with_the_literal_encodings(self) -> None:
        compiler = _find_compiler()
        assert compiler is not None  # NeedsHarness implies a compiler was found
        binary = WORKDIR / "harness_fast"  # type: ignore[union-attr]
        # Three translation units, no LTO: the sweep cannot see the reconstruction and the
        # literal encodings at once, so it cannot fold the comparison away.
        ok, error = _compile([
            compiler, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror",
            str(IMPLEMENTATION), str(HARNESS_SOURCE), str(ARITH_LITERALS),
            "-o", str(binary),
        ])
        self.assertTrue(ok, error)
        result = subprocess.run([str(binary), "exhaustive"], capture_output=True, text=True,
                                timeout=3600)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout.strip(),
                         r"^exhaustive=ok cases=4294967296 digest=[0-9a-f]{16}$")
        # ... and the summed values, not just the two formulations' agreement, match the
        # closed-form model above.
        digest = int(result.stdout.strip().split("digest=")[1], 16)
        self.assertEqual(digest, modelled_exhaustive_digest())


class AbiAdapterTests(NeedsAbiProbe):
    def test_adapter_receives_the_originals_argument_registers(self) -> None:
        result = subprocess.run([str(ABI_BINARY)], capture_output=True, text=True, timeout=60)
        lines = result.stdout.splitlines()
        checks = {line.split("=")[1].split()[0]: line.split()[-1]
                  for line in lines if line.startswith("check=")}
        # Every declared probe must have reported, and all of them must have passed: an empty
        # or silent run is not a pass.
        self.assertEqual(set(checks), {
            "slot_from_rcx", "buffer_from_rsi", "dirty_written", "rax_is_context",
            "unused_positions_ignored", "selector_r9d_overflow_case",
            "selector_r9d_negative_case", "buffer_path_words", "slot_reads_rcx_not_rdx",
            "selector_reads_r9d_not_r8",
        })
        self.assertEqual(set(checks.values()), {"ok"})
        self.assertIn("abi=ok", lines)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
