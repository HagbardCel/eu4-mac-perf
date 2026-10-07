import unittest

from analysis.tools.r0_stack_analyze import analyze_sample_text


class R0StackAnalyzeTests(unittest.TestCase):
    def test_two_dimensional_classification(self) -> None:
        text = """
        CInGameIdler::Render
          CPdxMeshObject::RenderBuckets
            GfxDrawIndexed
              glDrawElements
                GLDContextRec::setRenderState
        """
        report = analyze_sample_text(text)
        self.assertIn("mesh_render_buckets", report["functional_caller"])
        self.assertIn("gl_driver_set_render_state", report["execution_location"])
        self.assertTrue(report["cross_tab_counts"])


if __name__ == "__main__":
    unittest.main()
