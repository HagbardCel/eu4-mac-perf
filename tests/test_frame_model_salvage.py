import unittest

import eu4_frame_model as model
from frame_model_salvage import (
    _frame_retention,
    _window_frame_keys,
    eligible_intrusive_salvage_trace,
)


class FrameModelSalvageTests(unittest.TestCase):
    def test_salvage_includes_s_with_flag_2048(self):
        frame = {
            "measurement_epoch": 1,
            "update_id": 2,
            "phase": model.PHASE_NUMBER["TAIL"],
            "flags": 2048,
            "generation": 24,
            "start_ns": 0,
            "end_ns": 1_000_000_000,
        }
        window = {
            "name": "TAIL",
            "measurement_epoch": 1,
            "start_ns": 0,
            "end_ns": 2_000_000_000,
            "window_generation": 0,
            "clock_offset_ns": 0,
            "alignment_uncertainty_ns": 0,
        }
        s_row = ["S", "1", "4096", "8", "0"] + ["0"] * 8 + ["1", "2", str(model.PHASE_NUMBER["TAIL"]), "0", "0", "0", "0"]
        d_row = ["D", "1", "8192", "1", "0", "0", "0", "100", "0", "0", "0", "0", "1"] + ["1", "2", "103", "0", "0", "0", "0"]
        trace = eligible_intrusive_salvage_trace([s_row, d_row], [frame], window)
        kinds = [row[0] for row in trace]
        self.assertIn("S", kinds)
        self.assertNotIn("D", kinds)

    def test_window_keys_prefer_observed_over_generation_match(self):
        tail_frames = [
            {
                "measurement_epoch": 6,
                "update_id": 11531,
                "generation": 24,
                "state_calls": 100,
                "uniform_calls": 10,
            },
            {
                "measurement_epoch": 6,
                "update_id": 99999,
                "generation": 24,
                "state_calls": 100,
                "uniform_calls": 10,
            },
        ]
        observed = [{"measurement_epoch": 6, "update_id": 11531}]
        keys = _window_frame_keys(tail_frames, 24, observed)
        self.assertEqual(keys, {(6, 11531)})

    def test_frame_retention_matches_detail_rows_for_observed_key(self):
        frame = {
            "measurement_epoch": 6,
            "update_id": 11531,
            "state_calls": 4,
            "uniform_calls": 2,
        }
        rows = [
            ["S", "1", "1", "1", "0"] + ["0"] * 8 + ["6", "11531", "103", "0", "0", "0", "0"],
            ["S", "1", "2", "1", "0"] + ["0"] * 8 + ["6", "11531", "103", "0", "0", "0", "0"],
            ["U", "1", "3", "0"] + ["0"] * 9 + ["6", "11531", "103"],
        ]
        retention = _frame_retention(frame, rows)
        self.assertEqual(retention["surviving_s"], 2)
        self.assertEqual(retention["surviving_u"], 1)
        self.assertAlmostEqual(retention["retention_s"], 0.5)
