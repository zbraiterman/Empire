"""Smoke tests for the performance-test infrastructure.

The ``slow``-marked tests require a running MySQL container and Empire
server (provided by the session-scoped fixtures in ``conftest.py``).
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
from starlette.status import HTTP_200_OK

pytestmark = [pytest.mark.slow, pytest.mark.mysql]


@pytest.mark.slow
def test_mysql_container_is_running(mysql_port):
    """Verify that the MySQL container is accepting TCP connections."""
    with socket.create_connection(("127.0.0.1", mysql_port), timeout=5):
        pass  # connection succeeded


@pytest.mark.slow
def test_empire_server_is_running(empire_base_url):
    """Verify that the Empire server responds to ``POST /token``."""
    resp = httpx.post(
        f"{empire_base_url}/token",
        data={
            "grant_type": "password",
            "username": "empireadmin",
            "password": "password123",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    assert resp.status_code == HTTP_200_OK


@pytest.mark.slow
def test_authenticated_api_call(empire_base_url, auth_header):
    """Verify that an authenticated GET /api/v2/listeners succeeds."""

    async def _fetch():
        async with httpx.AsyncClient(base_url=empire_base_url) as client:
            return await client.get(
                "/api/v2/listeners", headers=auth_header, timeout=10
            )

    resp = asyncio.run(_fetch())
    assert resp.status_code == HTTP_200_OK
