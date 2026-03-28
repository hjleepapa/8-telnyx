"""FastAPI lifespan must run MCP session_manager when HTTP mount is enabled."""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def test_health_with_mcp_mount_runs_lifespan(monkeypatch) -> None:
    monkeypatch.setenv("HANOK_MCP_HTTP_MOUNT", "1")
    import telnyx_restaurant.app as app_module

    importlib.reload(app_module)
    with TestClient(app_module.app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
