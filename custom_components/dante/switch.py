"""Switch platform for Dante Audio Network."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
    """Set up Dante switch entities."""
    coordinator: DanteDataUpdateCoordinator = entry.runtime_data

    entities: list[SwitchEntity] = []
    if coordinator.data:
        for device_name in coordinator.data:
            entities.append(DanteAES67Switch(coordinator, device_name))

    async_add_entities(entities)


class DanteAES67Switch(DanteEntity, SwitchEntity):
    """Switch entity for AES67 mode on a Dante device.

    Note: The netaudio library (v0.0.10) does not expose a direct AES67 toggle.
    This entity is a placeholder for future library support. Currently it tracks
    a local state and logs a warning that the command is unsupported.
    """

    _attr_icon = "mdi:audio-input-xlr"
    _attr_name = "AES67 Mode"

    def __init__(
        self,
        coordinator: DanteDataUpdateCoordinator,
        device_name: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_name)
        self._attr_unique_id = f"{DOMAIN}_{device_name}_aes67"
        self._is_on = False

    @property
    def is_on(self) -> bool:
        """Return True if AES67 mode is on."""
        return self._is_on

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on AES67 mode."""
        device = self.coordinator.get_device(self._device_name)
        if not device:
            return

        if hasattr(device, "set_aes67") and callable(device.set_aes67):
            try:
                await device.set_aes67(True)
                self._is_on = True
                self.async_write_ha_state()
                await self.coordinator.async_request_refresh()
                return
            except Exception as err:
                LOGGER.error(
                    "Failed to enable AES67 on %s: %s", self._device_name, err
                )
                return

        LOGGER.warning(
            "AES67 toggle not supported by netaudio library for %s",
            self._device_name,
        )
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off AES67 mode."""
        device = self.coordinator.get_device(self._device_name)
        if not device:
            return

        if hasattr(device, "set_aes67") and callable(device.set_aes67):
            try:
                await device.set_aes67(False)
                self._is_on = False
                self.async_write_ha_state()
                await self.coordinator.async_request_refresh()
                return
            except Exception as err:
                LOGGER.error(
                    "Failed to disable AES67 on %s: %s", self._device_name, err
                )
                return

        LOGGER.warning(
            "AES67 toggle not supported by netaudio library for %s",
            self._device_name,
        )
        self._is_on = False
        self.async_write_ha_state()
