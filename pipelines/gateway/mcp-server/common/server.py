"""
Factories and helpers for FastMCP servers.
"""

from mcp.server.fastmcp import FastMCP


def build_fastmcp_server(name: str, port: int, *, host: str = "0.0.0.0", json_response: bool = True) -> FastMCP:
    """
    Build a FastMCP server with consistent defaults.
    Args:
        name: Human-readable server name.
        port: TCP port to listen on.
        host: Interface to bind to. Defaults to localhost.
        json_response: Whether to emit JSON responses.
    Returns:
        FastMCP: Configured server instance.
    """
    return FastMCP(name, json_response=json_response, host=host, port=port)
