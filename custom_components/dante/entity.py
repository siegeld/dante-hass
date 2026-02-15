"""Base entity for Dante Audio Network."""
from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DanteDataUpdateCoordinator


class DanteEntity(CoordinatorEntity[DanteDataUpdateCoordinator]):
    """Base entity for Dante devices."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DanteDataUpdateCoordinator,
        device_name: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._device_name = device_name

    @property
    def device_data(self) -> dict | None:
        """Get the device data from coordinator."""
        if self.coordinator.data:
            return self.coordinator.data.get(self._device_name)
        return None

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info."""
        data = self.device_data
        if not data:
            return None
        return DeviceInfo(
            identifiers={(DOMAIN, data.get("mac_address") or data["server_name"])},
            name=data["name"],
            manufacturer=data.get("manufacturer"),
            model=data.get("model"),
            sw_version=data.get("software"),
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return super().available and self.device_data is not None
