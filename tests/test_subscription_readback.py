"""Receiver readback must survive missing transmitter discovery."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

from custom_components.dante.coordinator import DanteDataUpdateCoordinator
from custom_components.dante.select import DanteSubscriptionSelect


@pytest.fixture
def setup_select(tmp_path):
    def create(subscriptions, rx_name="Dante 1"):
        hass = HomeAssistant(str(tmp_path))
        frame.async_setup(hass)
        coordinator = DanteDataUpdateCoordinator(hass)
        coordinator.data = {"Amp": {"subscriptions": subscriptions}}
        entity = DanteSubscriptionSelect(coordinator, "Amp", 1, rx_name)
        return coordinator, entity

    return create


def subscription(channel="01", *, rx_name="Dante 1", source="Loggia"):
    return {
        "rx_channel_name": rx_name,
        "tx_device_name": source,
        "tx_channel_name": channel,
        "status_code": 9,
    }


def discover_transmitter(coordinator):
    coordinator._update_tx_catalog(
        "Loggia",
        "Loggia",
        {
            "tx_channels": {
                1: {"name": "01", "friendly_name": "Left"},
                2: {"name": "02", "friendly_name": "Right"},
            }
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("rx_name,channel", [("Dante 1", "01"), ("Dante 2", "02")])
async def test_saved_stereo_route_is_readable_before_tx_discovery(
    setup_select, rx_name, channel
):
    coordinator, entity = setup_select(
        [subscription(), subscription("02", rx_name="Dante 2")], rx_name
    )

    assert coordinator.get_all_tx_channels() == []
    assert entity.available
    assert entity.state == f"Loggia - {channel}"
    assert entity.options == ["None", f"Loggia - {channel}"]
    # Reading one receiver must not fabricate transmitter discovery or offer
    # its sibling's channel as an undiscovered routing target.
    assert coordinator._tx_catalog == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_channel", ["01", "Left"])
async def test_later_discovery_resolves_names_without_duplicate_options(
    setup_select, reported_channel
):
    coordinator, entity = setup_select([subscription(reported_channel)])
    assert entity.state == f"Loggia - {reported_channel}"

    discover_transmitter(coordinator)

    assert entity.state == "Loggia - 01"
    assert entity.options == ["None", "Loggia - 01", "Loggia - 02"]
    # No live TX device is needed for canonical resolution after discovery:
    # the catalog also covers a later control-poll outage.
    assert "Loggia" not in coordinator.data


@pytest.mark.asyncio
async def test_exact_channel_name_wins_over_another_channels_friendly_name(
    setup_select,
):
    coordinator, entity = setup_select([subscription("Left")])
    coordinator._update_tx_catalog(
        "Loggia",
        "Loggia",
        {
            "tx_channels": {
                1: {"name": "01", "friendly_name": "Left"},
                2: {"name": "Left", "friendly_name": "Aux"},
            }
        },
    )

    assert entity.state == "Loggia - Left"
    assert entity.options.count("Loggia - Left") == 1


@pytest.mark.asyncio
async def test_fallback_tracks_changed_and_removed_receiver_subscriptions(setup_select):
    coordinator, entity = setup_select([subscription()])
    assert entity.state == "Loggia - 01"

    coordinator.data["Amp"]["subscriptions"] = [subscription(source="Office")]
    assert entity.state == "Office - 01"
    assert entity.options == ["None", "Office - 01"]

    coordinator.data["Amp"]["subscriptions"] = []
    assert entity.state == "None"
    assert entity.options == ["None"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        subscription(source=None),
        subscription(source=""),
        subscription(channel=None),
        subscription(channel=""),
        subscription(rx_name="Dante 2"),
    ],
)
async def test_incomplete_or_other_channel_routes_are_not_offered(setup_select, record):
    _, entity = setup_select([record])

    assert entity.state == "None"
    assert entity.options == ["None"]


@pytest.mark.asyncio
async def test_missing_receiver_is_still_unavailable(setup_select):
    coordinator, entity = setup_select([subscription()])
    assert entity.available

    coordinator.data = {}

    assert not entity.available
    assert entity.options == ["None"]


@pytest.mark.asyncio
async def test_pending_and_aes67_selections_keep_precedence(setup_select, monkeypatch):
    coordinator, entity = setup_select([subscription()])
    aes67_option = "[AES67] Stream - Left"
    monkeypatch.setattr(coordinator, "get_all_aes67_sources", lambda: [aes67_option])
    coordinator._aes67_selections[("Amp", 1)] = aes67_option

    assert entity.state == aes67_option
    assert entity.options == ["None", aes67_option, "Loggia - 01"]

    entity._pending_option = "None"
    assert entity.state == "None"

