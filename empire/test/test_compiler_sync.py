from unittest.mock import MagicMock, patch

from empire.server.core.config.config_manager import EmpireCompilerConfig
from empire.server.core.config.data_manager import (
    _resolve_compiler_download_url,
    _resolve_compiler_platform,
)


@patch("empire.server.core.config.data_manager.platform")
def test_resolve_platform_linux_x86_64(mock_platform):
    mock_platform.system.return_value = "Linux"
    mock_platform.machine.return_value = "x86_64"
    assert _resolve_compiler_platform() == ("linux", "x64")


@patch("empire.server.core.config.data_manager.platform")
def test_resolve_platform_darwin_arm64(mock_platform):
    mock_platform.system.return_value = "Darwin"
    mock_platform.machine.return_value = "arm64"
    assert _resolve_compiler_platform() == ("osx", "arm64")


@patch("empire.server.core.config.data_manager.platform")
def test_resolve_platform_darwin_aarch64(mock_platform):
    mock_platform.system.return_value = "Darwin"
    mock_platform.machine.return_value = "aarch64"
    assert _resolve_compiler_platform() == ("osx", "arm64")


@patch("empire.server.core.config.data_manager.platform")
def test_resolve_platform_linux_arm64(mock_platform):
    mock_platform.system.return_value = "Linux"
    mock_platform.machine.return_value = "arm64"
    assert _resolve_compiler_platform() == ("linux", "arm64")


def test_download_url_missing_repo_and_ref():
    config = EmpireCompilerConfig()
    assert _resolve_compiler_download_url(config, "linux-x64") is None


def test_download_url_missing_ref():
    config = EmpireCompilerConfig(repo="BC-SECURITY/Empire-Compiler")
    assert _resolve_compiler_download_url(config, "linux-x64") is None


@patch("empire.server.core.config.data_manager.requests")
def test_download_url_repo_ref_finds_asset(mock_requests):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "assets": [
            {
                "name": "EmpireCompiler-linux-x64-v0.4.4.tgz",
                "browser_download_url": "https://github.com/downloads/EmpireCompiler-linux-x64-v0.4.4.tgz",
            },
            {
                "name": "EmpireCompiler-osx-arm64-v0.4.4.tgz",
                "browser_download_url": "https://github.com/downloads/EmpireCompiler-osx-arm64-v0.4.4.tgz",
            },
        ]
    }
    mock_requests.get.return_value = mock_resp

    config = EmpireCompilerConfig(repo="BC-SECURITY/Empire-Compiler", ref="v0.4.4")
    url = _resolve_compiler_download_url(config, "linux-x64")
    assert url == "https://github.com/downloads/EmpireCompiler-linux-x64-v0.4.4.tgz"

    mock_requests.get.assert_called_once_with(
        "https://api.github.com/repos/BC-SECURITY/Empire-Compiler/releases/tags/v0.4.4",
        timeout=30,
    )


@patch("empire.server.core.config.data_manager.requests")
def test_download_url_repo_ref_no_matching_asset(mock_requests):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "assets": [
            {
                "name": "EmpireCompiler-osx-arm64-v0.4.4.tgz",
                "browser_download_url": "https://example.com/osx.tgz",
            },
        ]
    }
    mock_requests.get.return_value = mock_resp

    config = EmpireCompilerConfig(repo="BC-SECURITY/Empire-Compiler", ref="v0.4.4")
    assert _resolve_compiler_download_url(config, "linux-x64") is None


@patch("empire.server.core.config.data_manager.requests")
def test_download_url_repo_ref_api_failure(mock_requests):
    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 404
    mock_resp.text = "Not Found"
    mock_requests.get.return_value = mock_resp

    config = EmpireCompilerConfig(repo="BC-SECURITY/Empire-Compiler", ref="v99.0.0")
    assert _resolve_compiler_download_url(config, "linux-x64") is None
