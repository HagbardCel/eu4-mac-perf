import unittest

import eu4_frame_model as model
from frame_model_salvage import eligible_intrusive_salvage_trace


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
