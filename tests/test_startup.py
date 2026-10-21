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
