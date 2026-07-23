"""Remote MCP connector package — lets Shelby use hosted MCP servers."""

from .connector import (
    MCP_BETA,
    add_server,
    describe_servers,
    list_servers,
    mcp_servers,
    remove_server,
)

__all__ = [
    "MCP_BETA",
    "add_server",
    "describe_servers",
    "list_servers",
    "mcp_servers",
    "remove_server",
]
