"""Integration tests for hook connection amplification under concurrent load.

These tests run against a real Empire server with a constrained DB pool
to verify that async hooks don't exhaust connections under load.  The
constrained pool (pool_size=3, max_overflow=2 = 5 total) makes
amplification failures deterministic: if hooks hold 2 connections per
request, only 2 concurrent requests exhaust the pool.

Requires MySQL (Docker) — skipped without ``--mysql`` marker.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import tempfile
from pathlib import Path

import httpx
import pytest
import yaml
from starlette.status import HTTP_200_OK, HTTP_500_INTERNAL_SERVER_ERROR

from empire.test.test_performance.conftest import (
    EMPIRE_STARTUP_TIMEOUT,
    MAX_MANAGEMENT_LATENCY_SECONDS,
    MYSQL_DATABASE,
    MYSQL_PASSWORD,
    MYSQL_USER,
    SERVER_CONFIG_LOC,
    _find_free_port,
    _wait_for_empire,
)
from empire.test.test_performance.helpers import (
    summarize_results,
    timed_request,
)

log = logging.getLogger(__name__)

pytestmark = [pytest.mark.slow, pytest.mark.mysql]

# Use a very small pool to make amplification failures deterministic.
# With 2x amplification, 3 concurrent requests would need 6 connections
# and exhaust a pool of 5.
CONSTRAINED_POOL_SIZE = 3
CONSTRAINED_MAX_OVERFLOW = 2
TOTAL_POOL = CONSTRAINED_POOL_SIZE + CONSTRAINED_MAX_OVERFLOW  # 5

# Number of concurrent requests to fire.  Must exceed TOTAL_POOL / 2
# so that 2x amplification would exhaust the pool, but stay under
# TOTAL_POOL so that 1x usage (after fix) fits.
CONCURRENT_REQUESTS = 4


@pytest.fixture(scope="module")
def constrained_empire_base_url(mysql_port, empire_log_path):
    """Start Empire with a constrained DB pool for amplification testing."""
    empire_port = _find_free_port()

    with SERVER_CONFIG_LOC.open() as f:
        config = yaml.safe_load(f)

    config["api"]["port"] = empire_port
    config["database"]["use"] = "mysql"
    config["database"]["mysql"]["url"] = f"localhost:{mysql_port}"
    config["database"]["mysql"]["username"] = MYSQL_USER
    config["database"]["mysql"]["password"] = MYSQL_PASSWORD
    config["database"]["mysql"]["database_name"] = MYSQL_DATABASE
    config["database"]["mysql"]["pool_size"] = CONSTRAINED_POOL_SIZE
    config["database"]["mysql"]["max_overflow"] = CONSTRAINED_MAX_OVERFLOW
    config["starkiller"]["enabled"] = False
    config["submodules"]["auto_update"] = False
    config["logging"]["level"] = "WARNING"

    config_fd, config_path = tempfile.mkstemp(
        prefix="empire_constrained_config_", suffix=".yaml"
    )
    with os.fdopen(config_fd, "w") as f:
        yaml.dump(config, f, default_flow_style=False)

    env = os.environ.copy()
    env["DATABASE_USE"] = "mysql"

    log_path = empire_log_path.replace(".log", "_constrained.log")
    log_fh = Path(log_path).open("w")  # noqa: SIM115

    log.info(
        "Starting constrained Empire (pool=%d+%d) on port %d",
        CONSTRAINED_POOL_SIZE,
        CONSTRAINED_MAX_OVERFLOW,
        empire_port,
    )
    proc = subprocess.Popen(
        [
            "poetry",
            "run",
            "python",
            "empire.py",
            "server",
            "--config",
            config_path,
        ],
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )

    base_url = f"http://127.0.0.1:{empire_port}"
    try:
        _wait_for_empire(base_url, EMPIRE_STARTUP_TIMEOUT, proc, log_path)
        yield base_url
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log_fh.close()
        Path(config_path).unlink()


@pytest.fixture(scope="module")
def constrained_auth_header(constrained_empire_base_url):
    """Obtain an admin access token from the constrained Empire server."""
    resp = httpx.post(
        f"{constrained_empire_base_url}/token",
        data={
            "grant_type": "password",
            "username": "empireadmin",
            "password": "password123",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    return {"X-Empire-Token": f"Bearer {token}"}


@pytest.mark.slow
def test_concurrent_db_requests_with_constrained_pool(
    constrained_empire_base_url: str,
    constrained_auth_header: dict[str, str],
    empire_log_path: str,
):
    """Fire CONCURRENT_REQUESTS simultaneous DB-hitting requests against
    Empire with a constrained pool.

    With pool_size=3, max_overflow=2 (5 total), 4 concurrent requests
    should succeed if each uses only 1 connection.  If hooks cause 2x
    amplification, 4 requests would need 8 connections and exhaust the
    pool.
    """
    endpoints = ["/api/v2/agents", "/api/v2/listeners", "/api/v2/users/me"]

    async def _run():
        async with httpx.AsyncClient(
            base_url=constrained_empire_base_url, timeout=30.0
        ) as client:
            tasks = [
                timed_request(
                    client,
                    "GET",
                    endpoints[i % len(endpoints)],
                    headers=constrained_auth_header,
                )
                for i in range(CONCURRENT_REQUESTS)
            ]
            return await asyncio.gather(*tasks)

    results = asyncio.run(_run())
    summary = summarize_results(list(results))
    log.info("Constrained pool concurrent requests: %s", summary)

    server_errors = sum(
        count
        for status, count in summary["status_codes"].items()
        if status >= HTTP_500_INTERNAL_SERVER_ERROR
    )
    assert server_errors == 0, (
        f"Got {server_errors} server errors with constrained pool "
        f"(pool={CONSTRAINED_POOL_SIZE}+{CONSTRAINED_MAX_OVERFLOW}): "
        f"{summary['status_codes']}"
    )

    log_path = empire_log_path.replace(".log", "_constrained.log")
    if Path(log_path).exists():
        log_contents = Path(log_path).read_text()
        pool_errors = log_contents.count("QueuePool limit")
        assert pool_errors == 0, (
            f"Found {pool_errors} 'QueuePool limit' errors with constrained "
            f"pool — hooks may be amplifying connection usage"
        )


@pytest.mark.slow
def test_management_api_responsive_during_concurrent_load(
    constrained_empire_base_url: str,
    constrained_auth_header: dict[str, str],
):
    """While concurrent DB requests are in flight, the management API
    must still respond within MAX_MANAGEMENT_LATENCY_SECONDS.

    This catches event loop blocking (async def handlers with sync DB)
    and pool exhaustion (hooks holding 2 connections).
    """

    async def _run():
        async with httpx.AsyncClient(
            base_url=constrained_empire_base_url, timeout=30.0
        ) as client:

            async def _db_load():
                """Generate sustained DB load."""
                tasks = []
                for i in range(CONCURRENT_REQUESTS):
                    await asyncio.sleep(0.1 * i)  # stagger arrivals
                    tasks.append(
                        timed_request(
                            client,
                            "GET",
                            "/api/v2/agents",
                            headers=constrained_auth_header,
                        )
                    )
                return await asyncio.gather(*tasks)

            async def _probe_management():
                """Probe management API while load is running."""
                await asyncio.sleep(0.3)  # let some load requests start
                probes = [
                    timed_request(
                        client,
                        "GET",
                        "/api/v2/listeners",
                        headers=constrained_auth_header,
                    )
                    for _ in range(3)
                ]
                return await asyncio.gather(*probes)

            load_task = asyncio.create_task(_db_load())
            probe_task = asyncio.create_task(_probe_management())
            load_results, probe_results = await asyncio.gather(load_task, probe_task)
            return load_results, probe_results

    load_results, probe_results = asyncio.run(_run())

    log.info("Load summary: %s", summarize_results(list(load_results)))
    log.info("Probe summary: %s", summarize_results(list(probe_results)))

    for i, probe in enumerate(probe_results):
        assert probe.status_code == HTTP_200_OK, (
            f"Management probe {i} returned {probe.status_code} during "
            f"concurrent load (expected {HTTP_200_OK})"
        )
        assert probe.latency <= MAX_MANAGEMENT_LATENCY_SECONDS, (
            f"Management probe {i} took {probe.latency:.2f}s during "
            f"concurrent load (max {MAX_MANAGEMENT_LATENCY_SECONDS}s). "
            f"Pool exhaustion or event loop blocking likely."
        )
