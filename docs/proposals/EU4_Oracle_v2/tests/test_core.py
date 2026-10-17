import unittest
from oracle_proto.core import ObservationLedger, compare_fixture


class TestObservationLedger(unittest.TestCase):
    def test_reconcile_duplicate_and_counted_only(self):
        x = ObservationLedger()
        for _ in range(5): x.encountered("f")
        self.assertTrue(x.observed("f", {"in": "a", "out": "b"}, complete=True).is_new)
        self.assertFalse(x.observed("f", {"out": "b", "in": "a"}, complete=True).is_new)
        x.observed("f", {"in": "z"}, complete=False)
        self.assertEqual(x.summary("f"), {"encountered": 5, "deep_captured": 3, "unique_complete": 1,
                                              "verified_repeats": 1, "incomplete_deep": 1,
                                              "counted_only": 2, "lost_events": 0})

    def test_same_input_different_outcome_not_duplicate(self):
        x = ObservationLedger()
        for n in (1, 2):
            x.encountered("f")
            self.assertTrue(x.observed("f", {"args": [7], "post": {"state": n}}, complete=True).is_new)
        self.assertEqual(x.summary("f")["unique_complete"], 2)

    def test_missing_hooks_fail_closed(self):
        x = ObservationLedger()
        x.encountered("f", loss=True)
        self.assertEqual(x.summary("f")["lost_events"], 1)

    def test_no_false_hash_dedup(self):
        x = ObservationLedger()
        # distinct complete payloads must not collapse
        for i in range(3):
            x.encountered("f")
            x.observed("f", {"value": i}, complete=True)
        self.assertEqual(x.summary("f")["unique_complete"], 3)

class TestComparator(unittest.TestCase):
    def setUp(self):
        self.e = {"capture": {"complete": True, "lost_events": 0, "unknown_dependencies": []},
                  "return_hex": "", "exit": "normal", "post_memory": {"ctx:g0": "000100"},
                  "effects": [{"call": "glBindBuffer", "args": [1, 2]}]}

    def test_pass(self): self.assertEqual(compare_fixture(self.e, self.e)["status"], "PASS")

    def test_memory_first_difference(self):
        a = dict(self.e, post_memory={"ctx:g0": "000000"})
        r = compare_fixture(self.e, a)
        self.assertEqual(r["first_divergence"]["byte_offset"], 1)

    def test_effect_order(self):
        a = dict(self.e, effects=[])
        r = compare_fixture(self.e, a)
        self.assertEqual(r["first_divergence"]["domain"], "effects")

    def test_return(self):
        a = dict(self.e, return_hex="01")
        self.assertEqual(compare_fixture(self.e, a)["first_divergence"]["domain"], "return")

    def test_incomplete_is_not_pass(self):
        e = dict(self.e, capture={"complete": True, "lost_events": 0, "unknown_dependencies": ["global-x"]})
        self.assertEqual(compare_fixture(e, self.e)["status"], "INCOMPLETE_ORACLE")

    def test_exact_fp_bits(self):
        e = dict(self.e, return_hex="0000000000000000")
        a = dict(self.e, return_hex="0000000000000080")
        self.assertEqual(compare_fixture(e, a)["status"], "MISMATCH")

if __name__ == "__main__": unittest.main()
