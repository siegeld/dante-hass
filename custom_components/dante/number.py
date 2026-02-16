"""Number platform for Dante Audio Network."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    AVIO_INPUT_MODELS,
    AVIO_OUTPUT_MODELS,
    DOMAIN,
    GAIN_LABELS_INPUT,
    GAIN_LABELS_OUTPUT,
    LOGGER,
)
from .coordinator import DanteDataUpdateCoordinator
from .entity import DanteEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dante number entities."""
    coordinator: DanteDataUpdateCoordinator = entry.runtime_data
    known_devices: set[str] = set()

    def _add_new_devices() -> None:
        """Add entities for any newly discovered devices."""
        if not coordinator.data:
            return
        new_entities: list[NumberEntity] = []
        for device_name, dev_data in coordinator.data.items():
            if device_name not in known_devices:
                known_devices.add(device_name)
                new_entities.append(
                    DanteLatencyNumber(coordinator, device_name)
                )
                model_id = dev_data.get("model_id")
                if model_id in AVIO_INPUT_MODELS:
                    for ch_num, ch_data in dev_data.get("tx_channels", {}).items():
                        new_entities.append(
                            DanteGainNumber(
                                coordinator,
                                device_name,
                                ch_num,
                                ch_data["name"],
                                "input",
                            )
                        )
                elif model_id in AVIO_OUTPUT_MODELS:
                    for ch_num, ch_data in dev_data.get("rx_channels", {}).items():
                        new_entities.append(
                            DanteGainNumber(
                                coordinator,
                                device_name,
                                ch_num,
                                ch_data["name"],
                                "output",
                            )
                        )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(lambda: _add_new_devices()))


class DanteLatencyNumber(DanteEntity, NumberEntity):
    """Number entity for Dante device latency (ms)."""

    _attr_icon = "mdi:timer-outline"
    _attr_name = "Latency"
    _attr_native_min_value = 0.15
    _attr_native_max_value = 10.0
    _attr_native_step = 0.05
    _attr_native_unit_of_measurement = "ms"
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: DanteDataUpdateCoordinator,
        device_name: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, device_name)
        self._attr_unique_id = f"{DOMAIN}_{device_name}_latency"

    @property
    def native_value(self) -> float | None:
        """Return the current latency in ms."""
        data = self.device_data
        if data and data.get("latency") is not None:
            # Library stores latency in nanoseconds; convert to ms
            return data["latency"] / 1_000_000
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the latency (value is in ms)."""
        device = self.coordinator.get_device(self._device_name)
        if not device:
            return

        try:
            await device.set_latency(value)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            LOGGER.error(
                "Failed to set latency on %s: %s", self._device_name, err
            )


class DanteGainNumber(DanteEntity, NumberEntity):
    """Number entity for AVIO device gain level (1-5)."""

    _attr_icon = "mdi:knob"
    _attr_native_min_value = 1
    _attr_native_max_value = 5
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: DanteDataUpdateCoordinator,
        device_name: str,
        channel_num: int,
        channel_name: str,
        device_type: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, device_name)
        self._channel_num = channel_num
        self._channel_name = channel_name
        self._device_type = device_type
        self._attr_unique_id = (
            f"{DOMAIN}_{device_name}_gain_{device_type}_{channel_num}"
        )
        labels = (
            GAIN_LABELS_INPUT if device_type == "input" else GAIN_LABELS_OUTPUT
        )
        label_str = " / ".join(f"{k}={v}" for k, v in labels.items())
        self._attr_name = f"Gain Ch {channel_num} ({channel_name})"
        self._attr_entity_registry_visible_default = True

    @property
    def native_value(self) -> float | None:
        """Return the current gain level (not directly readable from library)."""
        # Gain level is write-only in the netaudio library
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the gain level (1-5)."""
        device = self.coordinator.get_device(self._device_name)
        if not device:
            return

        try:
            await device.set_gain_level(
                self._channel_num, int(value), self._device_type
            )
            await self.coordinator.async_request_refresh()
        except Exception as err:
            LOGGER.error(
                "Failed to set gain on %s ch %s: %s",
                self._device_name,
                self._channel_num,
                err,
            )
