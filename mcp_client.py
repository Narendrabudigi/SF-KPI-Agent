from typing import Any, Dict

class MCPClient:
    """
    In-memory MCP Client that routes tool execution calls directly to
    the registered Python functions in mcp_server.py.
    
    This preserves the decoupled architecture without requiring complex
    network handshakes (SSE/HTTP POST) within the same process.
    """
    def __init__(self, base_url: str = None):
        # Kept for compatibility, but we execute in-memory
        self.base_url = base_url

    def call_tool(self, tool_name: str, arguments: Dict[str, Any] = None) -> Any:
        """
        Looks up the registered tool function in mcp_server and executes it.
        """
        import mcp_server
        arguments = arguments or {}

        if hasattr(mcp_server, tool_name):
            func = getattr(mcp_server, tool_name)
            return func(**arguments)
        else:
            raise RuntimeError(f"Tool '{tool_name}' is not registered on the MCP Server.")

# Global singleton client instance
mcp_client = MCPClient()
