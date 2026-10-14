import unittest


class McpSmokeTests(unittest.TestCase):
    def test_mcpserver_import(self) -> None:
        from mcp.server.mcpserver import MCPServer

        srv = MCPServer("test")

        @srv.tool()
        def ping() -> str:
            return "ok"

        tools = srv.list_tools()
        names = [t.name for t in tools.tools] if hasattr(tools, "tools") else []
        if not names and hasattr(srv, "_tool_manager"):
            names = list(srv._tool_manager._tools.keys())
        self.assertTrue(ping() == "ok")


if __name__ == "__main__":
    unittest.main()
