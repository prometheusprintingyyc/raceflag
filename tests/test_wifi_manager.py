import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from raceflag.wifi_manager import WiFiManager
from raceflag.config import Config


@pytest.fixture
def config():
    return Config(wifi_ssid="MyNet", wifi_password="secret")


@pytest.fixture
def manager(config):
    return WiFiManager(config=config)


def test_is_connected_returns_false_initially(manager):
    assert manager.is_connected() is False


def test_get_ssid_returns_empty_initially(manager):
    assert manager.get_ssid() == ""


@pytest.mark.asyncio
async def test_check_connectivity_returns_true_when_ping_succeeds(manager, mocker):
    mock_proc = MagicMock()
    mock_proc.wait = AsyncMock(return_value=0)
    mock_proc.returncode = 0
    mocker.patch("raceflag.wifi_manager.asyncio.create_subprocess_exec", return_value=mock_proc)
    result = await manager._check_connectivity()
    assert result is True


@pytest.mark.asyncio
async def test_check_connectivity_returns_false_when_ping_fails(manager, mocker):
    mock_proc = MagicMock()
    mock_proc.wait = AsyncMock(return_value=1)
    mock_proc.returncode = 1
    mocker.patch("raceflag.wifi_manager.asyncio.create_subprocess_exec", return_value=mock_proc)
    result = await manager._check_connectivity()
    assert result is False


@pytest.mark.asyncio
async def test_enable_hotspot_runs_hostapd(manager, mocker):
    mock_proc = MagicMock()
    mock_proc.wait = AsyncMock(return_value=0)
    mock_run = mocker.patch("raceflag.wifi_manager.asyncio.create_subprocess_exec", return_value=mock_proc)
    mocker.patch("raceflag.wifi_manager.Path.write_text")
    await manager.enable_hotspot()
    calls = [str(c) for c in mock_run.call_args_list]
    assert any("hostapd" in c for c in calls)


@pytest.mark.asyncio
async def test_connect_updates_config(manager, config, tmp_path, mocker):
    config_path = tmp_path / "config.json"
    manager._config_path = config_path
    mock_proc = MagicMock()
    mock_proc.wait = AsyncMock(return_value=0)
    mock_proc.returncode = 0
    mocker.patch("raceflag.wifi_manager.asyncio.create_subprocess_exec", return_value=mock_proc)
    await manager.connect("NewNet", "newpass")
    assert manager._config.wifi_ssid == "NewNet"
    assert manager._config.wifi_password == "newpass"
