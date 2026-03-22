"""Tests for sync-blocking behaviour in async FastAPI handlers.

Empire uses blocking ``subprocess.run()`` for stager compilation inside
async request handlers.  This blocks the entire event loop, causing
unrelated management API requests to queue behind long-running stager
builds.  These tests detect that behaviour.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from starlette.status import HTTP_200_OK, HTTP_201_CREATED, HTTP_400_BAD_REQUEST

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

# ---------------------------------------------------------------------------
# Shared stager options
# ---------------------------------------------------------------------------
_STAGER_OPTIONS = {
    "Listener": "",  # filled in per-call
    "Language": "powershell",
    "StagerRetries": "0",
    "OutFile": "",
    "Base64": "True",
    "Obfuscate": "False",
    "ObfuscateCommand": "Token\\All\\1",
    "SafeChecks": "True",
    "UserAgent": "default",
    "Proxy": "default",
    "ProxyCreds": "default",
    "Bypasses": "mattifestation etw",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def listener_name(empire_base_url: str, auth_header: dict[str, str]) -> str:
    """Create (or re-use) an HTTP listener for stager tests."""
    name = "perf-test-listener"
    resp = httpx.post(
        f"{empire_base_url}/api/v2/listeners/",
        headers=auth_header,
        json={
            "name": name,
            "template": "http",
            "options": {
                "Name": name,
                "Host": "http://127.0.0.1",
                "Port": "8080",
            },
        },
        timeout=60,
    )
    if resp.status_code == HTTP_201_CREATED:
        return name
    if resp.status_code == HTTP_400_BAD_REQUEST and "already exists" in resp.text:
        return name
    if resp.status_code != HTTP_201_CREATED:
        pytest.fail(f"Failed to create listener: {resp.status_code} {resp.text}")
    # Should not reach here, but satisfy the type checker.
    return name


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_stager_creation_does_not_block_management_api(
    empire_base_url: str,
    auth_header: dict[str, str],
    listener_name: str,
):
    """While a stager is being compiled, lightweight management API calls
    (``GET /api/v2/listeners``) should still respond within
    ``MAX_MANAGEMENT_LATENCY_SECONDS``.

    If the event loop is blocked by a synchronous ``subprocess.run()`` call,
    the management probes will be delayed until the stager build finishes.
    """

    async def _run():
        async with httpx.AsyncClient(base_url=empire_base_url, timeout=60.0) as client:

            async def create_stager():
                options = {**_STAGER_OPTIONS, "Listener": listener_name}
                return await timed_request(
                    client,
                    "POST",
                    "/api/v2/stagers/",
                    headers=auth_header,
                    json={
                        "name": "perf-test-stager",
                        "template": "multi_launcher",
                        "options": options,
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

            stager_task = asyncio.create_task(create_stager())
            probe_task = asyncio.create_task(probe_management_api())
            stager_result, probe_results = await asyncio.gather(stager_task, probe_task)
            return stager_result, probe_results

    stager_result, probe_results = asyncio.run(_run())

    log.info("Stager creation latency: %.3fs", stager_result.latency)
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
            f"Likely blocked behind stager creation."
        )


@pytest.mark.slow
def test_multiple_stager_requests_do_not_serialize(
    empire_base_url: str,
    auth_header: dict[str, str],
    listener_name: str,
):
    """Fire 3 concurrent stager creation requests and measure whether they
    run in parallel or serialize behind a single-threaded event loop.

    This test is informational -- it logs a *serialization ratio* but does
    **not** hard-assert on it.
    """

    async def _run():
        async with httpx.AsyncClient(base_url=empire_base_url, timeout=60.0) as client:
            options = {**_STAGER_OPTIONS, "Listener": listener_name}

            async def _create(name: str, opts: dict):
                return await timed_request(
                    client,
                    "POST",
                    "/api/v2/stagers/",
                    headers=auth_header,
                    json={
                        "name": name,
                        "template": "multi_launcher",
                        "options": opts,
                    },
                )

            tasks = [_create(f"perf-serial-test-{i}", options) for i in range(3)]
            return await asyncio.gather(*tasks)

    results = asyncio.run(_run())

    summary = summarize_results(list(results))
    log.info("Concurrent stager creation summary: %s", summary)

    latencies = [r.latency for r in results]
    ratio = max(latencies) / min(latencies) if min(latencies) > 0 else float("inf")
    log.info(
        "Serialization ratio: %.2f (1.0 = perfect parallel, 3.0 = fully serial)",
        ratio,
    )


@pytest.mark.slow
@pytest.mark.compiler
def test_csharp_stager_does_not_block_management_api(
    empire_base_url: str,
    auth_header: dict[str, str],
    listener_name: str,
):
    """C# stager compilation with obfuscation should not block management API.

    This reproduces the exact production incident: ``POST /api/v2/stagers``
    for a C# stager with obfuscation takes ~8 s via blocking
    ``subprocess.run()``, during which all concurrent requests are queued.

    Requires: EmpireCompiler (C# compiler) to be available.
    """

    async def _run():
        async with httpx.AsyncClient(base_url=empire_base_url, timeout=120.0) as client:

            async def create_csharp_stager():
                return await timed_request(
                    client,
                    "POST",
                    "/api/v2/stagers/",
                    headers=auth_header,
                    json={
                        "name": "perf-csharp-stager",
                        "template": "windows_csharp_exe",
                        "options": {
                            "Listener": listener_name,
                            "Language": "csharp",
                            "DotNetVersion": "net40",
                            "StagerRetries": "0",
                            "OutFile": "perf-test.exe",
                            "Obfuscate": "True",
                            "ObfuscateCommand": "",
                            "Bypasses": "",
                        },
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

            stager_task = asyncio.create_task(create_csharp_stager())
            probe_task = asyncio.create_task(probe_management_api())
            stager_result, probe_results = await asyncio.gather(stager_task, probe_task)
            return stager_result, probe_results

    stager_result, probe_results = asyncio.run(_run())

    log.info(
        "C# stager creation latency: %.3fs (status=%d)",
        stager_result.latency,
        stager_result.status_code,
    )
    log.info("Probe summary: %s", summarize_results(list(probe_results)))

    for i, probe in enumerate(probe_results):
        assert probe.error is None, (
            f"Management API probe {i} returned an error: {probe.error}"
        )
        assert probe.latency <= MAX_MANAGEMENT_LATENCY_SECONDS, (
            f"Management API call took {probe.latency:.2f}s while C# stager "
            f"was compiling ({stager_result.latency:.2f}s). "
            f"Event loop is blocked by synchronous subprocess.run()."
        )
