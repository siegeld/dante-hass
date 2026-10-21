"""Missing ARC discovery must recover without masquerading as a UDP timeout."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from zeroconf import IPVersion, ServiceStateChange

from custom_components.dante import coordinator as coordinator_module
from custom_components.dante import discovery
from custom_components.dante.netaudio.const import SERVICE_ARC, SERVICE_CMC


def incomplete(name="Amp"):
    return {
        "ipv4": "192.0.2.10",
        "services": {
            f"{name}.{SERVICE_CMC}": {
                "type": SERVICE_CMC,
                "port": 8800,
                "properties": {},
            }
        },
        "props": {},
    }


def resolved_info(*, port=14160, addresses=None, success=True):
    return SimpleNamespace(
        async_request=AsyncMock(return_value=success),
        parsed_addresses=Mock(
            return_value=(["192.0.2.20"] if addresses is None else addresses)
        ),
        port=port,
        properties={b"router_info": b"test", b"empty": None},
    )


@pytest.mark.asyncio
async def test_missing_browse_event_recovers_advertised_port_and_address(monkeypatch):
    info = resolved_info()
    factory = Mock(return_value=info)
    monkeypatch.setattr(discovery, "AsyncServiceInfo", factory)
    devices = {"Amp": incomplete()}
    discovered = {}
    zc = object()
    recovery = discovery.RoutingDiscoveryRecovery()

    assert await recovery.async_recover(zc, devices, discovered)
    factory.assert_called_once_with(SERVICE_ARC, f"Amp.{SERVICE_ARC}")
    info.async_request.assert_awaited_once_with(zc, 3000)
    info.parsed_addresses.assert_called_once_with(IPVersion.V4Only)
    assert devices["Amp"]["ipv4"] == "192.0.2.20"
    assert devices["Amp"]["services"][f"Amp.{SERVICE_ARC}"]["port"] == 14160
    assert devices["Amp"]["props"] == {"router_info": "test", "empty": None}
    assert discovered == {f"Amp.{SERVICE_ARC}": SERVICE_ARC}
    assert discovery.has_routing_service(devices["Amp"])
    assert not await recovery.async_recover(zc, devices, discovered)
    assert factory.call_count == 1


@pytest.mark.asyncio
async def test_failures_are_bounded_rate_limited_and_fair(monkeypatch):
    now = 100.0
    monkeypatch.setattr(discovery.time, "monotonic", lambda: now)
    factory = Mock(side_effect=lambda *_: resolved_info(success=False))
    monkeypatch.setattr(discovery, "AsyncServiceInfo", factory)
    recovery = discovery.RoutingDiscoveryRecovery()
    devices = {f"Amp{i}": incomplete(f"Amp{i}") for i in range(12)}
    for expected in [4, 8, 12]:
        assert not await recovery.async_recover(object(), devices, {})
        assert factory.call_count == expected
    now = 159.9
    assert not await recovery.async_recover(object(), devices, {})
    assert factory.call_count == 12
    now = 160.0
    assert not await recovery.async_recover(object(), devices, {})
    assert factory.call_count == 16
    assert len(devices) == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("port,addresses", [(0, ["192.0.2.20"]), (4440, [])])
async def test_incomplete_answers_are_not_installed(monkeypatch, port, addresses):
    monkeypatch.setattr(
        discovery,
        "AsyncServiceInfo",
        Mock(return_value=resolved_info(port=port, addresses=addresses)),
    )
    devices = {"Amp": incomplete()}
    assert not await discovery.RoutingDiscoveryRecovery().async_recover(
        object(), devices, {}
    )
    assert devices["Amp"]["ipv4"] == "192.0.2.10"
    assert not discovery.has_routing_service(devices["Amp"])


@pytest.mark.asyncio
async def test_one_resolver_error_does_not_prevent_other_recovery(monkeypatch):
    factory = Mock(side_effect=[ValueError("bad record"), resolved_info()])
    monkeypatch.setattr(discovery, "AsyncServiceInfo", factory)
    devices = {"Bad": incomplete("Bad"), "Good": incomplete("Good")}
    assert await discovery.RoutingDiscoveryRecovery().async_recover(
        object(), devices, {}
    )
    assert not discovery.has_routing_service(devices["Bad"])
    assert discovery.has_routing_service(devices["Good"])


@pytest.mark.asyncio
async def test_cancellation_propagates(monkeypatch):
    info = resolved_info()
    info.async_request.side_effect = asyncio.CancelledError
    monkeypatch.setattr(discovery, "AsyncServiceInfo", Mock(return_value=info))
    with pytest.raises(asyncio.CancelledError):
        await discovery.RoutingDiscoveryRecovery().async_recover(
            object(), {"Amp": incomplete()}, {}
        )


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
        if hasattr(coordinator, "_store"):
            monkeypatch.setattr(coordinator._store, "async_save", AsyncMock())
        return coordinator

    return create


@pytest.mark.asyncio
async def test_missing_arc_stays_unavailable_without_consuming_misses(
    monkeypatch,
    setup_coordinator,
):
    coordinator = setup_coordinator()
    coordinator._known_devices["Amp"] = incomplete()
    coordinator._cached_data["Amp"] = {"name": "Amp"}
    coordinator._devices["Amp"] = object()
    coordinator._miss_count["Amp"] = 5
    monkeypatch.setattr(
        coordinator._routing_recovery, "async_recover", AsyncMock(return_value=False)
    )
    query = AsyncMock()
    monkeypatch.setattr(coordinator_module.DanteDevice, "get_controls", query)
    for _ in range(coordinator_module.DEVICE_MISS_LIMIT + 2):
        assert await coordinator._async_update_data() == {}
    query.assert_not_awaited()
    assert "Amp" in coordinator._known_devices
    assert "Amp" not in coordinator._miss_count
    assert coordinator.get_device("Amp") is None
    assert "Amp" not in coordinator._cached_data


@pytest.mark.asyncio
async def test_recovered_endpoint_is_polled_and_persisted_same_cycle(
    monkeypatch,
    setup_coordinator,
):
    coordinator = setup_coordinator()
    coordinator._known_devices["Amp"] = incomplete()
    monkeypatch.setattr(
        discovery, "AsyncServiceInfo", Mock(return_value=resolved_info())
    )

    async def controls(device, **kwargs):
        assert str(device.ipv4) == "192.0.2.20"
        assert device.get_service(SERVICE_ARC)["port"] == 14160
        device.name = "Amp"

    monkeypatch.setattr(coordinator_module.DanteDevice, "get_controls", controls)
    assert "Amp" in await coordinator._async_update_data()
    assert "Amp" not in coordinator._miss_count
    if hasattr(coordinator, "_store"):
        stored = coordinator._store.async_save.call_args.args[0]
        assert (
            stored["devices"]["Amp"]["services"][f"Amp.{SERVICE_ARC}"]["port"] == 14160
        )


@pytest.mark.asyncio
async def test_real_query_failure_still_uses_existing_miss_limit(
    monkeypatch,
    setup_coordinator,
):
    coordinator = setup_coordinator()
    known = incomplete()
    known["services"][f"Amp.{SERVICE_ARC}"] = {"type": SERVICE_ARC, "port": 4440}
    coordinator._known_devices["Amp"] = known
    query = AsyncMock()
    monkeypatch.setattr(coordinator_module.DanteDevice, "get_controls", query)
    for _ in range(coordinator_module.DEVICE_MISS_LIMIT + 1):
        await coordinator._async_update_data()
    assert query.await_count == coordinator_module.DEVICE_MISS_LIMIT + 1
    assert "Amp" not in coordinator._known_devices


@pytest.mark.asyncio
async def test_updated_service_event_can_restore_browse_tracking(setup_coordinator):
    coordinator = setup_coordinator()
    name = f"Amp.{SERVICE_ARC}"
    coordinator._on_service_state_change(SERVICE_ARC, name, ServiceStateChange.Updated)
    assert coordinator._discovered_services[name] == SERVICE_ARC
