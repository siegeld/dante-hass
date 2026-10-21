"""A restored registry must not hold setup for the mDNS browse."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

from custom_components.dante import coordinator as coordinator_module


@pytest.fixture
def setup_coordinator(monkeypatch, tmp_path):
    def create():
        hass = HomeAssistant(str(tmp_path))
        frame.async_setup(hass)
        coordinator = coordinator_module.DanteDataUpdateCoordinator(hass)
        monkeypatch.setattr(
            coordinator_module.zeroconf,
            "async_get_async_instance",
            AsyncMock(return_value=SimpleNamespace(zeroconf=object())),
        )
        monkeypatch.setattr(coordinator_module, "AsyncServiceBrowser", Mock())
        return coordinator

    return create


@pytest.mark.asyncio
async def test_restored_registry_does_not_wait_for_browse(monkeypatch, setup_coordinator):
    coordinator = setup_coordinator()
    sleep = AsyncMock()
    monkeypatch.setattr(coordinator_module.asyncio, "sleep", sleep)
    coordinator._known_devices["Amp"] = {"ipv4": "192.0.2.10", "services": {}}

    await coordinator.async_start_browser()

    sleep.assert_not_awaited()
    assert coordinator._browser_ready.is_set()


@pytest.mark.asyncio
async def test_first_install_still_waits_for_browse(monkeypatch, setup_coordinator):
    coordinator = setup_coordinator()
    sleep = AsyncMock()
    monkeypatch.setattr(coordinator_module.asyncio, "sleep", sleep)

    await coordinator.async_start_browser()

    sleep.assert_awaited_once_with(coordinator_module.MDNS_TIMEOUT * 3)
    assert coordinator._browser_ready.is_set()


@pytest.mark.asyncio
async def test_first_poll_skips_sap_and_later_polls_run_it(
    monkeypatch, setup_coordinator
):
    coordinator = setup_coordinator()
    coordinator._browser_ready.set()
    monkeypatch.setattr(coordinator._store, "async_save", AsyncMock())
    monkeypatch.setattr(coordinator, "_find_bind_ip", lambda _: "192.0.2.1")
    sap = Mock(return_value={})
    monkeypatch.setattr(coordinator, "_discover_sap_streams", sap)

    await coordinator._async_update_data()
    sap.assert_not_called()

    await coordinator._async_update_data()
    sap.assert_called_once_with("192.0.2.1")
