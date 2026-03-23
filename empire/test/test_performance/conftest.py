"""Fixtures for Empire performance tests.

Spins up a real MySQL container and an Empire server subprocess so that
performance tests can exercise the full stack over HTTP.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest
import yaml
from starlette.status import HTTP_200_OK

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable constants
# ---------------------------------------------------------------------------
MYSQL_IMAGE = "mysql:8.0"
MYSQL_USER = "empire_user"
MYSQL_PASSWORD = "empire_password"
MYSQL_DATABASE = "perf_test_empire"
MYSQL_ROOT_PASSWORD = "root"
MYSQL_STARTUP_TIMEOUT = 60
EMPIRE_STARTUP_TIMEOUT = 120

# Pool exhaustion test
CONCURRENT_CHECKINS = 25
MAX_ERROR_RATE = 0.0
MAX_P99_LATENCY_SECONDS = 5.0
MAX_POOL_ERRORS = 0

# Sync blocking test
MAX_MANAGEMENT_LATENCY_SECONDS = 1.0
STAGER_WAIT_BEFORE_PROBE_SECONDS = 0.5
SIMULATED_BLOCK_SECONDS = 5


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def _find_free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float) -> None:
    """Block until *host*:*port* accepts a TCP connection or *timeout* expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f"{host}:{port} not reachable within {timeout}s")


def _wait_for_mysql_ready(
    container_name: str, user: str, password: str, timeout: float
) -> None:
    """Wait until the MySQL server inside *container_name* is query-ready."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "docker",
                "exec",
                container_name,
                "mysql",
                f"-u{user}",
                f"-p{password}",
                "-e",
                "SELECT 1",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise TimeoutError(f"MySQL in {container_name} not ready within {timeout}s")


SERVER_CONFIG_LOC = Path("empire/test/test_server_config.yaml")


def _build_test_config(mysql_port: int, empire_port: int) -> str:
    """Load the shared test config and override settings for perf testing."""
    with SERVER_CONFIG_LOC.open() as f:
        config = yaml.safe_load(f)

    config["api"]["port"] = empire_port
    config["database"]["use"] = "mysql"
    config["database"]["mysql"]["url"] = f"localhost:{mysql_port}"
    config["database"]["mysql"]["username"] = MYSQL_USER
    config["database"]["mysql"]["password"] = MYSQL_PASSWORD
    config["database"]["mysql"]["database_name"] = MYSQL_DATABASE
    # Use a small pool to catch connection leaks and amplification issues
    # that would be hidden by the default 25-connection pool.
    config["database"]["mysql"]["pool_size"] = 5
    config["database"]["mysql"]["max_overflow"] = 3
    config["starkiller"]["enabled"] = False
    config["submodules"]["auto_update"] = False
    config["logging"]["level"] = "WARNING"

    return yaml.dump(config, default_flow_style=False)


def _wait_for_empire(
    base_url: str, timeout: float, proc: subprocess.Popen, log_path: str
) -> None:
    """Poll ``POST /token`` until Empire responds with HTTP 200.

    Also checks whether the subprocess has crashed so we can fail fast
    with a useful error instead of waiting the full timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Fail fast if the subprocess has exited
        if proc.poll() is not None:
            log_contents = Path(log_path).read_text()
            raise RuntimeError(
                f"Empire subprocess exited with code {proc.returncode}. "
                f"Log output:\n{log_contents}"
            )
        try:
            resp = httpx.post(
                f"{base_url}/token",
                data={
                    "grant_type": "password",
                    "username": "empireadmin",
                    "password": "password123",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=5,
            )
            if resp.status_code == HTTP_200_OK:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise TimeoutError(f"Empire not ready at {base_url} within {timeout}s")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def mysql_port():
    """Start a MySQL container, yield its host port, then tear it down."""
    port = _find_free_port()
    container_name = f"empire-perf-mysql-{port}"

    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            container_name,
            "-e",
            f"MYSQL_ROOT_PASSWORD={MYSQL_ROOT_PASSWORD}",
            "-e",
            f"MYSQL_USER={MYSQL_USER}",
            "-e",
            f"MYSQL_PASSWORD={MYSQL_PASSWORD}",
            "-e",
            f"MYSQL_DATABASE={MYSQL_DATABASE}",
            "-p",
            f"{port}:3306",
            MYSQL_IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    try:
        _wait_for_port("127.0.0.1", port, MYSQL_STARTUP_TIMEOUT)
        _wait_for_mysql_ready(
            container_name, MYSQL_USER, MYSQL_PASSWORD, MYSQL_STARTUP_TIMEOUT
        )
        yield port
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            check=False,
            capture_output=True,
        )


@pytest.fixture(scope="session")
def empire_log_path():
    """Create a temporary file for Empire server logs."""
    fd, path = tempfile.mkstemp(prefix="empire_perf_", suffix=".log")
    os.close(fd)
    return path
    # Leave the log file around for post-mortem debugging; the OS will
    # clean it up eventually via /tmp.


@pytest.fixture(scope="session")
def empire_base_url(mysql_port, empire_log_path):
    """Start Empire as a subprocess against the MySQL container.

    Yields the base URL once the server is accepting requests.
    """
    empire_port = _find_free_port()
    config_yaml = _build_test_config(mysql_port, empire_port)

    config_fd, config_path = tempfile.mkstemp(
        prefix="empire_perf_config_", suffix=".yaml"
    )
    with os.fdopen(config_fd, "w") as f:
        f.write(config_yaml)

    env = os.environ.copy()
    env["DATABASE_USE"] = "mysql"

    log_fh = Path(empire_log_path).open("w")  # noqa: SIM115

    log.info("Starting Empire subprocess on port %d", empire_port)
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
        _wait_for_empire(base_url, EMPIRE_STARTUP_TIMEOUT, proc, empire_log_path)
        yield base_url
    finally:
        # Graceful shutdown
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log_fh.close()
        Path(config_path).unlink()


@pytest.fixture(scope="session")
def auth_token(empire_base_url):
    """Obtain an admin access token from the running Empire server."""
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
    resp.raise_for_status()
    return resp.json()["access_token"]


@pytest.fixture(scope="session")
def auth_header(auth_token):
    """Return a dict suitable for passing as ``headers=`` to httpx."""
    return {"X-Empire-Token": f"Bearer {auth_token}"}
