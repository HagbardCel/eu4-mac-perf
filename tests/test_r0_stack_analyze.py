import json
import unittest
from pathlib import Path

from analysis.tools.r0_stack_analyze import (
    StackNode,
    analyze_sample_text,
    assert_tree_exclusive_nonnegative,
    build_tree,
    enumerate_weighted_call_graph_lines,
    extract_main_thread_section,
    parse_weighted_frame_line,
    _parse_frame_lines,
)

ROOT = Path(__file__).resolve().parents[1]
AUTHENTIC_SAMPLE = ROOT / "results/r0-native-qualification/authentic-sample.txt"
PROVENANCE_PATH = ROOT / "results/r0-native-qualification/provenance.json"


def _authentic_main_thread_id() -> int:
    if not PROVENANCE_PATH.is_file():
        return 6322634
    return int(json.loads(PROVENANCE_PATH.read_text())["main_thread_id"])


def _child_matching(node: StackNode, substring: str) -> StackNode:
    for symbol, child in node.children.items():
        if substring in symbol:
            return child
    raise AssertionError(f"no child matching {substring!r} in {list(node.children)}")


def _parse_authentic_tree() -> StackNode:
    text = AUTHENTIC_SAMPLE.read_text(errors="replace")
    section = extract_main_thread_section(text, _authentic_main_thread_id())
    section = "Call graph:\n" + section
    return build_tree(_parse_frame_lines(section))


class R0StackAnalyzeTests(unittest.TestCase):
    def test_two_dimensional_classification(self) -> None:
        text = """
Call graph:
    100 CInGameIdler::Render
      100 CPdxMeshObject::RenderBuckets
        60 GfxDrawIndexed
          60 glDrawElements
            60 GLDContextRec::setRenderState
        """
        report = analyze_sample_text(text)
        self.assertIn("mesh_render_buckets", report["functional_caller"])
        self.assertIn("gl_driver_set_render_state", report["execution_location"])
        self.assertTrue(report["cross_tab_counts"])
        self.assertEqual(report["attributed_sample_weight"], 100)

    def test_inclusive_tree_conservation(self) -> None:
        text = """
Call graph:
    100 root
      60 child_a
        30 child_b
        """
        report = analyze_sample_text(text)
        self.assertEqual(report["attributed_sample_weight"], 100)

    def test_real_sample_snippet(self) -> None:
        text = """
Call graph:
    1234 CInGameIdler::Render(void*)
      800 CPdxMeshObject::RenderBuckets
        500 GfxDrawIndexed
          500 glDrawElements
        300 CPdxMapBorderLayer::DrawBorders
        """
        report = analyze_sample_text(text)
        self.assertIn("border_draw", report["functional_caller"])
        self.assertIn("gfx_draw_indexed", report["functional_caller"])
        self.assertEqual(report["attributed_sample_weight"], 1234)

    def test_fixture_snippet_with_main_thread_id(self) -> None:
        text = Path(__file__).parent.joinpath("fixtures/r0_epoch_b_sample_snippet.txt").read_text()
        report = analyze_sample_text(text, main_thread_id=0x2A)
        self.assertEqual(report["attributed_sample_weight"], 100)

    def test_main_thread_section_rejects_prefix_thread_id(self) -> None:
        text = """
Call graph:
    10 Thread_1234   main
      10 foo
    20 Thread_12345   other
      20 bar
        """
        report = analyze_sample_text(text, main_thread_id=1234)
        self.assertEqual(report["attributed_sample_weight"], 10)
        self.assertNotIn("bar", str(report))

    def test_branched_siblings(self) -> None:
        text = """
Call graph:
    100 root
      60 branch_a
      40 branch_b
        """
        report = analyze_sample_text(text)
        self.assertEqual(report["attributed_sample_weight"], 100)
        self.assertGreaterEqual(len(report["cross_tab_counts"]), 1)

    def test_decorated_sample_cross_attribution(self) -> None:
        text = """
Call graph:
    100 CInGameIdler::Render(void*)
      + 100 CPdxMeshObject::RenderBuckets
        + 100 glDrawElements
          + 100 GLDContextRec::setRenderState
        """
        report = analyze_sample_text(text)
        self.assertEqual(report["attributed_sample_weight"], 100)
        self.assertEqual(report["root_inclusive"], 100)
        self.assertEqual(report["weighted_row_count"], 4)
        self.assertIn("mesh_render_buckets|gl_driver_set_render_state", report["cross_tab_counts"])
        self.assertEqual(report["cross_tab_counts"]["mesh_render_buckets|gl_driver_set_render_state"], 100)

    def test_unparsed_call_graph_line_fails_closed(self) -> None:
        text = """
Call graph:
    100 root
      not a weighted row
        """
        with self.assertRaises(ValueError):
            analyze_sample_text(text)

    @unittest.skipUnless(AUTHENTIC_SAMPLE.is_file(), "missing committed authentic-sample.txt")
    def test_authentic_sample_complete_parse_and_conservation(self) -> None:
        text = AUTHENTIC_SAMPLE.read_text(errors="replace")
        report = analyze_sample_text(text, main_thread_id=_authentic_main_thread_id())
        self.assertEqual(report["weighted_row_count"], 15)
        self.assertEqual(report["root_inclusive"], 722)
        self.assertEqual(report["attributed_sample_weight"], 722)

    @unittest.skipUnless(AUTHENTIC_SAMPLE.is_file(), "missing committed authentic-sample.txt")
    def test_authentic_sample_ancestry_and_leaf_weights(self) -> None:
        root = _parse_authentic_tree()
        assert_tree_exclusive_nonnegative(root)
        thread = _child_matching(root, "Thread_")
        self.assertEqual(thread.inclusive, 722)
        start = _child_matching(thread, "start")
        self.assertEqual(start.inclusive, 722)
        main = _child_matching(start, "main")
        self.assertEqual(main.inclusive, 714)
        clock = _child_matching(main, "clock_gettime")
        self.assertEqual(clock.inclusive, 697)
        boot = _child_matching(clock, "_mach_boottime_usec")
        self.assertEqual(boot.inclusive, 675)
        gtod = _child_matching(boot, "gettimeofday")
        self.assertEqual(gtod.inclusive, 657)
        commpage = _child_matching(gtod, "__commpage_gettimeofday_internal")
        self.assertEqual(commpage.inclusive, 637)
        mach = _child_matching(commpage, "mach_absolute_time")
        self.assertEqual(mach.inclusive, 637)

    @unittest.skipUnless(AUTHENTIC_SAMPLE.is_file(), "missing committed authentic-sample.txt")
    def test_authentic_sample_enumerates_fifteen_weighted_rows(self) -> None:
        text = AUTHENTIC_SAMPLE.read_text(errors="replace")
        section = extract_main_thread_section(text, _authentic_main_thread_id())
        rows = enumerate_weighted_call_graph_lines("Call graph:\n" + section)
        self.assertEqual(len(rows), 15)
        for raw in rows:
            parse_weighted_frame_line(raw)


if __name__ == "__main__":
    unittest.main()
