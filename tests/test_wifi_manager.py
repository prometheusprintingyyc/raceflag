import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from raceflag.wifi_manager import WiFiManager, MAX_HOTSPOT_ATTEMPTS
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
    mock_proc.communicate = AsyncMock(return_value=(None, None))
    mock_proc.returncode = 0
    mocker.patch("raceflag.wifi_manager.asyncio.create_subprocess_exec", return_value=mock_proc)
    await manager.connect("NewNet", "newpass")
    assert manager._config.wifi_ssid == "NewNet"
    assert manager._config.wifi_password == "newpass"


@pytest.mark.asyncio
async def test_connect_to_configured_returns_true_on_success(manager, mocker):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(None, None))
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock(return_value=None)
    mocker.patch("raceflag.wifi_manager.asyncio.create_subprocess_exec", return_value=mock_proc)
    result = await manager._connect_to_configured()
    assert result is True


@pytest.mark.asyncio
async def test_connect_to_configured_returns_false_on_nonzero_exit(manager, mocker):
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(None, None))
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock(return_value=None)
    mocker.patch("raceflag.wifi_manager.asyncio.create_subprocess_exec", return_value=mock_proc)
    result = await manager._connect_to_configured()
    assert result is False


@pytest.mark.asyncio
async def test_connect_to_configured_returns_false_on_timeout(manager, mocker):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(None, None))
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock(return_value=None)
    mocker.patch("raceflag.wifi_manager.asyncio.create_subprocess_exec", return_value=mock_proc)
    mocker.patch("raceflag.wifi_manager.asyncio.wait_for", side_effect=asyncio.TimeoutError)
    result = await manager._connect_to_configured()
    assert result is False
    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_called()


@pytest.mark.asyncio
async def test_connect_reenables_hotspot_on_failure(manager, mocker):
    mocker.patch.object(manager, "disable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "_connect_to_configured", new=AsyncMock(return_value=False))
    await manager.connect("BadNet", "wrongpass")
    manager.enable_hotspot.assert_called_once()


@pytest.mark.asyncio
async def test_connect_does_not_reenable_hotspot_on_success(manager, mocker):
    mocker.patch.object(manager, "disable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "_connect_to_configured", new=AsyncMock(return_value=True))
    await manager.connect("GoodNet", "rightpass")
    manager.enable_hotspot.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_loop_sets_ever_connected_on_first_ping_success(manager, mocker):
    call_count = 0
    async def mock_check():
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            manager._running = False
        return True
    mocker.patch.object(manager, "_check_connectivity", side_effect=mock_check)
    mocker.patch("raceflag.wifi_manager.asyncio.sleep", new=AsyncMock())
    manager._running = True
    manager._hotspot_active = False
    await manager._monitor_loop()
    assert manager._ever_connected is True


@pytest.mark.asyncio
async def test_monitor_loop_enables_hotspot_after_2_failures_when_never_connected(manager, mocker):
    fail_calls = 0
    async def mock_check():
        nonlocal fail_calls
        fail_calls += 1
        if fail_calls > 2:
            manager._running = False
        return False
    mocker.patch.object(manager, "_check_connectivity", side_effect=mock_check)
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch("raceflag.wifi_manager.asyncio.sleep", new=AsyncMock())
    manager._running = True
    manager._hotspot_active = False
    manager._ever_connected = False
    await manager._monitor_loop()
    manager.enable_hotspot.assert_called_once()


@pytest.mark.asyncio
async def test_monitor_loop_enables_hotspot_after_10_failures_when_previously_connected(manager, mocker):
    fail_calls = 0
    async def mock_check():
        nonlocal fail_calls
        fail_calls += 1
        if fail_calls > 10:
            manager._running = False
        return False
    mocker.patch.object(manager, "_check_connectivity", side_effect=mock_check)
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch("raceflag.wifi_manager.asyncio.sleep", new=AsyncMock())
    manager._running = True
    manager._hotspot_active = False
    manager._ever_connected = True
    await manager._monitor_loop()
    manager.enable_hotspot.assert_called_once()


@pytest.mark.asyncio
async def test_monitor_loop_reenables_hotspot_immediately_on_failed_auto_reconnect(manager, mocker):
    call_count = 0
    async def mock_connect():
        nonlocal call_count
        call_count += 1
        manager._running = False
        return False
    mocker.patch.object(manager, "_check_configured_available", new=AsyncMock(return_value=True))
    mocker.patch.object(manager, "_connect_to_configured", side_effect=mock_connect)
    mocker.patch.object(manager, "disable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch("raceflag.wifi_manager.asyncio.sleep", new=AsyncMock())
    manager._running = True
    manager._hotspot_active = True
    await manager._monitor_loop()
    manager.enable_hotspot.assert_called_once()
    assert manager._hotspot_attempt_count == 1


@pytest.mark.asyncio
async def test_monitor_loop_clears_credentials_after_max_hotspot_attempts(manager, mocker, tmp_path):
    manager._config_path = tmp_path / "config.json"
    call_count = 0
    async def mock_connect():
        nonlocal call_count
        call_count += 1
        if call_count >= MAX_HOTSPOT_ATTEMPTS:
            manager._running = False
        return False
    mocker.patch.object(manager, "_check_configured_available", new=AsyncMock(return_value=True))
    mocker.patch.object(manager, "_connect_to_configured", side_effect=mock_connect)
    mocker.patch.object(manager, "disable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch("raceflag.wifi_manager.asyncio.sleep", new=AsyncMock())
    manager._running = True
    manager._hotspot_active = True
    await manager._monitor_loop()
    assert manager._config.wifi_ssid == ""
    assert manager._config.wifi_password == ""
    assert manager._hotspot_attempt_count == 0


@pytest.mark.asyncio
async def test_monitor_loop_resets_attempt_count_on_successful_reconnect(manager, mocker):
    async def mock_connect():
        manager._running = False
        return True
    mocker.patch.object(manager, "_check_configured_available", new=AsyncMock(return_value=True))
    mocker.patch.object(manager, "_connect_to_configured", side_effect=mock_connect)
    mocker.patch.object(manager, "disable_hotspot", new=AsyncMock())
    mocker.patch.object(manager, "enable_hotspot", new=AsyncMock())
    mocker.patch("raceflag.wifi_manager.asyncio.sleep", new=AsyncMock())
    manager._running = True
    manager._hotspot_active = True
    manager._hotspot_attempt_count = 2
    await manager._monitor_loop()
    assert manager._hotspot_attempt_count == 0
