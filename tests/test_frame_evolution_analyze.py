import unittest
from pathlib import Path

from analysis.tools.frame_evolution_analyze import (
    FrameRecord,
    accepted_census_frames,
    build_report,
    classify_scenario,
    consecutive_equal_runs,
    evaluate_positive_control,
)


class FrameEvolutionAnalyzeTests(unittest.TestCase):
    def test_consecutive_equal_runs(self) -> None:
        frames = [
            FrameRecord(1, 1, 0, True, True, 64, 64, 0, True, 1),
            FrameRecord(2, 1, 0, True, True, 64, 64, 1, True, 1),
            FrameRecord(3, 1, 0, True, True, 64, 64, 1, True, 1),
            FrameRecord(4, 1, 0, True, True, 64, 64, 0, True, 2),
        ]
        runs = consecutive_equal_runs(frames)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["length"], 2)

    def test_classify_static(self) -> None:
        frames = [
            FrameRecord(i, 1, 0, True, True, 64, 64, 1 if i > 1 else 0, True, 1)
            for i in range(1, 300)
        ]
        summary = classify_scenario(frames, min_frames=120, min_seconds=2.0, fps_hint=60.0)
        self.assertEqual(summary["classification"], "static")

    def test_positive_control(self) -> None:
        before = [FrameRecord(1, 1, 1, True, True, 64, 64, 0, True, 10)]
        after = [FrameRecord(2, 1, 1, True, True, 64, 64, 0, True, 20)]
        result = evaluate_positive_control(before, after)
        self.assertEqual(result["status"], "passed")

    def test_build_report_writes_candidates(self) -> None:
        log = Path(self._testMethodName) / "probe.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for i in range(1, 301):
            eq = 1 if i > 1 else 0
            lines.append(f"F,{i},1,0,1,1,0,64,64,{eq},1,42,1\n")
        log.write_text("".join(lines))
        frames = accepted_census_frames(
            [
                FrameRecord(i, 1, 0, True, True, 64, 64, 1 if i > 1 else 0, True, 42)
                for i in range(1, 301)
            ]
        )
        report = build_report(
            log_path=log,
            scenario_segments={"0": frames},
            positive_control=None,
        )
        self.assertTrue(report["candidate_for_lossless_skip"])


if __name__ == "__main__":
    unittest.main()
