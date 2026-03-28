"""MCP streamable HTTP DNS rebinding allowed Hosts/Origins from env."""

from __future__ import annotations


def test_transport_security_off_when_no_public_url(monkeypatch) -> None:
    monkeypatch.delenv("HANOK_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("HANOK_MCP_API_BASE_URL", raising=False)
    monkeypatch.delenv("HANOK_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("HANOK_MCP_DISABLE_DNS_REBINDING", raising=False)
    from telnyx_restaurant.config import hanok_mcp_streamable_transport_security

    s = hanok_mcp_streamable_transport_security()
    assert s.enable_dns_rebinding_protection is False


def test_transport_security_allows_public_host(monkeypatch) -> None:
    monkeypatch.setenv("HANOK_PUBLIC_BASE_URL", "https://telnyx.convonetai.com")
    monkeypatch.delenv("HANOK_MCP_DISABLE_DNS_REBINDING", raising=False)
    from telnyx_restaurant.config import hanok_mcp_streamable_transport_security

    s = hanok_mcp_streamable_transport_security()
    assert s.enable_dns_rebinding_protection is True
    assert "telnyx.convonetai.com" in s.allowed_hosts
    assert "telnyx.convonetai.com:*" in s.allowed_hosts
    assert "https://telnyx.convonetai.com" in s.allowed_origins
