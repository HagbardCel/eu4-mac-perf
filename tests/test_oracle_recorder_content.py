"""P4.9: bounded resource-content capture in the native recorder core.

The recorder previously captured resource *references* and lifetimes but never
their contents, and its transcript never carried a hook's ABI arguments - so no
downstream translator could recover an operation's parameters. P4.9 adds both, in a
versioned observation export that leaves the released accounting transcript alone.

The properties under test: content is whole-span or nothing; the arena is bounded,
reserved by compare-and-swap, and never over-committed; default-off captures no
content at all and preserves the historical goldens byte-for-byte; and an
observation that declared content it did not get is *incomplete*, never complete.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from oracle.recorder import harness


def _counters(scenario: dict) -> dict[str, int]:
    return {key: int(value) for key, value in scenario["counters"].items()}


def _captured_spans(scenario: dict) -> list[dict]:
    spans = []
    for observation in scenario["observations"]:
        for span in observation["content"]:
            if span["status"] == "captured":
                spans.append(span)
    return spans


class ContentCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="oracle-p4-content-")
        cls.exe = harness.build("content", Path(cls._tmp) / "build")
        cls.transcript = harness.run_transcript(cls.exe)
        cls.parsed = harness.parse_export_transcript(cls.transcript)
        cls.scenarios = {s["name"]: s for s in cls.parsed["scenarios"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # --- the export ---------------------------------------------------------------

    def test_reproduces_the_committed_golden_export(self):
        self.assertEqual(self.transcript, harness.golden_transcript("content"))

    def test_output_is_deterministic(self):
        self.assertEqual(harness.run_transcript(self.exe), self.transcript)

    def test_export_is_well_formed(self):
        self.assertEqual(self.parsed["version"], "v1")
        self.assertEqual(
            sorted(self.scenarios),
            ["content-ample", "content-concurrent", "content-concurrent-bounded",
             "content-exact", "content-off", "content-policy", "content-short",
             "content-two-spans", "content-two-spans-lost", "content-zero-length"])

    def test_the_export_carries_the_abi_arguments_the_translator_needs(self):
        # The released accounting transcript omits args; this export must not. A
        # translation rule selects a hook and maps argument positions to fields, so
        # losing them would make the content unusable.
        observation = self._observation("content-ample", hook=2, sequence=2)
        self.assertEqual(observation["nargs"], 3)
        self.assertEqual(observation["args"], [0, 5, 4])
        self.assertEqual(observation["ncontext"], 6)
        for scenario in self.scenarios.values():
            for obs in scenario["observations"]:
                with self.subTest(scenario=scenario["name"], seq=obs["sequence"]):
                    self.assertEqual(len(obs["args"]), obs["nargs"])
                    self.assertEqual(len(obs["context"]), obs["ncontext"])

    def test_the_declared_capture_point_distinguishes_pre_state_from_post_state(self):
        # Reads observe the state the call was entered with; creates and updates
        # declare the state they left behind.
        for hook, point in ((1, 0), (2, 0), (3, 1)):
            with self.subTest(hook=hook):
                observed = [o for o in self.scenarios["content-ample"]["observations"]
                            if o["hook_id"] == hook]
                self.assertTrue(observed)
                for obs in observed:
                    self.assertEqual(obs["point"], point)

    # --- default off --------------------------------------------------------------

    def test_off_by_default_declares_no_content_at_all(self):
        counters = _counters(self.scenarios["content-off"])
        self.assertEqual(counters["content_bytes_declared"], 0)
        self.assertEqual(counters["content_bytes_captured"], 0)
        self.assertEqual(counters["content_bytes_lost"], 0)
        self.assertEqual(counters["content_spans_lost"], 0)
        for obs in self.scenarios["content-off"]["observations"]:
            with self.subTest(seq=obs["sequence"]):
                self.assertEqual(obs["content"], [])

    def test_content_capture_does_not_change_completeness_when_it_is_off(self):
        # Completeness must be driven by the call, never by the un-configured arena:
        # only the failed create and the stale read are incomplete.
        for obs in self.scenarios["content-off"]["observations"]:
            with self.subTest(seq=obs["sequence"]):
                self.assertEqual(obs["complete"], 1 if obs["result"] == 0 else 0)

    def test_the_historical_goldens_are_unchanged(self):
        for target in ("synthetic", "recorder"):
            with self.subTest(target=target):
                self.assertEqual(harness.reproduce(target),
                                 harness.golden_transcript(target))

    # --- bounded capture ----------------------------------------------------------

    def test_an_ample_arena_captures_every_declared_byte(self):
        counters = _counters(self.scenarios["content-ample"])
        self.assertEqual(counters["content_bytes_declared"], 12)
        self.assertEqual(counters["content_bytes_captured"], 12)
        self.assertEqual(counters["content_bytes_lost"], 0)
        self.assertEqual(counters["content_spans_lost"], 0)
        self.assertEqual(counters["captured"], 6)
        self.assertEqual(counters["incomplete"], 2)

    def test_an_arena_exactly_the_declared_size_loses_nothing(self):
        counters = _counters(self.scenarios["content-exact"])
        self.assertEqual(counters["content_bytes_declared"], 12)
        self.assertEqual(counters["content_bytes_captured"], 12)
        self.assertEqual(counters["content_bytes_lost"], 0)
        self.assertEqual(self.scenarios["content-exact"]["arena"]["capacity"], 12)

    def test_one_byte_short_loses_the_last_span_whole(self):
        counters = _counters(self.scenarios["content-short"])
        self.assertEqual(counters["content_bytes_captured"], 10)
        self.assertEqual(counters["content_bytes_lost"], 2)
        self.assertEqual(counters["content_spans_lost"], 1)
        # The declaring observation keeps its partial evidence and its loss is exact.
        last = self._observation("content-short", hook=2, sequence=5)
        self.assertEqual(last["complete"], 0)
        self.assertEqual(last["content"], [{"status": "lost", "length": 2, "bytes": None}])
        # No span was partially captured.
        self.assertEqual([s["length"] for s in _captured_spans(self.scenarios["content-short"])],
                         [4, 4, 1, 1])

    def test_missing_content_downgrades_an_otherwise_identical_observation(self):
        # Same hook, same arguments, same context - the only difference is whether
        # the declared content fit the arena.
        ample = self._observation("content-ample", hook=2, sequence=5)
        short = self._observation("content-short", hook=2, sequence=5)
        self.assertEqual(ample["args"], short["args"])
        self.assertEqual(ample["context"], short["context"])
        self.assertEqual(ample["complete"], 1)
        self.assertEqual(short["complete"], 0)
        self.assertEqual(ample["content"], [{"status": "captured", "length": 2,
                                             "bytes": bytes.fromhex("0608")}])

    def test_one_observation_two_spans_keeps_the_first_and_loses_the_second(self):
        # The declared span that fit stays inspectable; the one that did not is lost
        # whole; the observation is incomplete and its byte accounting reconciles.
        counters = _counters(self.scenarios["content-two-spans"])
        self.assertEqual(counters["content_bytes_declared"], 8)
        self.assertEqual(counters["content_bytes_captured"], 6)
        self.assertEqual(counters["content_bytes_lost"], 2)
        self.assertEqual(counters["content_spans_lost"], 1)
        pair = self._observation("content-two-spans", hook=6, sequence=2)
        self.assertEqual(pair["complete"], 0)
        self.assertEqual([span["status"] for span in pair["content"]],
                         ["captured", "lost"])
        self.assertEqual(pair["content"][0]["bytes"], bytes.fromhex("0000"))
        self.assertEqual(pair["content"][1]["length"], 2)
        self.assertEqual(counters["captured"], 1)
        self.assertEqual(counters["incomplete"], 1)

    def test_a_zero_length_span_declares_nothing_and_is_never_a_loss(self):
        counters = _counters(self.scenarios["content-zero-length"])
        self.assertEqual(counters["content_bytes_declared"], 4)
        self.assertEqual(counters["content_bytes_captured"], 4)
        self.assertEqual(counters["content_bytes_lost"], 0)
        self.assertEqual(counters["content_spans_lost"], 0)
        observing = self._observation("content-zero-length", hook=3, sequence=2)
        self.assertEqual(observing["complete"], 1)
        self.assertEqual(observing["content"], [])

    def test_policy_excluded_content_is_not_a_budget_loss(self):
        # The updates are deep-captured; the creates and reads are counted only, so
        # the content they declare is neither counted nor copied - and nothing is
        # reported as lost.
        counters = _counters(self.scenarios["content-policy"])
        self.assertEqual(counters["selected"], 2)
        self.assertEqual(counters["unselected"], 6)
        self.assertEqual(counters["content_bytes_declared"], 6)
        self.assertEqual(counters["content_bytes_captured"], 6)
        self.assertEqual(counters["content_bytes_lost"], 0)
        self.assertEqual(counters["content_spans_lost"], 0)
        self.assertEqual({o["hook_id"] for o in self.scenarios["content-policy"]["observations"]},
                         {2})

    def test_an_oversize_content_capacity_is_rejected_not_truncated(self):
        config: dict[str, str] = {}
        for line in self.parsed["config"]:
            config.update(line)
        self.assertEqual(config["oversize_content_rejected"], "1")

    # --- the two independent identities -------------------------------------------

    def test_byte_and_event_accounting_are_separate_and_both_hold(self):
        for name, scenario in self.scenarios.items():
            counters = _counters(scenario)
            with self.subTest(scenario=name):
                self.assertEqual(scenario["accounting"], "ok")
                self.assertEqual(counters["intercepted"],
                                 counters["selected"] + counters["unselected"])
                self.assertEqual(counters["selected"],
                                 counters["captured"] + counters["incomplete"] + counters["lost"])
                self.assertEqual(counters["content_bytes_declared"],
                                 counters["content_bytes_captured"] + counters["content_bytes_lost"])

    def test_one_event_can_account_for_several_lost_spans(self):
        # Byte loss and event loss are not the same equation: one incomplete
        # observation here accounts for two lost spans.
        counters = _counters(self.scenarios["content-two-spans-lost"])
        self.assertEqual(counters["content_spans_lost"], 2)
        self.assertEqual(counters["incomplete"], 1)
        self.assertEqual(counters["captured"], 1)
        self.assertEqual(counters["content_bytes_declared"], 8)
        self.assertEqual(counters["content_bytes_captured"], 4)
        self.assertEqual(counters["content_bytes_lost"], 4)
        pair = self._observation("content-two-spans-lost", hook=6, sequence=2)
        self.assertEqual(pair["content"],
                         [{"status": "lost", "length": 2, "bytes": None},
                          {"status": "lost", "length": 2, "bytes": None}])

    def test_byte_loss_and_event_loss_are_independent_quantities(self):
        # An event buffer that withheld nothing can still have lost content bytes:
        # the two budgets and the two loss accounts are separate.
        counters = _counters(self.scenarios["content-concurrent-bounded"])
        self.assertEqual(counters["content_bytes_declared"],
                         counters["content_bytes_captured"] + counters["content_bytes_lost"])
        self.assertEqual(counters["selected"],
                         counters["captured"] + counters["incomplete"] + counters["lost"])
        self.assertEqual(counters["content_bytes_lost"], 28)
        self.assertEqual(counters["lost"], 0)
        self.assertNotEqual(counters["content_bytes_lost"], counters["lost"])

    # --- the arena ----------------------------------------------------------------

    def test_no_two_captured_spans_share_a_byte(self):
        for name, scenario in self.scenarios.items():
            with self.subTest(scenario=name):
                self.assertEqual(scenario["arena"]["overlaps"], 0)
                self.assertEqual(scenario["arena"]["out_of_range"], 0)

    def test_the_arena_report_agrees_with_the_byte_counters(self):
        for name, scenario in self.scenarios.items():
            counters = _counters(scenario)
            with self.subTest(scenario=name):
                self.assertEqual(scenario["arena"]["used"], counters["content_bytes_captured"])
                self.assertLessEqual(scenario["arena"]["used"], scenario["arena"]["capacity"])
                if scenario["observation_count"] is not None:
                    self.assertEqual(scenario["arena"]["intervals"],
                                     len(_captured_spans(scenario)))

    # --- concurrency --------------------------------------------------------------

    def test_concurrent_content_capture_is_exact(self):
        counters = _counters(self.scenarios["content-concurrent"])
        self.assertEqual(counters["intercepted"], 12)
        self.assertEqual(counters["content_bytes_declared"], 36)
        self.assertEqual(counters["content_bytes_captured"], 36)
        self.assertEqual(counters["content_bytes_lost"], 0)
        self.assertEqual(counters["rejected"], 0)
        # Each thread declared bytes derived from its own seed, so no two threads'
        # contents collide.
        contents = {o["content"][0]["bytes"] for o in self.scenarios["content-concurrent"]["observations"]
                    if o["hook_id"] == 2}
        self.assertEqual(len(contents), 4)

    def test_concurrent_loss_fills_the_arena_exactly_and_reconciles(self):
        # Four threads racing into eight arena bytes: the arena ends exactly full, so
        # the totals are deterministic even though which thread wins is not.
        counters = _counters(self.scenarios["content-concurrent-bounded"])
        arena = self.scenarios["content-concurrent-bounded"]["arena"]
        self.assertEqual(arena["capacity"], 8)
        self.assertEqual(counters["content_bytes_captured"], 8)
        self.assertEqual(counters["content_bytes_declared"], 36)
        self.assertEqual(counters["content_bytes_lost"], 28)
        self.assertEqual(counters["content_spans_lost"], 10)
        self.assertEqual(counters["captured"], 2)
        self.assertEqual(counters["incomplete"], 10)
        self.assertEqual(arena["used"], arena["capacity"])
        self.assertEqual(arena["overlaps"], 0)

    # --- the arguments are what distinguishes two calls ---------------------------

    def test_calls_sharing_a_context_are_distinguished_by_their_arguments(self):
        # Two reads of one resource version: identical context, different offsets and
        # therefore different observed bytes. A translation table must not merge them.
        first = self._observation("content-ample", hook=3, sequence=3)
        second = self._observation("content-ample", hook=3, sequence=4)
        self.assertEqual(first["context"], second["context"])
        self.assertNotEqual(first["args"], second["args"])
        self.assertNotEqual(first["content"][0]["bytes"], second["content"][0]["bytes"])
        self.assertEqual(first["content"][0]["bytes"], bytes.fromhex("05"))
        self.assertEqual(second["content"][0]["bytes"], bytes.fromhex("08"))

    def test_the_captured_bytes_are_the_subject_state(self):
        # create(4) zeroes the resource, update(seed 5, span 4) adds 5,6,7,8, then
        # update(seed 1, span 2) adds 1,2 - the export must carry those bytes exactly.
        self.assertEqual(
            [s["bytes"] for s in _captured_spans(self.scenarios["content-ample"])],
            [bytes.fromhex("00000000"), bytes.fromhex("05060708"), bytes.fromhex("05"),
             bytes.fromhex("08"), bytes.fromhex("0608")])

    # --- helpers ------------------------------------------------------------------

    def _observation(self, scenario: str, *, hook: int, sequence: int) -> dict:
        for obs in self.scenarios[scenario]["observations"]:
            if obs["hook_id"] == hook and obs["sequence"] == sequence:
                return obs
        raise AssertionError(f"no {scenario} observation hook={hook} seq={sequence}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
