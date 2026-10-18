"""RE2 differential validation: the RE1 reconstruction against the P-code reference.

Always (CI, no Ghidra): the frozen corpus is the declared cross product of the six slots with the
two frozen input sets and contains every RE0 pilot row; the committed reference record validates
against that corpus; forged records and perturbed reference rows are rejected for the reason under
test; and the compiled reconstruction matches the recorded original on every corpus row, with the
stored buffer pointer compared as a role.

Deliberately incorrect reconstructions - a signed division on the buffer path, and the missing
`+3` wrap on the selector path - are compiled from mutated copies of the tracked sources and must
be rejected by that same comparator, so a comparator that accepts everything cannot pass here.

With Ghidra and the analysed project present, the corpus is re-run twice and must reproduce the
committed canonical block, and a buffer-path row with its required input withheld must fail
qualification.

The corpus freezes inputs, not reference outputs: no test here compares an outcome with an
expected value, and nothing derives an expectation from the reconstruction. Nothing here launches,
attaches to or reads the game.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from reverse_engineering import paths
from reverse_engineering import reconstruction_diff as diff
from reverse_engineering import reconstruction_pilot as pilot

RECON_DIR = diff.FUNCTION_DIR / "reconstruction"
HEADER = RECON_DIR / "gfx_set_constant_buffer.hpp"
REFERENCE_PRESENT = diff.REFERENCE_PATH.is_file()
BINARY_PRESENT = paths.executable_path().is_file()
PROJECT_PRESENT = pilot.default_project_dir().is_dir()

COMPILER_FALLBACKS = ("c++", "clang++", "g++")
# The RE1 gate owns the sanitizer policy (fatal findings, and enforcement on the supported CI
# runner). This module only needs a build of the same reconstruction to compare.
SANITIZER_FLAGS = ("-fsanitize=address,undefined", "-fno-sanitize-recover=all")

#: The declared rule the frozen corpus must equal, so editing the corpus (or the rule) without
#: re-running the oracle fails a test here rather than drifting silently.
SLOTS = tuple(range(6))
BUFFER_MARKER = 0xFFFFFFFF
SELECTORS = tuple(sorted(set(
    [value & 0xFFFFFFFF for value in range(-16, 17)]
    + list(range(0x7FFFFFFC, 0x80000004))
    + list(range(0xFFFFFFFA, 0x100000000))
)))
BUFFER_DWORDS = tuple(sorted(set(
    list(range(0, 17))
    + list(range(0x7FFFFFFC, 0x80000004))
    + list(range(0xFFFFFFFC, 0x100000000))
)))

#: The two near misses RE1's arithmetic was written against, as textual mutations of the tracked
#: header. Each anchor is asserted present, so the reconstruction changing underneath cannot turn
#: a control into a no-op.
NEAR_MISSES = {
    "signed_division_on_the_buffer_path": (
        "    return (first_dword + 3u) >> 2;",
        "    return static_cast<std::uint32_t>("
        "static_cast<std::int32_t>(first_dword + 3u) / 4);",
    ),
    "missing_wrap_on_the_selector_path": (
        "    const std::uint32_t sum = selector + 3u;\n"
        "    const std::int32_t signed_sum = static_cast<std::int32_t>(sum);\n"
        "    return static_cast<std::uint32_t>(signed_sum / 4);",
        "    return static_cast<std::uint32_t>(static_cast<std::int32_t>(selector) / 4);",
    ),
}

WORKDIR: Path | None = None
COMPILER: str | None = None
HARNESS: Path | None = None
BUILD_NOTE = "build not attempted"


def ghidra_available() -> bool:
    root = os.environ.get("GHIDRA_INSTALL")
    if not root:
        return False
    return (Path(root).expanduser() / "support" / "analyzeHeadless").is_file()


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


def _compile_harness(workdir: Path, source_dir: Path, name: str) -> Path:
    """Build the reconstruction's scenario harness from `source_dir`."""
    assert COMPILER is not None
    binary = workdir / name
    ok, error = _compile([
        COMPILER, "-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-Werror", *SANITIZER_FLAGS,
        str(source_dir / "gfx_set_constant_buffer.cpp"), str(source_dir / "harness.cpp"),
        str(source_dir / "arith_literals.cpp"), "-o", str(binary),
    ])
    if not ok:
        raise AssertionError(f"{name} did not build from {source_dir}: {error}")
    return binary


def _mutated_source_dir(name: str, mutation: tuple[str, str]) -> Path:
    """A copy of the tracked reconstruction with one textual mutation applied.

    Nothing tracked is modified; the mutation must anchor on the tracked text or the control would
    silently become a duplicate of the faithful build.
    """
    assert WORKDIR is not None
    target = WORKDIR / name
    shutil.copytree(RECON_DIR, target)
    header = target / HEADER.name
    text = header.read_text()
    old, new = mutation
    if old not in text:
        raise AssertionError(f"{name}: the mutation anchor is no longer in the tracked header")
    header.write_text(text.replace(old, new, 1))
    return target


def setUpModule() -> None:
    global WORKDIR, COMPILER, HARNESS, BUILD_NOTE
    COMPILER = _find_compiler()
    if COMPILER is None:
        # Not enforced here: the RE1 gate already fails the supported CI runner when no compiler
        # is present, so this module cannot be the one that silently skips.
        BUILD_NOTE = "no C++ compiler on PATH"
    else:
        WORKDIR = Path(tempfile.mkdtemp(prefix="eu4-re2-"))
        HARNESS = _compile_harness(WORKDIR, RECON_DIR, "harness")
        BUILD_NOTE = "sanitized host build of the tracked reconstruction"
    print(f"RE2 gate: {BUILD_NOTE}", flush=True)


def tearDownModule() -> None:
    if WORKDIR is not None:
        shutil.rmtree(WORKDIR, ignore_errors=True)


class NeedsHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if HARNESS is None:
            raise unittest.SkipTest(f"reconstruction not built: {BUILD_NOTE}")


def _row_key(row: dict) -> tuple:
    dword = row["buffer_first_dword"]
    return (row["slot"], row["path"], pilot.hexint(row["r9d"]),
            None if dword is None else pilot.hexint(dword))


def _window_index(corpus: dict, slot: int, field: str, offset: int = 0) -> int:
    start, _ = diff.common_window(corpus)
    return diff.declared_fields(corpus, slot)[field][0] - start + offset


def _flip_byte(hex_text: str, index: int) -> str:
    raw = bytearray.fromhex(hex_text)
    raw[index] ^= 0xFF
    return raw.hex()


class FrozenCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.corpus = diff.load_corpus()

    def test_pins_the_target_binary(self) -> None:
        self.assertEqual(self.corpus["schema"], diff.CORPUS_SCHEMA)
        self.assertEqual(self.corpus["function"], "_GfxSetConstantBuffer")
        self.assertEqual(self.corpus["binary_sha256"], paths.binary_sha256())
        self.assertEqual(self.corpus["image_base"], paths.load_target()["image_base"])
        self.assertTrue(self.corpus["frozen_before_execution"])
        # The emulator constructs a larger region than the reconstruction's object; only the
        # intersection is compared, so the declaration of both matters.
        self.assertEqual(pilot.hexint(self.corpus["memory"]["ctx_size"]), 0x140)

    def test_rows_are_the_declared_cross_product(self) -> None:
        expected = []
        for slot in SLOTS:
            for dword in BUFFER_DWORDS:
                expected.append((slot, "length_from_buffer", BUFFER_MARKER, dword))
            for selector in [value for value in SELECTORS if value != BUFFER_MARKER]:
                expected.append((slot, "explicit_length", selector, None))
        self.assertEqual([_row_key(row) for row in self.corpus["scenarios"]], expected)

    def test_row_identifiers_are_unique_and_ordered(self) -> None:
        ids = [row["id"] for row in self.corpus["scenarios"]]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(ids, sorted(ids))

    def test_every_row_is_a_well_formed_input(self) -> None:
        for row in self.corpus["scenarios"]:
            tag = row["id"]
            r9d = pilot.hexint(row["r9d"])
            self.assertIn(row["slot"], SLOTS, tag)
            self.assertEqual(row["path"], diff.path_label(r9d), tag)
            if r9d == BUFFER_MARKER:
                # The buffer path needs the length dword; its absence would be a missing input.
                self.assertIsNotNone(row["buffer_first_dword"], tag)
            else:
                # ... and the explicit path must not read [RSI] at all, so no buffer is
                # constructed: a stray read of it would be caught as uninitialized state.
                self.assertIsNone(row["buffer_first_dword"], tag)

    def test_the_corpus_contains_every_re0_pilot_row(self) -> None:
        """The pilot's hand-derived expectations still anchor this corpus's boundary."""
        frozen = {_row_key(row) for row in pilot.load_scenarios()["scenarios"]}
        missing = frozen - {_row_key(row) for row in self.corpus["scenarios"]}
        self.assertEqual(missing, set(), f"the differential corpus drops pilot rows: {sorted(missing)}")

    def test_the_compared_region_holds_every_declared_write(self) -> None:
        """A field outside the common window would be silently dropped from the comparison."""
        start, end = diff.common_window(self.corpus)
        for slot in SLOTS:
            for name, (offset, width) in diff.declared_fields(self.corpus, slot).items():
                self.assertGreaterEqual(offset, start, f"slot {slot} {name}")
                self.assertLessEqual(offset + width, end, f"slot {slot} {name}")
        # And the window is the declared intersection, not something that drifts with the layout.
        self.assertEqual((start, end), (0xB8, diff.CONTEXT_SIZE))

    def test_the_corpus_limits_are_differential(self) -> None:
        limits = " ".join(self.corpus["limits"])
        self.assertIn("P-code", limits)
        self.assertIn("0xb8", limits)


@unittest.skipUnless(REFERENCE_PRESENT, "committed RE2 reference not present")
class ReferenceRecordTests(unittest.TestCase):
    """The committed reference is validated in CI without Ghidra, a project or the binary."""

    def setUp(self) -> None:
        self.reference = diff.load_reference()

    def test_reference_validates_against_the_frozen_corpus(self) -> None:
        self.assertEqual(diff.validate_reference(self.reference), [])

    def test_determinism_block_hashes_the_canonical_block(self) -> None:
        determinism = self.reference["determinism"]
        digests = determinism["canonical_sha256"]
        self.assertEqual(determinism["runs"], 2)
        self.assertEqual(len(digests), 2)
        self.assertEqual(digests[0], digests[1])
        self.assertTrue(determinism["identical"])
        self.assertEqual(pilot.canonical_digest(self.reference["canonical"]), digests[0])

    def test_the_reference_binds_the_frozen_corpus_digest(self) -> None:
        self.assertEqual(self.reference["canonical"]["scenarios_sha256"], diff.corpus_sha256())

    def test_both_paths_are_recorded_qualified(self) -> None:
        results = self.reference["canonical"]["results"]
        self.assertEqual(len(results), len(diff.load_corpus()["scenarios"]))
        self.assertEqual({result["path"] for result in results},
                         {"length_from_buffer", "explicit_length"})
        self.assertTrue(all(result["qualified"] for result in results))


def _resign(reference: dict) -> dict:
    """Re-record the canonical digest after a mutation.

    A forged record is self-consistent: it hashes what it carries. That is the point - the
    mutation must be caught by the semantic checks, not by a stale digest.
    """
    digest = pilot.canonical_digest(reference["canonical"])
    reference["determinism"]["canonical_sha256"] = [digest, digest]
    return reference


@unittest.skipUnless(REFERENCE_PRESENT, "committed RE2 reference not present")
class ForgedReferenceTests(unittest.TestCase):
    """Mutations a record could carry while still hashing to its own digests.

    These are the checks the record's *validator* owns: that it describes this corpus, this
    binary, and a well-formed execution. A byte-level forgery of a recorded window is not one of
    them - this corpus freezes no expected values, so nothing in the validator can contradict a
    recorded byte - and `ComparatorSensitivityTests` is where that forgery is caught.
    """

    def setUp(self) -> None:
        self.reference = diff.load_reference()

    def failures(self, mutate) -> list[str]:
        reference = copy.deepcopy(self.reference)
        mutate(reference)
        return diff.validate_reference(_resign(reference))

    def assert_rejected(self, failures: list[str], keyword: str) -> None:
        self.assertTrue(failures, "the mutation was accepted")
        self.assertTrue(any(keyword in message for message in failures),
                        f"rejected, but not for {keyword!r}: {failures[:5]}")

    def test_resigning_an_unmutated_reference_still_validates(self) -> None:
        # Guards the helper: only the mutation may cause a rejection.
        self.assertEqual(diff.validate_reference(_resign(copy.deepcopy(self.reference))), [])

    def test_a_reference_from_another_corpus_is_rejected(self) -> None:
        def mutate(reference: dict) -> None:
            reference["canonical"]["scenarios_sha256"] = "0" * 64

        self.assert_rejected(self.failures(mutate), "byte-for-byte")

    def test_a_reference_from_another_binary_is_rejected(self) -> None:
        def mutate(reference: dict) -> None:
            reference["canonical"]["binary_sha256"] = "0" * 64

        self.assert_rejected(self.failures(mutate), "pinned target")

    def test_a_dropped_row_is_rejected(self) -> None:
        def mutate(reference: dict) -> None:
            reference["canonical"]["results"].pop()

        self.assert_rejected(self.failures(mutate), "ids or their order")

    def test_reordered_rows_are_rejected(self) -> None:
        def mutate(reference: dict) -> None:
            results = reference["canonical"]["results"]
            results[0], results[1] = results[1], results[0]

        self.assert_rejected(self.failures(mutate), "ids or their order")

    def test_a_changed_recorded_selector_is_rejected(self) -> None:
        def mutate(reference: dict) -> None:
            result = reference["canonical"]["results"][0]
            result["r9d"] = "0x00000000"

        self.assert_rejected(self.failures(mutate), "r9d")

    def test_a_wrong_path_label_is_rejected(self) -> None:
        def mutate(reference: dict) -> None:
            result = reference["canonical"]["results"][-1]
            result["path"] = "length_from_buffer"

        self.assert_rejected(self.failures(mutate), "path label")

    def test_a_missing_declared_store_is_rejected(self) -> None:
        def mutate(reference: dict) -> None:
            reference["canonical"]["results"][0]["stores"].pop()

        self.assert_rejected(self.failures(mutate), "store at ctx+")

    def test_an_unqualified_row_is_rejected(self) -> None:
        def mutate(reference: dict) -> None:
            result = reference["canonical"]["results"][0]
            result["qualified"] = False
            result["reasons"] = ["forged"]

        self.assert_rejected(self.failures(mutate), "not qualified")

    def test_an_uninitialized_read_is_rejected(self) -> None:
        def mutate(reference: dict) -> None:
            reference["canonical"]["results"][0]["uninitialized_reads"] = [
                ["ram", "0x300000000", "4", "not in the constructed state"]
            ]

        self.assert_rejected(self.failures(mutate), "uninitialized")

    def test_a_changed_label_is_rejected(self) -> None:
        def mutate(reference: dict) -> None:
            reference["statement"] = "compared with a hand-written expectation"

        self.assert_rejected(self.failures(mutate), "differential label")

    def test_changed_limits_are_rejected(self) -> None:
        def mutate(reference: dict) -> None:
            reference["limits"] = reference["limits"][:-1]

        self.assert_rejected(self.failures(mutate), "limits")


@unittest.skipUnless(REFERENCE_PRESENT, "committed RE2 reference not present")
class ComparatorTests(NeedsHarness):
    """What the comparison itself must catch: this is the gate that carries the evidence."""

    def setUp(self) -> None:
        self.corpus = diff.load_corpus()
        self.reference = diff.load_reference()

    def row_failures(self, mutate) -> list[str]:
        """One row's recorded original, mutated, against that row's reconstruction."""
        assert HARNESS is not None
        row = self.corpus["scenarios"][0]
        result = copy.deepcopy(self.reference["canonical"]["results"][0])
        mutate(result)
        recon = diff.run_reconstruction(HARNESS, row, self.corpus)
        return diff.compare_row(row, result, recon, self.corpus)

    def test_an_altered_recorded_word_count_is_rejected(self) -> None:
        """The recorded original is the reference; changing it must break the comparison."""
        def mutate(result: dict) -> None:
            index = _window_index(self.corpus, 0, "word_count")
            result["ctx_window_after"] = _flip_byte(result["ctx_window_after"], index)

        failures = self.row_failures(mutate)
        self.assertTrue(failures)
        self.assertEqual({message.split(":")[0] for message in failures}, {"d001"})
        self.assertIn("word_count differs", failures[0])

    def test_a_recorded_write_outside_the_declared_fields_is_rejected(self) -> None:
        """A canary byte the original never writes, changed in the record, is not a difference
        the reconstruction can match - and the comparison must say so, not average it away."""
        def mutate(result: dict) -> None:
            result["ctx_window_after"] = _flip_byte(result["ctx_window_after"], 0)

        failures = self.row_failures(mutate)
        self.assertTrue(failures)
        self.assertIn("outside the declared fields", " ".join(failures))

    def test_a_reference_row_that_is_not_qualified_is_refused(self) -> None:
        def mutate(result: dict) -> None:
            result["qualified"] = False
            result["reasons"] = ["forged"]

        failures = self.row_failures(mutate)
        self.assertEqual({message.split(":")[0] for message in failures}, {"d001"})
        self.assertIn("not qualified", failures[0])

    def test_a_missing_corpus_row_in_the_reference_is_reported(self) -> None:
        assert HARNESS is not None
        reference = copy.deepcopy(self.reference)
        reference["canonical"]["results"].pop()
        failures = diff.compare_corpus(HARNESS, self.corpus, reference)
        self.assertTrue(failures)
        self.assertTrue(any("no row for this corpus input" in message for message in failures))


@unittest.skipUnless(REFERENCE_PRESENT, "committed RE2 reference not present")
class DifferentialTests(NeedsHarness):
    def test_the_reconstruction_matches_the_recorded_original_on_every_row(self) -> None:
        corpus = diff.load_corpus()
        failures = diff.compare_corpus(HARNESS, corpus, diff.load_reference())  # type: ignore[arg-type]
        self.assertEqual(failures, [], "\n".join(failures[:20]))
        # The comparison is the evidence, so it must have covered the whole frozen corpus.
        self.assertGreater(len(corpus["scenarios"]), 400)


@unittest.skipUnless(REFERENCE_PRESENT, "committed RE2 reference not present")
class NearMissTests(NeedsHarness):
    """The controls: a deliberately wrong reconstruction must be caught, on its own path only."""

    def test_a_signed_division_on_the_buffer_path_is_rejected(self) -> None:
        self._rejected(NEAR_MISSES["signed_division_on_the_buffer_path"],
                       name="near_miss_buffer", path="length_from_buffer")

    def test_the_missing_wrap_on_the_selector_path_is_rejected(self) -> None:
        self._rejected(NEAR_MISSES["missing_wrap_on_the_selector_path"],
                       name="near_miss_selector", path="explicit_length")

    def _rejected(self, mutation: tuple[str, str], *, name: str, path: str) -> None:
        assert WORKDIR is not None
        source_dir = _mutated_source_dir(name, mutation)
        harness = _compile_harness(WORKDIR, source_dir, f"harness_{name}")
        corpus = diff.load_corpus()
        failures = diff.compare_corpus(harness, corpus, diff.load_reference())
        self.assertTrue(failures, f"the comparator accepted the {name} reconstruction")
        rows = {row["id"]: row for row in corpus["scenarios"]}
        failed = {message.split(":")[0] for message in failures}
        self.assertTrue(failed <= set(rows))
        self.assertEqual({rows[tag]["path"] for tag in failed}, {path},
                         f"{name}: the rejection reached the wrong path")
        # ... and the faithful build was accepted, so the comparator is not simply refusing
        # everything: the positive case is exercised in DifferentialTests.


@unittest.skipUnless(
    ghidra_available() and PROJECT_PRESENT and BINARY_PRESENT and REFERENCE_PRESENT,
    "GHIDRA_INSTALL, the analysed project, the pinned binary or the reference not present",
)
class ReferenceRunTests(unittest.TestCase):
    """Re-runs the frozen corpus and must reproduce the committed canonical block."""

    def test_two_runs_reproduce_the_committed_reference(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            reference = diff.run_twice(Path(work))
        self.assertTrue(reference["determinism"]["identical"], "two runs disagree")
        committed = diff.load_reference()
        self.assertEqual(pilot.canonical_digest(reference["canonical"]),
                         pilot.canonical_digest(committed["canonical"]),
                         "a re-run does not reproduce the committed canonical block")
        self.assertEqual(diff.validate_reference(reference), [])


@unittest.skipUnless(
    ghidra_available() and PROJECT_PRESENT and BINARY_PRESENT,
    "GHIDRA_INSTALL, the analysed project or the pinned binary not present",
)
class ReferenceNegativeControlTests(unittest.TestCase):
    """A buffer-path row with its required input withheld must fail qualification.

    The explicit-path rows of the corpus also have no buffer, and those qualify: the difference is
    that the buffer path reads [RSI]. Both cases together show the qualification check is not
    vacuous.
    """

    def test_withheld_required_input_fails_qualification(self) -> None:
        broken = copy.deepcopy(diff.load_corpus())
        target = next(row for row in broken["scenarios"] if row["path"] == "length_from_buffer")
        target["buffer_first_dword"] = None

        with tempfile.TemporaryDirectory() as work:
            corpus_path = Path(work) / "scenarios.json"
            corpus_path.write_text(json.dumps(broken, indent=2) + "\n")
            runs = [json.loads(pilot.run_pilot(Path(work) / f"run{index}.json",
                                               scenarios_path=corpus_path).read_text())
                    for index in (1, 2)]
            reference = pilot.build_receipt(runs[0], runs[1], scenarios_path=corpus_path,
                                            schema=diff.REFERENCE_SCHEMA, statement=diff.STATEMENT)
            failures = diff.validate_reference(reference, broken)

        result = next(row for row in reference["canonical"]["results"] if row["id"] == target["id"])
        self.assertTrue(result["uninitialized_reads"], "the withheld input was not noticed")
        self.assertIn("uninitialized", " ".join(result["reasons"]))
        self.assertFalse(result["qualified"])
        self.assertTrue(any(target["id"] in message for message in failures),
                        f"the validator accepted a run with a missing required input: {failures[:5]}")


if __name__ == "__main__":
    unittest.main()
