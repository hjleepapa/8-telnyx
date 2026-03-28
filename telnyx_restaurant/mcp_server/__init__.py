"""Hanok Table MCP server — exposes reservation REST as MCP tools (stdio or HTTP)."""

from telnyx_restaurant.mcp_server.server import mcp

__all__ = ["mcp"]
