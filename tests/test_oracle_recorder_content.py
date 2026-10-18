"""P4.9: bounded resource-content capture in the native recorder core.

The recorder previously captured resource *references* and lifetimes but never
their contents, and its transcript never carried a hook's ABI arguments - so no
downstream translator could recover an operation's parameters. P4.9 adds both, in a
versioned observation export that leaves the released accounting transcript alone.

The properties under test:

* content is whole-span or nothing, and the arena is bounded, reserved by
  compare-and-swap, and never over-committed;
* declared content is the *complete* contents of one resource version, never a
  fragment of one - so a digest of it can be published as that version's digest;
* every captured blob is attributable to the resource version it came from, which
  is not always the state the call addressed;
* default-off captures no content at all and preserves the historical goldens
  byte-for-byte;
* a malformed declaration fails closed: one instrumentation fault, counted once,
  declaring no content, never complete, and never read past its own array;
* an observation that declared content it did not get is *incomplete*, never
  complete.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from oracle.recorder import harness, registry


def _counters(scenario: dict) -> dict[str, int]:
    return {key: int(value) for key, value in scenario["counters"].items()}


def _captured_spans(scenario: dict) -> list[dict]:
    spans = []
    for observation in scenario["observations"]:
        for span in observation["content"]:
            if span["status"] == "captured":
                spans.append(span)
    return spans


def _origin(reference_like: list[int]) -> registry.Reference:
    return registry.reference_from_context(reference_like)


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
             "content-exact", "content-malformed", "content-off", "content-policy",
             "content-short", "content-stale-origin", "content-two-spans",
             "content-two-spans-lost", "content-zero-length"])
        self.assertIsNotNone(self.parsed["end"])

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
                    self.assertEqual(len(obs["content"]), obs["nspans"])
                    self.assertEqual(len(obs["origin"]), obs["norigin"])

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
                self.assertEqual(obs["origin"], [])

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
        self.assertEqual(counters["content_bytes_captured"], 8)
        self.assertEqual(counters["content_bytes_lost"], 4)
        self.assertEqual(counters["content_spans_lost"], 1)
        # The declaring observation keeps its declared origin and its loss is exact.
        last = self._observation("content-short", hook=2, sequence=5)
        self.assertEqual(last["complete"], 0)
        self.assertEqual(last["content"], [{"status": "lost", "length": 4, "bytes": None}])
        self.assertEqual(_origin(last["origin"]).version, 2)
        # No span was partially captured.
        self.assertEqual([s["length"] for s in _captured_spans(self.scenarios["content-short"])],
                         [4, 4])

    def test_missing_content_downgrades_an_otherwise_identical_observation(self):
        # Same hook, same arguments, same context - the only difference is whether
        # the declared content fit the arena.
        ample = self._observation("content-ample", hook=2, sequence=5)
        short = self._observation("content-short", hook=2, sequence=5)
        self.assertEqual(ample["args"], short["args"])
        self.assertEqual(ample["context"], short["context"])
        self.assertEqual(ample["complete"], 1)
        self.assertEqual(short["complete"], 0)
        self.assertEqual(ample["content"], [{"status": "captured", "length": 4,
                                             "bytes": bytes.fromhex("06080708")}])

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
        self.assertEqual(observing["fault"], "none")

    def test_policy_excluded_content_is_not_a_budget_loss(self):
        # The updates are deep-captured; the creates and reads are counted only, so
        # the content they declare is neither counted nor copied - and nothing is
        # reported as lost.
        counters = _counters(self.scenarios["content-policy"])
        self.assertEqual(counters["selected"], 2)
        self.assertEqual(counters["unselected"], 6)
        self.assertEqual(counters["content_bytes_declared"], 8)
        self.assertEqual(counters["content_bytes_captured"], 8)
        self.assertEqual(counters["content_bytes_lost"], 0)
        self.assertEqual(counters["content_spans_lost"], 0)
        self.assertEqual({o["hook_id"] for o in self.scenarios["content-policy"]["observations"]},
                         {2})

    def test_an_oversize_content_capacity_is_rejected_not_truncated(self):
        config: dict[str, str] = {}
        for line in self.parsed["config"]:
            config.update(line)
        self.assertEqual(config["oversize_content_rejected"], "1")

    # --- content is a complete resource version -----------------------------------

    def test_content_is_the_complete_post_update_contents_not_the_mutated_prefix(self):
        # update(seed 1, span 2) mutates the first two bytes and leaves the last two
        # alone. A two-byte fragment would be a digest of something no resource ever
        # held, so the whole four-byte version is what is declared.
        update = self._observation("content-ample", hook=2, sequence=5)
        self.assertEqual(update["args"], [0, 1, 2])
        self.assertEqual(update["content"],
                         [{"status": "captured", "length": 4,
                           "bytes": bytes.fromhex("06080708")}])

    def test_a_read_declares_no_content_because_a_byte_is_not_a_version(self):
        # The observed byte stays recoverable from the version the read's context
        # names and the offset in its arguments; publishing one byte under a
        # whole-version contract is exactly what must not happen.
        read = self._observation("content-ample", hook=3, sequence=3)
        self.assertEqual(read["content"], [])
        self.assertEqual(read["origin"], [])
        self.assertEqual(read["complete"], 1)

    def test_concatenated_spans_are_the_whole_version(self):
        # Two spans of one observation are consecutive regions of one version, so
        # their concatenation is that version's complete contents.
        pair = self._observation("content-two-spans", hook=6, sequence=2)
        create = self._observation("content-two-spans", hook=1, sequence=1)
        self.assertEqual(_origin(pair["origin"]), _origin(create["origin"]))
        declared = sum(span["length"] for span in pair["content"])
        self.assertEqual(declared, len(create["content"][0]["bytes"]))

    def test_the_captured_bytes_are_the_subject_state(self):
        # create(4) zeroes the resource, update(seed 5, span 4) adds 5,6,7,8, then
        # update(seed 1, span 2) adds 1,2 - the export must carry those whole
        # versions exactly.
        self.assertEqual(
            [s["bytes"] for s in _captured_spans(self.scenarios["content-ample"])],
            [bytes.fromhex("00000000"), bytes.fromhex("05060708"),
             bytes.fromhex("06080708")])

    # --- content provenance -------------------------------------------------------

    def test_an_update_declares_the_version_it_produced(self):
        # The first update runs against version 0 and leaves version 1: the
        # observation context names the state the call addressed, the origin names
        # the state its bytes were copied from.
        first = self._observation("content-ample", hook=2, sequence=2)
        second = self._observation("content-ample", hook=2, sequence=5)
        self.assertEqual(_origin(first["context"]).version, 0)
        self.assertEqual(_origin(first["origin"]).version, 1)
        self.assertEqual(_origin(second["context"]).version, 1)
        self.assertEqual(_origin(second["origin"]).version, 2)

    def test_the_origin_resolves_to_the_lifetime_the_bytes_came_from(self):
        for name in ("content-ample", "content-exact", "content-short"):
            for obs in self.scenarios[name]["observations"]:
                if not obs["content"]:
                    continue
                with self.subTest(scenario=name, seq=obs["sequence"]):
                    origin = _origin(obs["origin"])
                    self.assertTrue(origin.resolved)
                    self.assertEqual(origin.named, _origin(obs["context"]).named)

    def test_content_is_never_declared_without_an_origin(self):
        for scenario in self.scenarios.values():
            for obs in scenario["observations"]:
                with self.subTest(scenario=scenario["name"], seq=obs["sequence"]):
                    self.assertEqual(bool(obs["content"]), obs["norigin"] > 0)
                    if not obs["content"]:
                        self.assertEqual(obs["origin"], [])

    def test_a_stale_origin_is_recorded_complete_and_rejected_offline(self):
        # The declaration is structurally sound - the core has no way to know which
        # resource a subject's origin words mean - so attribution is the offline
        # closure verifier's decision, and it refuses the capture.
        scenario = self.scenarios["content-stale-origin"]
        stale = self._observation("content-stale-origin", hook=2, sequence=4)
        self.assertEqual(stale["complete"], 1)
        self.assertEqual(stale["fault"], "none")
        self.assertTrue(_origin(stale["context"]).resolved)
        self.assertFalse(_origin(stale["origin"]).resolved)
        with self.assertRaises(registry.RegistryError):
            registry.verify_closure(scenario["observations"])

    def test_a_well_formed_capture_passes_closure(self):
        for name in ("content-ample", "content-exact", "content-short",
                     "content-two-spans", "content-two-spans-lost",
                     "content-zero-length", "content-policy", "content-off",
                     "content-concurrent"):
            observations = self.scenarios[name]["observations"]
            with self.subTest(scenario=name):
                summary = registry.verify_closure(observations)
                self.assertIn("slot:0@0", summary["lifetimes"])
                # The unavailable set is exactly the lifetimes some declared reference
                # or origin names and cannot resolve - no more, no less.
                expected = set()
                for obs in observations:
                    for words in (obs["context"], obs["origin"]):
                        if not words:
                            continue
                        ref = _origin(words)
                        if ref.valid and not ref.resolved:
                            expected.add(ref.named)
                self.assertEqual(summary["unavailable_dependencies"], sorted(expected))

    # --- malformed declarations fail closed ---------------------------------------

    def test_every_named_fault_appears_in_the_malformed_script(self):
        faults = [obs["fault"] for obs in self.scenarios["content-malformed"]["observations"]]
        named = set(faults) - {"none"}
        self.assertEqual(named, {"args", "context", "point", "spans", "bytes", "origin"})

    def test_one_malformed_observation_is_one_rejection(self):
        # Twelve malformed declarations, one of them showing two symptoms at once: the
        # count is per observation, never per symptom, so it is exactly twelve.
        counters = _counters(self.scenarios["content-malformed"])
        malformed = [o for o in self.scenarios["content-malformed"]["observations"]
                     if o["fault"] != "none"]
        self.assertEqual(len(malformed), 12)
        self.assertEqual(counters["rejected"], 12)

    def test_a_declared_count_with_no_array_is_malformed_and_never_complete(self):
        # A positive argument or context count with a NULL array is a missing ABI
        # input, not a zero carrier: it must fault, declare no content, and stay
        # incomplete, so no consumer can read invented zeros as observed values.
        args = self._observation("content-malformed", hook=2, sequence=12)
        self.assertEqual(args["fault"], "args")
        self.assertEqual(args["complete"], 0)
        self.assertEqual(args["nargs"], 2)
        self.assertEqual(args["args"], [0, 0])
        self.assertEqual(args["nspans"], 0)
        context = self._observation("content-malformed", hook=2, sequence=13)
        self.assertEqual(context["fault"], "context")
        self.assertEqual(context["complete"], 0)
        self.assertEqual(context["ncontext"], 0)
        self.assertEqual(context["nspans"], 0)

    def test_an_oversized_declaration_is_never_read_from_its_array(self):
        # The declaration is judged before any carrier is read, so neither an over-long
        # nor an undersized array is dereferenced: the diagnostic slots are all zero.
        over_long = self._observation("content-malformed", hook=2, sequence=3)
        self.assertEqual(over_long["fault"], "args")
        self.assertEqual(over_long["nargs"], 8)
        self.assertEqual(set(over_long["args"]), {0})
        undersized = self._observation("content-malformed", hook=2, sequence=14)
        self.assertEqual(undersized["fault"], "args")
        self.assertEqual(undersized["nargs"], 5)
        self.assertEqual(undersized["args"], [0, 0, 0, 0, 0])

    def test_a_zero_count_with_no_array_remains_valid(self):
        # The complement: an *absent* array with a zero declared count is the legitimate
        # empty case, and must not fault - otherwise every content-free hook would be
        # rejected. A read declares no arguments here.
        zero = self._observation("content-zero-length", hook=3, sequence=2)
        self.assertEqual(zero["nargs"], 0)
        self.assertEqual(zero["args"], [])
        self.assertEqual(zero["fault"], "none")
        self.assertEqual(zero["complete"], 1)
        # A zero context count with a NULL context does not itself fault either: this
        # observation is malformed for another, named reason (content no origin can name).
        absent_context = self._observation("content-malformed", hook=2, sequence=9)
        self.assertEqual(absent_context["ncontext"], 0)
        self.assertEqual(absent_context["context"], [])
        self.assertEqual(absent_context["fault"], "origin")

    def test_a_malformed_observation_is_never_complete_and_declares_no_content(self):
        for obs in self.scenarios["content-malformed"]["observations"]:
            if obs["fault"] == "none":
                continue
            with self.subTest(seq=obs["sequence"], fault=obs["fault"]):
                self.assertEqual(obs["complete"], 0)
                self.assertEqual(obs["nspans"], 0)
                self.assertEqual(obs["content"], [])
                self.assertEqual(obs["norigin"], 0)

    def test_the_malformed_script_rejects_admission_but_still_reconciles(self):
        scenario = self.scenarios["content-malformed"]
        counters = _counters(scenario)
        self.assertEqual(scenario["admission"], "REJECTED")
        self.assertEqual(scenario["accounting"], "ok")
        # The two well-formed observations are unaffected by the twelve malformed ones,
        # and the malformed declarations contribute nothing to the content counters.
        self.assertEqual(counters["intercepted"], 14)
        self.assertEqual(counters["captured"], 2)
        self.assertEqual(counters["incomplete"], 12)
        self.assertEqual(counters["content_bytes_declared"], 8)
        self.assertEqual(counters["content_bytes_captured"], 8)

    def test_every_admissible_acquisition_says_so(self):
        for name, scenario in self.scenarios.items():
            with self.subTest(scenario=name):
                expected = "ok" if _counters(scenario)["rejected"] == 0 else "REJECTED"
                self.assertEqual(scenario["admission"], expected)

    def test_a_bounded_loss_is_not_a_fault(self):
        # Losing declared content to the arena is expected bounded behaviour, not
        # misinstrumentation: it leaves its observation incomplete, exactly.
        for name in ("content-short", "content-two-spans", "content-two-spans-lost"):
            scenario = self.scenarios[name]
            with self.subTest(scenario=name):
                self.assertEqual(scenario["admission"], "ok")
                self.assertEqual(_counters(scenario)["rejected"], 0)
                self.assertEqual({o["fault"] for o in scenario["observations"]}, {"none"})
        # The bounded concurrent acquisition loses content too, and says so in its
        # aggregates even though the per-observation race outcome is withheld.
        bounded = self.scenarios["content-concurrent-bounded"]
        self.assertEqual(bounded["admission"], "ok")
        self.assertFalse(bounded["listed"])
        self.assertEqual(_counters(bounded)["rejected"], 0)
        self.assertGreater(_counters(bounded)["content_bytes_lost"], 0)

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
        self.assertEqual(counters["content_bytes_lost"], 24)
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
                if scenario["listed"]:
                    self.assertEqual(scenario["arena"]["intervals"],
                                     len(_captured_spans(scenario)))
                else:
                    # Detail withheld as a race outcome: the count of captured spans is
                    # still bounded by the declared spans the arena could have held.
                    self.assertGreaterEqual(scenario["arena"]["intervals"], 1)

    # --- concurrency --------------------------------------------------------------

    def test_concurrent_content_capture_is_exact(self):
        counters = _counters(self.scenarios["content-concurrent"])
        self.assertEqual(counters["intercepted"], 12)
        self.assertEqual(counters["content_bytes_declared"], 32)
        self.assertEqual(counters["content_bytes_captured"], 32)
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
        self.assertEqual(counters["content_bytes_declared"], 32)
        self.assertEqual(counters["content_bytes_lost"], 24)
        self.assertEqual(counters["content_spans_lost"], 6)
        self.assertEqual(counters["captured"], 6)
        self.assertEqual(counters["incomplete"], 6)
        self.assertEqual(arena["used"], arena["capacity"])
        self.assertEqual(arena["overlaps"], 0)

    # --- the arguments are what distinguishes two calls ---------------------------

    def test_calls_sharing_a_context_are_distinguished_by_their_arguments(self):
        # Two reads of one resource version: identical context, different offsets. A
        # translation table must not merge them, and the values they observed stay
        # recoverable from that version's published contents.
        first = self._observation("content-ample", hook=3, sequence=3)
        second = self._observation("content-ample", hook=3, sequence=4)
        self.assertEqual(first["context"], second["context"])
        self.assertNotEqual(first["args"], second["args"])
        self.assertEqual(first["content"], [])
        version = self._observation("content-ample", hook=2, sequence=2)
        self.assertEqual(_origin(first["context"]).version,
                         _origin(version["origin"]).version)
        bytes_of_version = version["content"][0]["bytes"]
        self.assertEqual(bytes_of_version[first["args"][1]], 0x05)
        self.assertEqual(bytes_of_version[second["args"][1]], 0x08)

    # --- helpers ------------------------------------------------------------------

    def _observation(self, scenario: str, *, hook: int, sequence: int) -> dict:
        for obs in self.scenarios[scenario]["observations"]:
            if obs["hook_id"] == hook and obs["sequence"] == sequence:
                return obs
        raise AssertionError(f"no {scenario} observation hook={hook} seq={sequence}")


class ContentSanitizerTests(unittest.TestCase):
    """The fail-closed path must not read past a declaration's array.

    The `content-malformed` script declares more arguments than the array it names
    actually holds; a recorder that copies before validating would over-read it. Built
    under AddressSanitizer and UBSan, the transscript must stay byte-identical to the
    committed golden and the run must stay clean.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="oracle-p4-content-asan-")
        spec = dict(harness.TARGETS["content"])
        spec["cflags"] = (*spec["cflags"], "-fsanitize=address,undefined",
                          "-fno-omit-frame-pointer")
        cls.exe = harness.build_spec(spec, Path(cls._tmp) / "build")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_the_malformed_path_is_memory_clean(self):
        self.assertEqual(harness.run_transcript(self.exe),
                         harness.golden_transcript("content"))


class ExportParserStrictnessTests(unittest.TestCase):
    """The export is a wire format: a contradicted declaration is refused, not read."""

    @classmethod
    def setUpClass(cls):
        cls.transcript = harness.golden_transcript("content")

    def _mangled(self, line: str, replacement: str) -> str:
        lines = self.transcript.splitlines()
        for index, candidate in enumerate(lines):
            if candidate.startswith(line):
                lines[index] = replacement
                return "\n".join(lines) + "\n"
        raise AssertionError(f"no transcript line starts with {line!r}")

    def _duplicated(self, line: str) -> str:
        lines = self.transcript.splitlines()
        for index, candidate in enumerate(lines):
            if candidate.startswith(line):
                lines.insert(index + 1, candidate)
                return "\n".join(lines) + "\n"
        raise AssertionError(f"no transcript line starts with {line!r}")

    def _replaced_once(self, old: str, new: str) -> str:
        self.assertIn(old, self.transcript)
        return self.transcript.replace(old, new, 1)

    def test_the_golden_export_parses(self):
        self.assertEqual(harness.parse_export_transcript(self.transcript)["version"], "v1")

    def test_a_content_item_deleted_while_nspans_still_claims_it_is_refused(self):
        mangled = self._mangled(
            "OBS thread=0 seq=2 phase=0 hook=6",
            "OBS thread=0 seq=2 phase=0 hook=6 point=1 result=0 complete=0 fault=none"
            " nargs=1 args=0 ncontext=6 context=1,0,0,1,0,0 nspans=2 content=c:2:0000"
            " norigin=6 origin=1,0,0,1,0,0")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(mangled)

    def test_an_argument_list_truncated_below_nargs_is_refused(self):
        mangled = self._mangled(
            "OBS thread=0 seq=2 phase=0 hook=2",
            "OBS thread=0 seq=2 phase=0 hook=2 point=0 result=0 complete=1 fault=none"
            " nargs=3 args=0 ncontext=6 context=1,0,0,1,0,0 nspans=1 content=c:4:05060708"
            " norigin=6 origin=1,0,0,1,0,1")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(mangled)

    def test_content_with_no_origin_is_refused(self):
        mangled = self._mangled(
            "OBS thread=0 seq=1 phase=0 hook=1",
            "OBS thread=0 seq=1 phase=0 hook=1 point=0 result=0 complete=1 fault=none"
            " nargs=1 args=4 ncontext=6 context=1,0,0,1,0,0 nspans=1 content=c:4:00000000"
            " norigin=0 origin=")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(mangled)

    def test_a_faulted_observation_that_claims_completeness_is_refused(self):
        mangled = self._mangled(
            "OBS thread=0 seq=3 phase=0 hook=2",
            "OBS thread=0 seq=3 phase=0 hook=2 point=0 result=0 complete=1 fault=args"
            " nargs=8 args=0,1,2,3,4,5,6,7 ncontext=6 context=1,0,0,1,0,1 nspans=0 content="
            " norigin=0 origin=")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(mangled)

    def test_a_repeated_field_is_refused(self):
        mangled = self._mangled(
            "OBS thread=0 seq=1 phase=0 hook=1",
            "OBS thread=0 seq=1 phase=0 hook=1 point=0 result=0 complete=1 complete=0"
            " fault=none nargs=1 args=4 ncontext=6 context=1,0,0,1,0,0 nspans=1"
            " content=c:4:00000000 norigin=6 origin=1,0,0,1,0,0")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(mangled)

    def test_a_non_canonical_number_is_refused(self):
        mangled = self._mangled(
            "OBS thread=0 seq=1 phase=0 hook=1",
            "OBS thread=0 seq=01 phase=0 hook=1 point=0 result=0 complete=1 fault=none"
            " nargs=1 args=4 ncontext=6 context=1,0,0,1,0,0 nspans=1 content=c:4:00000000"
            " norigin=6 origin=1,0,0,1,0,0")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(mangled)

    def test_a_scenario_declaring_the_wrong_observation_count_is_refused(self):
        mangled = self._mangled("OBSERVATIONS count=8", "OBSERVATIONS count=7 listed=1")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(mangled)

    def test_a_scenario_claiming_not_to_list_observations_it_printed_is_refused(self):
        mangled = self._mangled("OBSERVATIONS count=8", "OBSERVATIONS count=8 listed=0")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(mangled)

    def test_a_record_missing_a_field_its_shape_promises_is_refused(self):
        # Dropping `norigin` entirely (not emptying it) must not read as "no origin".
        mangled = self._mangled(
            "OBS thread=0 seq=1 phase=0 hook=1",
            "OBS thread=0 seq=1 phase=0 hook=1 point=0 result=0 complete=1 fault=none"
            " nargs=1 args=4 ncontext=6 context=1,0,0,1,0,0 nspans=1 content=c:4:00000000")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(mangled)

    def test_an_admission_verdict_contradicting_the_rejections_is_refused(self):
        lines = self.transcript.splitlines()
        # The malformed scenario is REJECTED; claiming it admissible is a contradiction.
        index = next(i for i, line in enumerate(lines)
                     if line == "ADMISSION REJECTED")
        lines[index] = "ADMISSION ok"
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript("\n".join(lines) + "\n")

    def test_a_record_after_the_terminal_end_is_refused(self):
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(self.transcript + "SCENARIO extra\n")

    def test_a_missing_terminal_end_is_refused(self):
        lines = [line for line in self.transcript.splitlines()
                 if not line.startswith("END ")]
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript("\n".join(lines) + "\n")

    def test_a_missing_version_line_is_refused(self):
        lines = self.transcript.splitlines()
        self.assertEqual(lines[0], "RECORDER-EXPORT v1")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript("\n".join(lines[1:]) + "\n")

    def test_a_duplicated_control_record_is_refused(self):
        # A second COUNTERS/ACCOUNTING/ADMISSION/ARENA/OBSERVATIONS would silently
        # overwrite the first, which is how a contradicted scenario control passes.
        for line in ("COUNTERS intercepted=8", "ACCOUNTING ok", "ADMISSION ok",
                     "ARENA capacity=0", "OBSERVATIONS count=8"):
            with self.subTest(record=line):
                with self.assertRaises(harness.HarnessError):
                    harness.parse_export_transcript(self._duplicated(line))

    def test_admission_ok_with_a_listed_fault_is_refused(self):
        # The review's case: rewrite the malformed scenario to declare rejected=0 and
        # ADMISSION ok while retaining twelve faulted observations.
        text = self._replaced_once("rejected=12", "rejected=0")
        text = text.replace("ADMISSION REJECTED", "ADMISSION ok", 1)
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(text)

    def test_more_listed_faults_than_rejections_is_refused(self):
        # rejected=5 keeps ADMISSION REJECTED and every identity intact, but cannot
        # account for the twelve faulted observations the scenario printed.
        text = self._replaced_once("rejected=12", "rejected=5")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(text)

    def test_a_listed_observation_split_disagreeing_with_the_counters_is_refused(self):
        # Flip one complete observation to incomplete: the count still matches, but the
        # complete/incomplete split no longer agrees with the native counters.
        text = self._replaced_once(
            "OBS thread=0 seq=1 phase=0 hook=1 point=0 result=0 complete=1 fault=none"
            " nargs=1 args=4 ncontext=6 context=1,0,0,1,0,0 nspans=1 content=c:4:00000000"
            " norigin=6 origin=1,0,0,1,0,0",
            "OBS thread=0 seq=1 phase=0 hook=1 point=0 result=0 complete=0 fault=none"
            " nargs=1 args=4 ncontext=6 context=1,0,0,1,0,0 nspans=1 content=c:4:00000000"
            " norigin=6 origin=1,0,0,1,0,0")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(text)

    def test_an_accounting_verdict_contradicting_the_counters_is_refused(self):
        # The identities are recomputed: consistent counters beside `ACCOUNTING
        # VIOLATED` is a contradiction like its converse.
        text = self._replaced_once("ACCOUNTING ok", "ACCOUNTING VIOLATED")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(text)

    def test_counters_that_violate_their_own_identities_are_refused(self):
        # The native identities are re-checked here, not merely trusted from the
        # ACCOUNTING verdict: intercepted == selected + unselected and
        # selected == captured + incomplete + lost must both hold.
        text = self._replaced_once(
            "COUNTERS intercepted=8 selected=8 unselected=0 captured=6 incomplete=2 lost=0",
            "COUNTERS intercepted=8 selected=7 unselected=0 captured=6 incomplete=2 lost=0")
        with self.assertRaises(harness.HarnessError):
            harness.parse_export_transcript(text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
