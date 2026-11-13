"""Device endpoints must survive a restart after changes or failed writes."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

from custom_components.dante import coordinator as coordinator_module
from custom_components.dante.netaudio.const import SERVICE_ARC, SERVICE_CMC


@pytest.fixture
def discovered_device(monkeypatch, tmp_path):
    def create():
        hass = HomeAssistant(str(tmp_path))
        frame.async_setup(hass)
        coordinator = coordinator_module.DanteDataUpdateCoordinator(hass)
        arc_name = f"Amp.{SERVICE_ARC}"
        cmc_name = f"Amp.{SERVICE_CMC}"
        coordinator._known_devices["Amp"] = {
            "ipv4": "192.0.2.10",
            "dev_name": "Amp",
            "services": {
                arc_name: {
                    "type": SERVICE_ARC,
                    "port": 4440,
                    "properties": {"model": "old"},
                },
                cmc_name: {
                    "type": SERVICE_CMC,
                    "port": 8800,
                    "properties": {"id": "test"},
                },
            },
            "props": {"model": "old", "id": "test"},
        }
        coordinator._discovered_services[arc_name] = SERVICE_ARC
        coordinator._browser_ready.set()
        info = SimpleNamespace(
            async_request=AsyncMock(return_value=True),
            parsed_addresses=lambda: ["192.0.2.10"],
            port=4440,
            properties={b"model": b"old"},
        )
        monkeypatch.setattr(
            coordinator_module.zeroconf,
            "async_get_async_instance",
            AsyncMock(return_value=SimpleNamespace(zeroconf=object())),
        )
        monkeypatch.setattr(
            coordinator_module, "AsyncServiceInfo", Mock(return_value=info)
        )
        monkeypatch.setattr(coordinator, "_find_bind_ip", lambda _: None)

        async def controls(device):
            device.name = "Amp"

        monkeypatch.setattr(coordinator_module.DanteDevice, "get_controls", controls)
        writes = []

        async def save(data):
            # Store takes a snapshot; retaining the mutable input would hide
            # a missing save if a later poll changed the same dictionary.
            writes.append(deepcopy(data))

        monkeypatch.setattr(coordinator._store, "async_save", AsyncMock(side_effect=save))
        return coordinator, info, writes

    return create


@pytest.mark.asyncio
async def test_same_ip_port_change_is_saved_and_restored(monkeypatch, discovered_device):
    coordinator, info, writes = discovered_device()
    info.port = 14160

    await coordinator._async_update_data()

    assert len(writes) == 1
    saved = writes[0]["devices"]["Amp"]
    assert saved["services"][f"Amp.{SERVICE_ARC}"]["port"] == 14160
    # Resolving ARC alone must retain the previously known CMC endpoint.
    assert saved["services"][f"Amp.{SERVICE_CMC}"]["port"] == 8800
    assert saved["dev_name"] == "Amp"
    await coordinator._async_update_data()
    assert len(writes) == 1

    restored, _, _ = discovered_device()
    restored._known_devices.clear()
    monkeypatch.setattr(restored._store, "async_load", AsyncMock(return_value=writes[0]))
    await restored.async_load_known_devices()
    assert restored._known_devices["Amp"] == saved


@pytest.mark.asyncio
async def test_changed_and_added_txt_properties_are_saved(discovered_device):
    coordinator, info, writes = discovered_device()
    info.properties = {b"model": b"new", b"flag": None}

    await coordinator._async_update_data()

    assert len(writes) == 1
    saved = writes[0]["devices"]["Amp"]
    assert saved["props"] == {"model": "new", "id": "test", "flag": None}
    assert saved["services"][f"Amp.{SERVICE_ARC}"]["properties"] == {
        "model": "new",
        "flag": None,
    }


@pytest.mark.asyncio
async def test_new_service_on_existing_device_is_saved(discovered_device):
    coordinator, info, writes = discovered_device()
    coordinator._known_devices["Amp"]["services"].pop(f"Amp.{SERVICE_CMC}")
    coordinator._discovered_services = {f"Amp.{SERVICE_CMC}": SERVICE_CMC}
    info.port = 8800
    info.properties = {b"id": b"test"}

    await coordinator._async_update_data()

    assert len(writes) == 1
    assert writes[0]["devices"]["Amp"]["services"][f"Amp.{SERVICE_CMC}"] == {
        "type": SERVICE_CMC,
        "port": 8800,
        "properties": {"id": "test"},
    }


@pytest.mark.asyncio
async def test_aggregate_property_change_is_saved(discovered_device):
    coordinator, _, writes = discovered_device()
    # A restored entry can have a current service record and older aggregate
    # metadata. Updating that metadata still needs to reach the store.
    coordinator._known_devices["Amp"]["props"]["model"] = "older"

    await coordinator._async_update_data()

    assert len(writes) == 1
    assert writes[0]["devices"]["Amp"]["props"]["model"] == "old"


@pytest.mark.asyncio
async def test_unchanged_polls_do_not_write(discovered_device):
    coordinator, _, writes = discovered_device()

    await coordinator._async_update_data()
    await coordinator._async_update_data()

    assert writes == []


@pytest.mark.asyncio
async def test_failed_save_retries_without_another_discovery_change(
    monkeypatch, discovered_device
):
    coordinator, info, writes = discovered_device()
    save = coordinator._store.async_save
    attempts = 0

    async def fail_once(data):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary store failure")
        await save(data)

    monkeypatch.setattr(coordinator._store, "async_save", AsyncMock(side_effect=fail_once))
    info.port = 14160
    assert "Amp" in await coordinator._async_update_data()
    assert coordinator._known_devices_dirty
    assert writes == []

    assert "Amp" in await coordinator._async_update_data()
    assert attempts == 2
    assert len(writes) == 1
    saved_arc = writes[0]["devices"]["Amp"]["services"][f"Amp.{SERVICE_ARC}"]
    assert saved_arc["port"] == 14160
    assert not coordinator._known_devices_dirty

    await coordinator._async_update_data()
    assert attempts == 2


@pytest.mark.asyncio
async def test_cancelled_save_preserves_retry_and_propagates(
    monkeypatch, discovered_device
):
    coordinator, _, _ = discovered_device()
    coordinator._known_devices_dirty = True
    monkeypatch.setattr(
        coordinator._store, "async_save", AsyncMock(side_effect=asyncio.CancelledError)
    )

    with pytest.raises(asyncio.CancelledError):
        await coordinator._async_save_known_devices()

    assert coordinator._known_devices_dirty


@pytest.mark.asyncio
async def test_successful_save_does_not_clear_new_dirty_mark(
    monkeypatch, discovered_device
):
    coordinator, _, _ = discovered_device()
    coordinator._known_devices_dirty = True

    async def save_with_new_change(_data):
        coordinator._known_devices_dirty = True

    monkeypatch.setattr(
        coordinator._store, "async_save", AsyncMock(side_effect=save_with_new_change)
    )

    await coordinator._async_save_known_devices()

    assert coordinator._known_devices_dirty
