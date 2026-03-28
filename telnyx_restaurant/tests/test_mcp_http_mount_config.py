"""HANOK_MCP_HTTP_MOUNT* helpers (read env at call time — no module reload)."""

from __future__ import annotations

from telnyx_restaurant.config import hanok_mcp_http_mount_enabled, hanok_mcp_http_mount_path


def test_hanok_mcp_http_mount_path_default(monkeypatch) -> None:
    monkeypatch.delenv("HANOK_MCP_HTTP_MOUNT_PATH", raising=False)
    assert hanok_mcp_http_mount_path() == "/mcp"


def test_hanok_mcp_http_mount_path_custom(monkeypatch) -> None:
    monkeypatch.setenv("HANOK_MCP_HTTP_MOUNT_PATH", "custom")
    assert hanok_mcp_http_mount_path() == "/custom"


def test_hanok_mcp_http_mount_enabled(monkeypatch) -> None:
    monkeypatch.delenv("HANOK_MCP_HTTP_MOUNT", raising=False)
    assert hanok_mcp_http_mount_enabled() is False
    monkeypatch.setenv("HANOK_MCP_HTTP_MOUNT", "1")
    assert hanok_mcp_http_mount_enabled() is True
