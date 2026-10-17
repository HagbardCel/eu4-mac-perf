"""P3 slice: harness, corpus and completeness comparator (offline, no model)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from oracle.slice import compare, corpus, gen_fixtures, harness

SLICE_DIR = Path(__file__).resolve().parents[1] / "oracle" / "slice"

_TMP = None
_REF = None
_TARGET = None


def setUpModule() -> None:
    global _TMP, _REF, _TARGET
    _TMP = tempfile.TemporaryDirectory()
    root = Path(_TMP.name)
    _REF = harness.build(SLICE_DIR / "reference.c", root / "build_ref")
    _TARGET = harness.build(SLICE_DIR / "target.c", root / "build_tgt")


def tearDownModule() -> None:
    assert _TMP is not None
    _TMP.cleanup()


def _build_source(source: str, name: str) -> harness.Build:
    assert _TMP is not None
    path = Path(_TMP.name) / f"{name}.c"
    path.write_text(source, encoding="utf-8")
    return harness.build(path, Path(_TMP.name) / f"build_{name}")


def _run_corpus(build: harness.Build, set_name: str) -> dict:
    diagnostics = [
        harness.evaluate(build, fixture, run_id=f"test-{set_name}")
        for fixture in corpus.load_corpus(set_name)
    ]
    return compare.verdict(diagnostics)


class BaselineQualificationTests(unittest.TestCase):
    """The reference must be qualified before any agent runs."""

    def test_reference_passes_visible_and_held_out(self):
        self.assertEqual(gen_fixtures.check_fixtures(), 0)

    def test_defective_target_fails_visible_suite(self):
        self.assertEqual(_run_corpus(_TARGET, corpus.VISIBLE)["status"], compare.STATUS_MISMATCH)

    def test_defective_target_fails_held_out_suite(self):
        self.assertEqual(_run_corpus(_TARGET, corpus.HELD_OUT)["status"], compare.STATUS_MISMATCH)

    def test_defect_is_a_memory_write_divergence(self):
        fixture = next(f for f in corpus.load_corpus(corpus.VISIBLE) if f.fixture_id == "vis-000")
        diagnostic = harness.evaluate(_TARGET, fixture, run_id="t")
        self.assertEqual(diagnostic["status"], compare.STATUS_MISMATCH)
        self.assertEqual(diagnostic["first_divergence"]["domain"], "memory")
        self.assertEqual(diagnostic["first_divergence"]["byte_offset"], 4)


class HeldOutIndependenceTests(unittest.TestCase):
    """Held-out values include wraparound cases the visible set does not."""

    _CLAMPING = """
#include "synth.h"
int synth_apply(struct synth_state *s, int mode, int arg) {
    switch (mode) {
    case 0:
        s->dirty = 1; synth_emit(SYNTH_FIELD_DIRTY, 1);
        s->value = arg; synth_emit(SYNTH_FIELD_VALUE, arg); return 0;
    case 1: {
        long long c = (long long)s->counter + (long long)arg;
        if (c > 2147483647LL) c = 2147483647LL;
        if (c < -2147483648LL) c = -2147483648LL;
        s->counter = (int)c; synth_emit(SYNTH_FIELD_COUNTER, s->counter); return s->counter;
    }
    case 2:
        s->value = arg; synth_emit(SYNTH_FIELD_VALUE, arg); return arg;
    default:
        return -1;
    }
}
"""

    def test_clamping_candidate_passes_visible_but_fails_held_out(self):
        build = _build_source(self._CLAMPING, "clamping")
        self.assertEqual(_run_corpus(build, corpus.VISIBLE)["status"], compare.STATUS_PASS)
        self.assertEqual(_run_corpus(build, corpus.HELD_OUT)["status"], compare.STATUS_MISMATCH)


class ComparatorCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.fixture = next(
            f for f in corpus.load_corpus(corpus.VISIBLE) if f.fixture_id == "vis-000"
        )
        self.expected = self.fixture.expected

    def _compare(self, actual, exit_kind="normal", note=None):
        return compare.compare(
            self.expected,
            actual,
            function_id=self.fixture.function_id,
            fixture_id=self.fixture.fixture_id,
            run_id="t",
            exit_kind=exit_kind,
            note=note,
        )

    def test_complete_actual_passes(self):
        self.assertEqual(self._compare(self.expected)["status"], compare.STATUS_PASS)

    def test_missing_domain_is_incomplete_not_pass(self):
        actual = {k: v for k, v in self.expected.items() if k != "post_memory"}
        self.assertEqual(self._compare(actual)["status"], compare.STATUS_INCOMPLETE)

    def test_malformed_hex_is_incomplete(self):
        actual = dict(self.expected, post_memory={"state": "zz00"})
        self.assertEqual(self._compare(actual)["status"], compare.STATUS_INCOMPLETE)

    def test_effects_must_be_structured(self):
        actual = dict(self.expected, effects=["nope"])
        self.assertEqual(self._compare(actual)["status"], compare.STATUS_INCOMPLETE)

    def test_crash_is_not_a_mismatch(self):
        self.assertEqual(self._compare(None, exit_kind="crash")["status"], compare.STATUS_CRASH)

    def test_timeout_is_not_a_mismatch(self):
        self.assertEqual(self._compare(None, exit_kind="timeout")["status"], compare.STATUS_TIMEOUT)

    def test_real_mismatch_is_mismatch(self):
        actual = dict(self.expected, post_memory={"state": "000000000000000000000000"})
        self.assertEqual(self._compare(actual)["status"], compare.STATUS_MISMATCH)

    def test_verdict_requires_every_fixture_to_pass(self):
        diagnostics = [
            self._compare(self.expected),
            self._compare(None, exit_kind="crash"),
        ]
        aggregate = compare.verdict(diagnostics)
        self.assertEqual(aggregate["status"], compare.STATUS_CRASH)
        self.assertEqual(aggregate["passed"], 1)


class HarnessTests(unittest.TestCase):
    def test_parse_rejects_missing_terminator(self):
        observation, problems = harness.parse_observation("RET 00000000\nMEM 000000000000000000000000\n")
        self.assertTrue(any("END" in p for p in problems))

    def test_parse_rejects_wrong_state_length(self):
        _, problems = harness.parse_observation(
            "RET 00000000\nMEM 00\nEND 0\n"
        )
        self.assertTrue(any("hex chars" in p for p in problems))

    def test_crashing_candidate_is_classified_as_crash(self):
        build = _build_source(
            '#include "synth.h"\n#include <stdlib.h>\n'
            "int synth_apply(struct synth_state *s, int mode, int arg) "
            "{ (void)s; (void)mode; (void)arg; abort(); }\n",
            "crashing",
        )
        fixture = corpus.load_corpus(corpus.VISIBLE)[0]
        raw, _, _ = harness.observe(build, fixture)
        self.assertEqual(raw.exit_kind, "crash")
        self.assertLess(raw.returncode, 0)

    def test_hanging_candidate_is_timed_out_and_reaped(self):
        build = _build_source(
            '#include "synth.h"\n#include <unistd.h>\n'
            "int synth_apply(struct synth_state *s, int mode, int arg) "
            "{ (void)s; (void)mode; (void)arg; for (;;) sleep(1); return 0; }\n",
            "hanging",
        )
        fixture = corpus.load_corpus(corpus.VISIBLE)[0]
        raw, _, _ = harness.observe(build, fixture, timeout_s=1.0)
        self.assertEqual(raw.exit_kind, "timeout")

    def test_candidate_cannot_inject_driver_lines(self):
        build = _build_source(
            '#include "synth.h"\n#include <stdio.h>\n'
            "int synth_apply(struct synth_state *s, int mode, int arg) "
            "{ (void)s; (void)mode; (void)arg; printf(\"MEM deadbeefdeadbeefdeadbeef\\n\"); return 0; }\n",
            "injecting",
        )
        fixture = corpus.load_corpus(corpus.VISIBLE)[0]
        raw, observation, problems = harness.observe(build, fixture)
        self.assertEqual(raw.exit_kind, "incomplete")
        self.assertTrue(problems)

    def test_candidate_cannot_redefine_header(self):
        """A candidate-supplied synth.h is overwritten by the trusted copy."""
        source = (
            '#include "synth.h"\n'
            "int synth_apply(struct synth_state *s, int mode, int arg) "
            "{ (void)s; (void)mode; (void)arg; return 0; }\n"
        )
        assert _TMP is not None
        root = Path(_TMP.name) / "header_attack"
        root.mkdir(parents=True, exist_ok=True)
        (root / "synth.h").write_text("struct synth_state { char x; };\n", encoding="utf-8")
        (root / "candidate.c").write_text(source, encoding="utf-8")
        build = harness.build(root / "candidate.c", root / "build")
        # The trusted 12-byte layout must be in force.
        fixture = corpus.load_corpus(corpus.VISIBLE)[0]
        raw, observation, _ = harness.observe(build, fixture)
        self.assertEqual(raw.exit_kind, "normal")
        self.assertEqual(len(observation["post_memory"]["state"]), harness.STATE_BYTES * 2)


class CorpusTests(unittest.TestCase):
    def test_sets_are_disjoint_and_non_empty(self):
        visible = corpus.load_corpus(corpus.VISIBLE)
        held = corpus.load_corpus(corpus.HELD_OUT)
        self.assertTrue(visible and held)
        self.assertNotEqual({f.fixture_id for f in visible}, {f.fixture_id for f in held})
        visible_inputs = {tuple(sorted(f.input.items())) for f in visible}
        held_inputs = {tuple(sorted(f.input.items())) for f in held}
        self.assertFalse(visible_inputs & held_inputs)

    def test_manifest_is_content_addressed_and_stable(self):
        fixtures = corpus.load_corpus(corpus.VISIBLE)
        first = corpus.manifest(fixtures, set_name=corpus.VISIBLE)
        second = corpus.manifest(list(reversed(fixtures)), set_name=corpus.VISIBLE)
        self.assertEqual(first, second)
        self.assertEqual(first["count"], len(fixtures))
        self.assertEqual(len(first["sha256"]), 64)

    def test_manifest_changes_when_fixture_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("visible", "held_out"):
                (root / name).mkdir(parents=True, exist_ok=True)
            (root / "visible" / "a.json").write_text(
                json.dumps({
                    "schema_version": "eu4-oracle-fixture/v1",
                    "fixture_id": "a", "function_id": "f", "set": "visible",
                    "input": {"mode": 0, "arg": 1, "dirty": 0, "value": 0, "counter": 0},
                    "expected": {"capture": {"complete": True, "lost_events": 0, "unknown_dependencies": []}},
                }),
                encoding="utf-8",
            )
            doc = json.loads((root / "visible" / "a.json").read_text())
            before = corpus.manifest(corpus.load_corpus("visible", root=root), set_name="visible")
            doc["expected"]["return_hex"] = "00000000"
            (root / "visible" / "a.json").write_text(json.dumps(doc), encoding="utf-8")
            after = corpus.manifest(corpus.load_corpus("visible", root=root), set_name="visible")
            self.assertNotEqual(before["sha256"], after["sha256"])

    def test_rejects_wrong_schema_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "visible"
            root.mkdir(parents=True)
            (root / "bad.json").write_text(
                json.dumps({"schema_version": "nope", "fixture_id": "x", "function_id": "f",
                            "set": "visible", "input": {}, "expected": {}}),
                encoding="utf-8",
            )
            with self.assertRaises(corpus.CorpusError):
                corpus.load_corpus("visible", root=Path(tmp))

    def test_summary_never_exposes_expectations(self):
        fixture = corpus.load_corpus(corpus.VISIBLE)[0]
        summary = fixture.summary()
        self.assertNotIn("expected", summary)
        self.assertNotIn("return_hex", json.dumps(summary))


if __name__ == "__main__":
    unittest.main()
