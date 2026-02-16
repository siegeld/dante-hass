"""Button platform for Dante Audio Network."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LOGGER
from .coordinator import DanteDataUpdateCoordinator
from .entity import DanteEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dante button entities."""
    coordinator: DanteDataUpdateCoordinator = entry.runtime_data
    known_devices: set[str] = set()

    def _add_new_devices() -> None:
        """Add entities for any newly discovered devices."""
        if not coordinator.data:
            return
        new_entities: list[ButtonEntity] = []
        for device_name in coordinator.data:
            if device_name not in known_devices:
                known_devices.add(device_name)
                new_entities.append(
                    DanteIdentifyButton(coordinator, device_name)
                )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(lambda: _add_new_devices()))


class DanteIdentifyButton(DanteEntity, ButtonEntity):
    """Button to flash the LED on a Dante device."""

    _attr_icon = "mdi:led-on"
    _attr_name = "Identify"

    def __init__(
        self,
        coordinator: DanteDataUpdateCoordinator,
        device_name: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_name)
        self._attr_unique_id = f"{DOMAIN}_{device_name}_identify"

    async def async_press(self) -> None:
        """Press the button to identify the device."""
        device = self.coordinator.get_device(self._device_name)
        if not device:
            LOGGER.error("Device not found: %s", self._device_name)
            return

        try:
            await device.identify()
        except Exception as err:
            LOGGER.error(
                "Failed to identify device %s: %s", self._device_name, err
            )
