"""Channel advertisements must not become unreachable Dante devices."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

from custom_components.dante import coordinator as coordinator_module
from custom_components.dante.netaudio.const import SERVICE_ARC, SERVICE_CHAN


@pytest.fixture
def setup_coordinator(monkeypatch, tmp_path):
    def create():
        hass = HomeAssistant(str(tmp_path))
        frame.async_setup(hass)
        coordinator = coordinator_module.DanteDataUpdateCoordinator(hass)
        coordinator._browser_ready.set()
        monkeypatch.setattr(
            coordinator_module.zeroconf,
            "async_get_async_instance",
            AsyncMock(return_value=SimpleNamespace(zeroconf=object())),
        )
        monkeypatch.setattr(coordinator, "_find_bind_ip", lambda _: None)
        monkeypatch.setattr(coordinator._store, "async_save", AsyncMock())
        return coordinator

    return create


def resolved_info():
    return SimpleNamespace(
        async_request=AsyncMock(return_value=True),
        parsed_addresses=Mock(return_value=["192.0.2.10"]),
        port=4440,
        properties={},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label", ["01@Kitchen", "32@Mixer", "Left@Kitchen", "Right@Office"]
)
async def test_channel_aliases_never_resolve_or_enter_device_registry(
    monkeypatch, setup_coordinator, label
):
    coordinator = setup_coordinator()
    coordinator._discovered_services[f"{label}.{SERVICE_CHAN}"] = SERVICE_CHAN
    resolver = Mock(side_effect=lambda *_: resolved_info())
    query = AsyncMock()
    monkeypatch.setattr(coordinator_module, "AsyncServiceInfo", resolver)
    monkeypatch.setattr(coordinator_module.DanteDevice, "get_controls", query)

    for _ in range(coordinator_module.DEVICE_MISS_LIMIT + 2):
        assert await coordinator._async_update_data() == {}

    resolver.assert_not_called()
    query.assert_not_awaited()
    assert coordinator._known_devices == {}
    assert coordinator._miss_count == {}
    coordinator._store.async_save.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label", ["Kitchen", "Blaze-Kitchen-Wine-Room", "DA11ARC-Study"]
)
async def test_real_devices_still_resolve_poll_and_persist(
    monkeypatch, setup_coordinator, label
):
    coordinator = setup_coordinator()
    service_name = f"{label}.{SERVICE_ARC}"
    coordinator._discovered_services[service_name] = SERVICE_ARC
    coordinator._discovered_services[f"Left@Kitchen.{SERVICE_CHAN}"] = SERVICE_CHAN
    coordinator._discovered_services[f"Right@Office.{SERVICE_CHAN}"] = SERVICE_CHAN
    info = resolved_info()
    resolver = Mock(return_value=info)
    queried = []

    async def controls(device):
        queried.append(str(device.ipv4))
        device.name = label

    monkeypatch.setattr(coordinator_module, "AsyncServiceInfo", resolver)
    monkeypatch.setattr(coordinator_module.DanteDevice, "get_controls", controls)

    assert label in await coordinator._async_update_data()
    resolver.assert_called_once_with(SERVICE_ARC, service_name)
    assert queried == ["192.0.2.10"]
    assert set(coordinator._known_devices) == {label}
    stored = coordinator._store.async_save.call_args.args[0]
    assert set(stored["devices"]) == {label}


@pytest.mark.asyncio
async def test_at_sign_after_instance_label_does_not_filter_device(
    monkeypatch, setup_coordinator
):
    coordinator = setup_coordinator()
    service_name = f"Kitchen.{SERVICE_ARC}extra@domain"
    coordinator._discovered_services[service_name] = SERVICE_ARC
    resolver = Mock(return_value=resolved_info())
    monkeypatch.setattr(coordinator_module, "AsyncServiceInfo", resolver)
    monkeypatch.setattr(coordinator_module.DanteDevice, "get_controls", AsyncMock())

    await coordinator._async_update_data()

    resolver.assert_called_once_with(SERVICE_ARC, service_name)
    assert "Kitchen" in coordinator._known_devices


@pytest.mark.asyncio
async def test_stored_channel_aliases_are_dropped_on_load(setup_coordinator):
    coordinator = setup_coordinator()
    record = {"ipv4": "192.0.2.10", "services": {}, "props": {}}
    coordinator._store.async_load = AsyncMock(
        return_value={
            "devices": {"Left@Kitchen": record, "01@Mixer": record, "Kitchen": record}
        }
    )

    await coordinator.async_load_known_devices()

    assert set(coordinator._known_devices) == {"Kitchen"}
    await coordinator._async_save_known_devices()
    stored = coordinator._store.async_save.call_args.args[0]
    assert set(stored["devices"]) == {"Kitchen"}
