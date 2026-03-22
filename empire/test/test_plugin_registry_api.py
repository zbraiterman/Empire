import asyncio
import inspect
from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import ANY, AsyncMock

import pytest
from starlette import status

from empire.server.core.db.base import SessionLocal
from empire.server.core.plugin_registry_service import PluginRegistryService

if TYPE_CHECKING:
    from empire.server.common.empire import MainMenu


@pytest.fixture(scope="module")
def plugin_service(main: "MainMenu"):
    return main.pluginsv2


@pytest.fixture(scope="module")
def plugin_registry_service(main: "MainMenu"):
    return main.pluginregistriesv2


def test_get_marketplace(client, admin_auth_header):
    response = client.get(
        "/api/v2/plugin-registries/marketplace", headers=admin_auth_header
    )
    assert response.status_code == status.HTTP_200_OK

    marketplace = response.json()
    assert len(marketplace["records"]) > 0

    slack = marketplace["records"][0]
    assert slack["name"] == "slack"
    assert "BC-SECURITY" in slack["registries"]
    assert "BC-SECURITY-TEST" in slack["registries"]


def test_install_plugin_plugin_not_found(client, admin_auth_header):
    response = client.post(
        "/api/v2/plugin-registries/marketplace/install",
        json={"name": "not-a-plugin", "version": "1.0", "registry": "BC-SECURITY"},
        headers=admin_auth_header,
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"detail": "Plugin not found in marketplace"}


@contextmanager
def patch_installed_plugin(plugin_name, session_local, models):
    with session_local.begin() as db:
        db.add(models.Plugin(id=plugin_name, name=plugin_name, enabled=True))

    yield

    with session_local.begin() as db:
        db.query(models.Plugin).filter(models.Plugin.id == plugin_name).delete()


def test_install_plugin_plugin_already_installed(
    client, admin_auth_header, session_local, models
):
    with patch_installed_plugin("slack", session_local, models):
        response = client.post(
            "/api/v2/plugin-registries/marketplace/install",
            json={"name": "slack", "version": "1.0.0", "registry": "BC-SECURITY"},
            headers=admin_auth_header,
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "Plugin already installed"}


def test_install_plugin_registry_not_found(client, admin_auth_header):
    response = client.post(
        "/api/v2/plugin-registries/marketplace/install",
        json={"name": "slack", "version": "1.0", "registry": "not-a-registry"},
        headers=admin_auth_header,
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"detail": "Plugin not found in registry"}


def test_install_plugin_version_not_found(client, admin_auth_header):
    response = client.post(
        "/api/v2/plugin-registries/marketplace/install",
        json={"name": "slack", "version": "not-a-version", "registry": "BC-SECURITY"},
        headers=admin_auth_header,
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {"detail": "Version not found in plugin"}


@contextmanager
def patch_install_plugin_from_git(plugin_service):
    mock = AsyncMock()
    original = plugin_service.install_plugin_from_git_async
    plugin_service.install_plugin_from_git_async = mock

    yield mock

    plugin_service.install_plugin_from_git_async = original


class IsDict:
    __hash__ = None  # not hashable; used only for __eq__ assertions

    def __eq__(self, other):
        return isinstance(other, dict)


def test_install_plugin_git(client, admin_auth_header, plugin_service):
    with patch_install_plugin_from_git(plugin_service) as mock:
        response = client.post(
            "/api/v2/plugin-registries/marketplace/install",
            json={"name": "slack", "version": "1.0.0", "registry": "BC-SECURITY"},
            headers=admin_auth_header,
        )
        assert response.status_code == status.HTTP_200_OK

        # db: Session,
        # git_url: str,
        # subdir: str | None = None,
        # ref: str | None = None,
        # version_name: str | None = None,
        # registry_data: dict | None = None,
        mock.assert_called_once_with(
            ANY,
            "https://github.com/bc-security/slack-plugin",
            None,
            "v1.0.0",
            "1.0.0",
            IsDict(),
        )


@contextmanager
def patch_install_plugin_from_tar(plugin_service):
    mock = AsyncMock()
    original = plugin_service.install_plugin_from_tar_async
    plugin_service.install_plugin_from_tar_async = mock

    yield mock

    plugin_service.install_plugin_from_tar_async = original


def test_install_plugin_tar(client, admin_auth_header, plugin_service):
    with patch_install_plugin_from_tar(plugin_service) as mock:
        response = client.post(
            "/api/v2/plugin-registries/marketplace/install",
            json={"name": "slack", "version": "1.0.0", "registry": "BC-SECURITY-TEST"},
            headers=admin_auth_header,
        )
        assert response.status_code == status.HTTP_200_OK

        # db: Session,
        # tar_url: str,
        # subdir: str | None = None,
        # version_name: str | None = None,
        # registry_data: dict | None = None,
        mock.assert_called_once_with(
            ANY,
            "https://github.com/bc-security/slack-other/releases/download/v1.0.0/slack.tar.gz",
            None,
            "1.0.0",
            IsDict(),
        )


def test_install_plugin_is_async():
    """Guard against install_plugin regressing to a sync function.

    PR #1211 made install_plugin async; the setup codepath in empire.py
    depends on this so it can ``await`` the call via ``asyncio.run()``.
    If someone accidentally removes the ``async``, the ``await`` in empire.py
    will raise a TypeError and this test will also fail.
    """
    assert inspect.iscoroutinefunction(PluginRegistryService.install_plugin)


def test_auto_install_awaits_install_plugin(plugin_registry_service, plugin_service):
    """Simulate the setup auto-install loop and verify install_plugin is awaited.

    This is a regression test for the bug where empire.py called the now-async
    install_plugin() synchronously, producing a silently discarded coroutine.
    """
    mock_git = AsyncMock()
    original_git = plugin_service.install_plugin_from_git_async
    plugin_service.install_plugin_from_git_async = mock_git

    try:
        # Simulate what empire.py's _auto_install_plugins does:
        # await main.pluginregistriesv2.install_plugin(db, name, version, registry)
        with SessionLocal.begin() as db:
            coro = plugin_registry_service.install_plugin(
                db, "slack", "1.0.0", "BC-SECURITY"
            )
            # The return value MUST be a coroutine — if it's not, the
            # setup code's ``await`` would raise TypeError.
            assert inspect.iscoroutine(coro), (
                "install_plugin() must return a coroutine; "
                "setup auto-install depends on awaiting it"
            )
            asyncio.run(coro)

        mock_git.assert_called_once_with(
            ANY,
            "https://github.com/bc-security/slack-plugin",
            None,
            "v1.0.0",
            "1.0.0",
            IsDict(),
        )
    finally:
        plugin_service.install_plugin_from_git_async = original_git
