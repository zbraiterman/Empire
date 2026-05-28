import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette import status

from empire.server.core.obfuscation_service import ObfuscationService


def test_get_keyword_not_found(client, admin_auth_header):
    response = client.get(
        "/api/v2/obfuscation/keywords/9999", headers=admin_auth_header
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Keyword not found for id 9999"


def test_get_keyword(client, admin_auth_header):
    response = client.get("/api/v2/obfuscation/keywords/1", headers=admin_auth_header)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["id"] == 1
    assert len(response.json()["replacement"]) > 0


def test_get_keywords(client, admin_auth_header):
    response = client.get("/api/v2/obfuscation/keywords", headers=admin_auth_header)

    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()["records"]) > 0


def test_create_keyword_name_conflict(client, admin_auth_header):
    response = client.post(
        "/api/v2/obfuscation/keywords/",
        headers=admin_auth_header,
        json={"keyword": "Invoke-Mimikatz", "replacement": "Invoke-Hax"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        response.json()["detail"] == "Keyword with name Invoke-Mimikatz already exists."
    )


def test_create_keyword_validate_length(client, admin_auth_header):
    response = client.post(
        "/api/v2/obfuscation/keywords/",
        headers=admin_auth_header,
        json={"keyword": "a", "replacement": "b"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert (
        response.json()["detail"][0]["msg"]
        == "String should have at least 3 characters"
    )


def test_create_keyword(client, admin_auth_header):
    response = client.post(
        "/api/v2/obfuscation/keywords/",
        headers=admin_auth_header,
        json={"keyword": "Invoke-Things", "replacement": "Invoke-sgnihT;"},
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["keyword"] == "Invoke-Things"
    assert response.json()["replacement"] == "Invoke-sgnihT;"


def test_update_keyword_not_found(client, admin_auth_header):
    response = client.put(
        "/api/v2/obfuscation/keywords/9999",
        headers=admin_auth_header,
        json={"keyword": "thiswontwork", "replacement": "x=0;"},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Keyword not found for id 9999"


def test_update_keyword_name_conflict(client, admin_auth_header):
    response = client.put(
        "/api/v2/obfuscation/keywords/1",
        headers=admin_auth_header,
        json={"keyword": "Invoke-Mimikatz", "replacement": "Invoke-Whatever"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        response.json()["detail"] == "Keyword with name Invoke-Mimikatz already exists."
    )


def test_update_keyword(client, admin_auth_header):
    response = client.put(
        "/api/v2/obfuscation/keywords/1",
        headers=admin_auth_header,
        json={"keyword": "Completely-new_name", "replacement": "qwerefdsgaf"},
    )

    assert response.json()["keyword"] == "Completely-new_name"
    assert response.json()["replacement"] == "qwerefdsgaf"


def test_delete_keyword(client, admin_auth_header):
    response = client.delete(
        "/api/v2/obfuscation/keywords/1", headers=admin_auth_header
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT

    response = client.get("/api/v2/obfuscation/keywords/1", headers=admin_auth_header)

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_get_obfuscation_configs(client, admin_auth_header):
    response = client.get("/api/v2/obfuscation/global", headers=admin_auth_header)

    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()["records"]) > 1

    assert any(x["language"] == "powershell" for x in response.json()["records"])
    assert any(x["language"] == "csharp" for x in response.json()["records"])
    assert any(x["language"] == "python" for x in response.json()["records"])


def test_get_obfuscation_config_not_found(client, admin_auth_header):
    response = client.get(
        "/api/v2/obfuscation/global/madeup", headers=admin_auth_header
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert (
        response.json()["detail"]
        == "Obfuscation config not found for language madeup. Only powershell is supported."
    )


def test_get_obfuscation_config(client, admin_auth_header):
    response = client.get(
        "/api/v2/obfuscation/global/powershell", headers=admin_auth_header
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["language"] == "powershell"
    assert response.json()["enabled"] is False
    assert response.json()["command"] == r"Token\All\1"
    assert response.json()["module"] == "invoke-obfuscation"


def test_update_obfuscation_config_not_found(client, admin_auth_header):
    response = client.put(
        "/api/v2/obfuscation/global/madeup",
        headers=admin_auth_header,
        json={
            "language": "powershell",
            "command": "x=1;",
            "module": "x=1;",
            "enabled": True,
        },
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert (
        response.json()["detail"]
        == "Obfuscation config not found for language madeup. Only powershell is supported."
    )


def test_update_obfuscation_config(client, admin_auth_header):
    response = client.put(
        "/api/v2/obfuscation/global/powershell",
        headers=admin_auth_header,
        json={
            "language": "powershell",
            "command": r"Token\All\1",
            "module": "invoke-obfuscation",
            "enabled": True,
        },
    )

    assert response.json()["language"] == "powershell"
    assert response.json()["command"] == r"Token\All\1"
    assert response.json()["module"] == "invoke-obfuscation"
    assert response.json()["enabled"] is True


def test_preobfuscate_post_not_preobfuscatable(
    client, admin_auth_header, empire_config
):
    response = client.post(
        "/api/v2/obfuscation/global/csharp/preobfuscate", headers=admin_auth_header
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        response.json()["detail"]
        == "Obfuscation language csharp is not preobfuscatable."
    )


@contextmanager
def patch_main_source_path(main, path):
    old_path = main.modulesv2.module_source_path
    main.modulesv2.module_source_path = path
    yield
    main.modulesv2.module_source_path = old_path


@pytest.mark.slow
def test_preobfuscate_post(client, admin_auth_header, empire_config, main):
    module_source_dir = Path("empire/test/data/module_source")
    with patch_main_source_path(main, module_source_dir):
        response = client.post(
            "/api/v2/obfuscation/global/powershell/preobfuscate",
            headers=admin_auth_header,
        )

        # It is run as a background task, but in tests it runs synchronously.
        assert response.status_code == status.HTTP_202_ACCEPTED

        obf_module_dir = main.modulesv2._obfuscated_module_source_path

        count = 0
        for root, _dirs, files in os.walk(module_source_dir):
            for file in files:
                if not file.endswith(".ps1"):
                    continue
                root_rep = root.replace(str(module_source_dir), str(obf_module_dir))
                assert (Path(root_rep) / file).exists()
                count += 1

        assert count > 0


def test_preobfuscate_delete_not_preobfuscatable(
    client, admin_auth_header, empire_config
):
    response = client.delete(
        "/api/v2/obfuscation/global/csharp/preobfuscate", headers=admin_auth_header
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        response.json()["detail"]
        == "Obfuscation language csharp is not preobfuscatable."
    )


def test_preobfuscate_delete(main, client, admin_auth_header, empire_config):
    response = client.delete(
        "/api/v2/obfuscation/global/powershell/preobfuscate",
        headers=admin_auth_header,
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT

    module_dir = main.modulesv2.module_source_path
    obf_module_dir = main.modulesv2._obfuscated_module_source_path

    for root, _dirs, files in os.walk(module_dir):
        for file in files:
            root_rep = root.replace(str(module_dir), str(obf_module_dir))
            path = Path(root_rep + "/" + file)
            assert not path.exists()


def test_preobfuscate_modules_empty_list(client, admin_auth_header):
    response = client.post(
        "/api/v2/obfuscation/modules/preobfuscate",
        headers=admin_auth_header,
        json=[],
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "must not be empty" in response.json()["detail"]


def test_preobfuscate_modules_not_found(client, admin_auth_header):
    response = client.post(
        "/api/v2/obfuscation/modules/preobfuscate",
        headers=admin_auth_header,
        json=["nonexistent_module_id"],
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "nonexistent_module_id" in response.json()["detail"]


def test_preobfuscate_modules_deduplicates(client, admin_auth_header, main):
    """Duplicate module IDs should be accepted (deduplicated), not rejected."""
    with patch.object(main.modulesv2, "preobfuscate_module_by_id"):
        response = client.post(
            "/api/v2/obfuscation/modules/preobfuscate",
            headers=admin_auth_header,
            json=[
                "powershell_situational_awareness_network_arpscan",
                "powershell_situational_awareness_network_arpscan",
            ],
        )

    assert response.status_code == status.HTTP_202_ACCEPTED


def test_preobfuscate_modules_valid(client, admin_auth_header, main):
    with patch.object(main.modulesv2, "preobfuscate_module_by_id") as mock_preobfuscate:
        response = client.post(
            "/api/v2/obfuscation/modules/preobfuscate",
            headers=admin_auth_header,
            json=["powershell_situational_awareness_network_arpscan"],
        )

    assert response.status_code == status.HTTP_202_ACCEPTED
    mock_preobfuscate.assert_called_once_with(
        "powershell_situational_awareness_network_arpscan"
    )


def test_obfuscate_uses_config_timeout():
    """obfuscate() uses empire_config.obfuscation_timeout as the default timeout."""
    service = MagicMock(spec=ObfuscationService)
    service.obfuscate = ObfuscationService.obfuscate.__get__(service)
    service.main_menu = MagicMock()
    service.main_menu.install_path = "/tmp/fake"
    service.obfuscate_keywords = lambda data: data
    service._convert_obfuscation_command = lambda cmd: cmd

    with patch("empire.server.core.obfuscation_service.empire_config") as mock_config:
        mock_config.obfuscation.timeout = 999
        with patch("empire.server.core.obfuscation_service.data_util") as mock_util:
            mock_util.is_powershell_installed.return_value = True
            mock_util.get_powershell_name.return_value = "pwsh"
            with patch("empire.server.core.obfuscation_service.subprocess") as mock_sub:
                mock_sub.TimeoutExpired = subprocess.TimeoutExpired
                mock_sub.run.return_value = MagicMock(returncode=0, stderr=b"")
                service.obfuscate("echo test", "Token\\All\\1")
                _, kwargs = mock_sub.run.call_args
                assert kwargs["timeout"] == 999  # noqa: PLR2004


def test_obfuscate_explicit_timeout_overrides_config():
    """An explicit timeout parameter overrides the config value."""
    service = MagicMock(spec=ObfuscationService)
    service.obfuscate = ObfuscationService.obfuscate.__get__(service)
    service.main_menu = MagicMock()
    service.main_menu.install_path = "/tmp/fake"
    service.obfuscate_keywords = lambda data: data
    service._convert_obfuscation_command = lambda cmd: cmd

    with patch("empire.server.core.obfuscation_service.empire_config") as mock_config:
        mock_config.obfuscation.timeout = 999
        with patch("empire.server.core.obfuscation_service.data_util") as mock_util:
            mock_util.is_powershell_installed.return_value = True
            mock_util.get_powershell_name.return_value = "pwsh"
            with patch("empire.server.core.obfuscation_service.subprocess") as mock_sub:
                mock_sub.TimeoutExpired = subprocess.TimeoutExpired
                mock_sub.run.return_value = MagicMock(returncode=0, stderr=b"")
                service.obfuscate("echo test", "Token\\All\\1", timeout=42)
                _, kwargs = mock_sub.run.call_args
                assert kwargs["timeout"] == 42  # noqa: PLR2004


def test_obfuscate_zero_timeout_disables_timeout():
    """A timeout of 0 (from config) passes None to subprocess (no timeout)."""
    service = MagicMock(spec=ObfuscationService)
    service.obfuscate = ObfuscationService.obfuscate.__get__(service)
    service.main_menu = MagicMock()
    service.main_menu.install_path = "/tmp/fake"
    service.obfuscate_keywords = lambda data: data
    service._convert_obfuscation_command = lambda cmd: cmd

    with patch("empire.server.core.obfuscation_service.empire_config") as mock_config:
        mock_config.obfuscation.timeout = 0
        with patch("empire.server.core.obfuscation_service.data_util") as mock_util:
            mock_util.is_powershell_installed.return_value = True
            mock_util.get_powershell_name.return_value = "pwsh"
            with patch("empire.server.core.obfuscation_service.subprocess") as mock_sub:
                mock_sub.TimeoutExpired = subprocess.TimeoutExpired
                mock_sub.run.return_value = MagicMock(returncode=0, stderr=b"")
                service.obfuscate("echo test", "Token\\All\\1")
                _, kwargs = mock_sub.run.call_args
                assert kwargs["timeout"] is None
