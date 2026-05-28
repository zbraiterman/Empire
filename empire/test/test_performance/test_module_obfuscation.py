"""Tests for module task obfuscation performance.

When obfuscation is enabled, module task execution spawns a synchronous
PowerShell subprocess to run Invoke-Obfuscation.  The API handler must
be a regular ``def`` (not ``async def``) so Starlette runs it in a
thread pool without blocking the event loop.  This test verifies that
the event loop remains responsive during obfuscation.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

import httpx
import pytest
from starlette.status import HTTP_200_OK

from empire.test.test_performance.conftest import (
    MAX_MANAGEMENT_LATENCY_SECONDS,
    STAGER_WAIT_BEFORE_PROBE_SECONDS,
)
from empire.test.test_performance.helpers import (
    summarize_results,
    timed_request,
)

log = logging.getLogger(__name__)

pytestmark = [pytest.mark.slow, pytest.mark.mysql]

requires_powershell = pytest.mark.skipif(
    not shutil.which("powershell") and not shutil.which("pwsh"),
    reason="PowerShell (powershell or pwsh) is not available on this system",
)


@requires_powershell
def test_module_obfuscation_does_not_block_management_api(
    empire_base_url: str,
    auth_header: dict[str, str],
    test_agent_id: str,
    obfuscation_enabled: bool,
    powershell_module_id: str,
):
    """While a module task with obfuscation is running, lightweight management
    API calls (``GET /api/v2/listeners``) should still respond within
    ``MAX_MANAGEMENT_LATENCY_SECONDS``.

    The module task itself MAY fail (400/500) -- that is OK.  We are only
    testing whether the synchronous obfuscation subprocess blocks the
    event loop and delays unrelated requests.
    """

    async def _run():
        async with httpx.AsyncClient(base_url=empire_base_url, timeout=300.0) as client:

            async def execute_module_task():
                return await timed_request(
                    client,
                    "POST",
                    f"/api/v2/agents/{test_agent_id}/tasks/module",
                    headers=auth_header,
                    json={
                        "module_id": powershell_module_id,
                        "options": {},
                    },
                )

            async def probe_management_api():
                await asyncio.sleep(STAGER_WAIT_BEFORE_PROBE_SECONDS)
                tasks = [
                    timed_request(
                        client,
                        "GET",
                        "/api/v2/listeners",
                        headers=auth_header,
                    )
                    for _ in range(5)
                ]
                return await asyncio.gather(*tasks)

            module_task = asyncio.create_task(execute_module_task())
            probe_task = asyncio.create_task(probe_management_api())
            module_result, probe_results = await asyncio.gather(module_task, probe_task)
            return module_result, probe_results

    module_result, probe_results = asyncio.run(_run())

    log.info(
        "Module task latency: %.3fs (status=%s)",
        module_result.latency,
        module_result.status_code,
    )
    log.info("Probe summary: %s", summarize_results(list(probe_results)))

    for i, probe in enumerate(probe_results):
        assert probe.error is None, (
            f"Management API probe {i} returned an error: {probe.error}"
        )
        assert probe.status_code == HTTP_200_OK, (
            f"Management API probe {i} returned status {probe.status_code}, "
            f"expected {HTTP_200_OK}"
        )
        assert probe.latency <= MAX_MANAGEMENT_LATENCY_SECONDS, (
            f"Management API call took {probe.latency:.2f}s "
            f"(max {MAX_MANAGEMENT_LATENCY_SECONDS}s). "
            f"Likely blocked behind module obfuscation."
        )
