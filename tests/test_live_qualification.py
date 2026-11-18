"""Explicit live-qualification test module (M7-P2 profile split).

This module is never part of the offline default suite's *executed* tests: the
decorated tests skip with a reason unless ``EU4_TEST_PROFILE=live``.  The live
profile must be selected explicitly and must name this module.

It deliberately does not launch EU4.  It validates the live evidence contract
for an operator-supplied, already-captured case so a live gate cannot pass on
the wrong profile or a missing case.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from automation import test_profiles

CASE_ENV = "EU4_LIVE_CASE"


class LiveQualificationTests(unittest.TestCase):
    @test_profiles.requires_live(
        "a real EU4 capture and its sealed case are required")
    def test_live_profile_was_explicitly_selected(self):
        self.assertEqual(test_profiles.active_profile(), test_profiles.LIVE)
        self.assertTrue(test_profiles.live_allowed())

    @test_profiles.requires_live(
        "a real EU4 capture and its sealed case are required")
    def test_live_case_is_named_and_present(self):
        raw = os.environ.get(CASE_ENV)
        self.assertTrue(raw, f"{CASE_ENV} must name the already-captured case")
        case = Path(raw).expanduser()
        self.assertTrue((case / "case.json").is_file(),
                        f"{case}/case.json is missing")


if __name__ == "__main__":
    unittest.main()
