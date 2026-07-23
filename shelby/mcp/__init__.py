"""Remote MCP connector package — lets Shelby use hosted MCP servers."""

from .connector import MCP_BETA, describe_servers, mcp_servers

__all__ = ["MCP_BETA", "describe_servers", "mcp_servers"]
