"""Tests for client-side transport error handling."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from opsramp_mcp.client import OpsRampAPIError, OpsRampClient
from opsramp_mcp.config import PlatformConfig


class _DummyAsyncClient:
    async def request(self, *args, **kwargs):
        raise httpx.ReadTimeout("")


def _make_platform() -> PlatformConfig:
    return PlatformConfig(
        name="test",
        api_base_url="https://example.invalid",
        client_id="id",
        client_secret="secret",
        default_tenant="",
        tenants={},
    )


def test_request_wraps_httpx_error_with_details():
    client = OpsRampClient(_make_platform())
    client._token.access_token = "token"
    client._token.expires_at = time.time() + 3600
    client._http_client = _DummyAsyncClient()  # type: ignore[assignment]

    async def _run() -> None:
        with pytest.raises(OpsRampAPIError) as exc_info:
            await client._request("GET", "/tracing-query/api/v1/tenants/t1/operation-insights")

        exc = exc_info.value
        assert "transport error" in str(exc)
        assert isinstance(exc.details, dict)
        assert exc.details.get("exception_type") == "ReadTimeout"
        assert isinstance(exc.details.get("error"), str)
        assert exc.details.get("error", "").strip()

    asyncio.run(_run())
