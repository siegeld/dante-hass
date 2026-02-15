"""Sensor platform for Dante Audio Network."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DanteDataUpdateCoordinator
from .entity import DanteEntity


@dataclass(frozen=True, kw_only=True)
class DanteSensorEntityDescription(SensorEntityDescription):
    """Describe a Dante sensor entity."""

    value_fn: Callable[[dict[str, Any]], Any]


SENSOR_DESCRIPTIONS: tuple[DanteSensorEntityDescription, ...] = (
    DanteSensorEntityDescription(
        key="model",
        name="Model",
        icon="mdi:audio-video",
        value_fn=lambda data: data.get("model"),
    ),
    DanteSensorEntityDescription(
        key="manufacturer",
        name="Manufacturer",
        icon="mdi:factory",
        value_fn=lambda data: data.get("manufacturer"),
    ),
    DanteSensorEntityDescription(
        key="software_version",
        name="Software Version",
        icon="mdi:package-variant",
        value_fn=lambda data: data.get("software"),
    ),
    DanteSensorEntityDescription(
        key="sample_rate",
        name="Sample Rate",
        icon="mdi:sine-wave",
        native_unit_of_measurement="Hz",
        value_fn=lambda data: data.get("sample_rate"),
    ),
    DanteSensorEntityDescription(
        key="latency",
        name="Latency",
        icon="mdi:timer-outline",
        value_fn=lambda data: data.get("latency"),
    ),
    DanteSensorEntityDescription(
        key="rx_count",
        name="RX Channels",
        icon="mdi:import",
        value_fn=lambda data: data.get("rx_count"),
    ),
    DanteSensorEntityDescription(
        key="tx_count",
        name="TX Channels",
        icon="mdi:export",
        value_fn=lambda data: data.get("tx_count"),
    ),
    DanteSensorEntityDescription(
        key="ip_address",
        name="IP Address",
        icon="mdi:ip-network",
        value_fn=lambda data: data.get("ipv4"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dante sensor entities."""
    coordinator: DanteDataUpdateCoordinator = entry.runtime_data

    entities: list[DanteSensor] = []
    if coordinator.data:
        for device_name in coordinator.data:
            for desc in SENSOR_DESCRIPTIONS:
                entities.append(DanteSensor(coordinator, device_name, desc))

    async_add_entities(entities)


class DanteSensor(DanteEntity, SensorEntity):
    """Sensor entity for Dante device info."""

    entity_description: DanteSensorEntityDescription

    def __init__(
        self,
        coordinator: DanteDataUpdateCoordinator,
        device_name: str,
        description: DanteSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_name)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{device_name}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        data = self.device_data
        if data is None:
            return None
        return self.entity_description.value_fn(data)
