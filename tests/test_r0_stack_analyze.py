import unittest

from analysis.tools.r0_stack_analyze import analyze_sample_text


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


if __name__ == "__main__":
    unittest.main()
