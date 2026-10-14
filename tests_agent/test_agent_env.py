import os
import sys
import unittest


class AgentEnvironmentTests(unittest.TestCase):
    def test_python_311_plus(self) -> None:
        self.assertGreaterEqual(sys.version_info[:2], (3, 11))

    def test_graphics_agent_import(self) -> None:
        from reverse_engineering.graphics_agent import __version__

        self.assertTrue(__version__)

    def test_pydantic_models(self) -> None:
        from reverse_engineering.graphics_agent.models import QueryScopeStatus

        self.assertEqual(QueryScopeStatus.UNRESOLVED.value, "unresolved")

    def test_mcp_import(self) -> None:
        import mcp  # noqa: F401

    def test_allow_model_requests_false_in_ci(self) -> None:
        if os.environ.get("CI") == "true":
            self.assertEqual(os.environ.get("ALLOW_MODEL_REQUESTS"), "False")


if __name__ == "__main__":
    unittest.main()
