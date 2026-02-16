"""Select platform for Dante Audio Network."""
from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    ENCODINGS,
    ENCODING_LABELS,
    LOGGER,
    SAMPLE_RATES,
    SAMPLE_RATE_LABELS,
    SUBSCRIPTION_NONE,
)
from .coordinator import DanteDataUpdateCoordinator
from .entity import DanteEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dante select entities."""
    coordinator: DanteDataUpdateCoordinator = entry.runtime_data
    known_devices: set[str] = set()

    def _add_new_devices() -> None:
        """Add entities for any newly discovered devices."""
        if not coordinator.data:
            return
        new_entities: list[SelectEntity] = []
        for device_name, dev_data in coordinator.data.items():
            if device_name not in known_devices:
                known_devices.add(device_name)
                new_entities.append(
                    DanteSampleRateSelect(coordinator, device_name)
                )
                new_entities.append(
                    DanteEncodingSelect(coordinator, device_name)
                )
                for ch_num, ch_data in dev_data.get("rx_channels", {}).items():
                    new_entities.append(
                        DanteSubscriptionSelect(
                            coordinator, device_name, ch_num, ch_data["name"]
                        )
                    )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(lambda: _add_new_devices()))


class DanteSampleRateSelect(DanteEntity, SelectEntity):
    """Select entity for Dante device sample rate."""

    _attr_icon = "mdi:sine-wave"
    _attr_name = "Sample Rate"

    def __init__(
        self,
        coordinator: DanteDataUpdateCoordinator,
        device_name: str,
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator, device_name)
        self._attr_unique_id = f"{DOMAIN}_{device_name}_sample_rate_select"
        self._attr_options = [SAMPLE_RATE_LABELS[r] for r in SAMPLE_RATES]

    @property
    def current_option(self) -> str | None:
        """Return the current sample rate."""
        data = self.device_data
        if data and data.get("sample_rate"):
            return SAMPLE_RATE_LABELS.get(data["sample_rate"])
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the sample rate."""
        rate_map = {v: k for k, v in SAMPLE_RATE_LABELS.items()}
        rate = rate_map.get(option)
        if rate is None:
            return

        device = self.coordinator.get_device(self._device_name)
        if not device:
            return

        try:
            await device.set_sample_rate(rate)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            LOGGER.error(
                "Failed to set sample rate on %s: %s", self._device_name, err
            )


class DanteEncodingSelect(DanteEntity, SelectEntity):
    """Select entity for Dante device encoding."""

    _attr_icon = "mdi:waveform"
    _attr_name = "Encoding"

    def __init__(
        self,
        coordinator: DanteDataUpdateCoordinator,
        device_name: str,
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator, device_name)
        self._attr_unique_id = f"{DOMAIN}_{device_name}_encoding_select"
        self._attr_options = [ENCODING_LABELS[e] for e in ENCODINGS]

    @property
    def current_option(self) -> str | None:
        """Return the current encoding."""
        # Encoding is not directly exposed by the library as a device property,
        # so we return None until we can read it back from the device.
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the encoding."""
        enc_map = {v: k for k, v in ENCODING_LABELS.items()}
        encoding = enc_map.get(option)
        if encoding is None:
            return

        device = self.coordinator.get_device(self._device_name)
        if not device:
            return

        try:
            await device.set_encoding(encoding)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            LOGGER.error(
                "Failed to set encoding on %s: %s", self._device_name, err
            )


class DanteSubscriptionSelect(DanteEntity, SelectEntity):
    """Select entity for routing a TX source to an RX channel."""

    _attr_icon = "mdi:audio-input-stereo-minijack"

    def __init__(
        self,
        coordinator: DanteDataUpdateCoordinator,
        device_name: str,
        rx_channel_num: int,
        rx_channel_name: str,
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator, device_name)
        self._rx_channel_num = rx_channel_num
        self._rx_channel_name = rx_channel_name
        self._attr_unique_id = (
            f"{DOMAIN}_{device_name}_rx_{rx_channel_num}_subscription"
        )
        self._attr_name = f"RX {rx_channel_num} ({rx_channel_name})"

    @property
    def options(self) -> list[str]:
        """Return all available TX channels and AES67 streams as options."""
        tx_options = self.coordinator.get_all_tx_channels()
        aes67_options = self.coordinator.get_all_aes67_sources()
        return [SUBSCRIPTION_NONE] + tx_options + aes67_options

    @property
    def current_option(self) -> str | None:
        """Return the current subscription source for this RX channel."""
        # Check for local AES67 selection first
        key = (self._device_name, self._rx_channel_num)
        aes67_sel = self.coordinator._aes67_selections.get(key)
        if aes67_sel:
            return aes67_sel

        data = self.device_data
        if not data:
            return SUBSCRIPTION_NONE

        for sub in data.get("subscriptions", []):
            if sub.get("rx_channel_name") == self._rx_channel_name:
                tx_dev = sub.get("tx_device_name")
                tx_ch = sub.get("tx_channel_name")
                if tx_dev and tx_ch:
                    return f"{tx_dev} - {tx_ch}"
        return SUBSCRIPTION_NONE

    async def async_select_option(self, option: str) -> None:
        """Set the subscription for this RX channel."""
        device = self.coordinator.get_device(self._device_name)
        if not device:
            return

        rx_ch = device.rx_channels.get(self._rx_channel_num)
        if not rx_ch:
            LOGGER.error(
                "RX channel %s not found on %s",
                self._rx_channel_num,
                self._device_name,
            )
            return

        key = (self._device_name, self._rx_channel_num)

        if option == SUBSCRIPTION_NONE:
            self.coordinator._aes67_selections.pop(key, None)
            try:
                await device.remove_subscription(rx_ch)
                await self.coordinator.async_request_refresh()
            except Exception as err:
                LOGGER.error(
                    "Failed to remove subscription on %s ch %s: %s",
                    self._device_name,
                    self._rx_channel_num,
                    err,
                )
            return

        if option.startswith("[AES67] "):
            result = self.coordinator.get_aes67_stream_info(option)
            if not result:
                LOGGER.error("AES67 stream not found for option: %s", option)
                return

            stream_info, flow_channel = result
            device_ip = self.device_data.get("ipv4") if self.device_data else None
            if not device_ip:
                LOGGER.error("No IP for device %s", self._device_name)
                return

            try:
                success = await self.hass.async_add_executor_job(
                    self.coordinator._send_aes67_subscribe,
                    device_ip,
                    self._rx_channel_num,
                    flow_channel,
                    stream_info,
                )
                if success:
                    self.coordinator._aes67_selections[key] = option
                    self.async_write_ha_state()
                    LOGGER.warning(
                        "AES67 subscribed %s ch %d -> %s (flow ch %d)",
                        self._device_name,
                        self._rx_channel_num,
                        option,
                        flow_channel,
                    )
                else:
                    LOGGER.error(
                        "AES67 subscribe failed for %s ch %d -> %s",
                        self._device_name,
                        self._rx_channel_num,
                        option,
                    )
            except Exception as err:
                LOGGER.error(
                    "AES67 subscribe error for %s ch %d: %s",
                    self._device_name,
                    self._rx_channel_num,
                    err,
                )
            return

        # Clear any AES67 override when switching to a Dante source
        self.coordinator._aes67_selections.pop(key, None)

        # Parse "DeviceName - ChannelName"
        if " - " not in option:
            return

        tx_device_name, tx_channel_name = option.split(" - ", 1)
        tx_device = self.coordinator.get_device(tx_device_name)
        if not tx_device:
            LOGGER.error("TX device not found: %s", tx_device_name)
            return

        tx_ch = None
        if tx_device.tx_channels:
            for ch in tx_device.tx_channels.values():
                if ch.name == tx_channel_name:
                    tx_ch = ch
                    break

        if not tx_ch:
            LOGGER.error(
                "TX channel %s not found on %s", tx_channel_name, tx_device_name
            )
            return

        try:
            await device.add_subscription(rx_ch, tx_ch, tx_device)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            LOGGER.error(
                "Failed to add subscription on %s ch %s: %s",
                self._device_name,
                self._rx_channel_num,
                err,
            )
