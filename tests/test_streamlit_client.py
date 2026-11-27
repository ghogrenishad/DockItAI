"""Streamlit HTTP client tests; no domain policy belongs in this layer."""

from __future__ import annotations

import httpx
import pytest

from dockit.ui.streamlit_app import DockItApiClient, DockItApiError


def test_ui_client_uses_versioned_api_and_auth_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    client = DockItApiClient("https://api.example", "access-token", http_client=http_client)
    try:
        assert client.get_options("SHP1006") == {"status": "ok"}
        assert seen[0].url.path == "/api/v1/shipments/SHP1006/options"
        assert seen[0].headers["authorization"] == "Bearer access-token"
    finally:
        client.close()


def test_ui_client_surfaces_standard_api_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(409, json={"code": "conflict", "message": "slot unavailable"})

    client = DockItApiClient(
        "https://api.example",
        "access-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(DockItApiError, match="slot unavailable"):
            client.create_hold("SHP1006", "SLOT-JAI-062", "2026-08-04T04:00:00Z")
    finally:
        client.close()
