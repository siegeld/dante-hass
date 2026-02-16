"""The Dante Audio Network integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, LOGGER, PLATFORMS
from .coordinator import DanteDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dante from a config entry."""
    coordinator = DanteDataUpdateCoordinator(hass)
    await coordinator.async_start_browser()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: DanteDataUpdateCoordinator = entry.runtime_data
    await coordinator.async_stop_browser()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _get_coordinator(hass: HomeAssistant) -> DanteDataUpdateCoordinator | None:
    """Get the coordinator from the first config entry."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if entries and hasattr(entries[0], "runtime_data"):
        return entries[0].runtime_data
    return None


def _register_services(hass: HomeAssistant) -> None:
    """Register Dante services."""

    async def handle_add_subscription(call: ServiceCall) -> None:
        """Handle add_subscription service call."""
        rx_device_name = call.data["rx_device"]
        rx_channel_num = call.data["rx_channel"]
        tx_device_name = call.data["tx_device"]
        tx_channel_num = call.data["tx_channel"]

        coordinator = _get_coordinator(hass)
        if not coordinator:
            LOGGER.error("No Dante coordinator available")
            return

        rx_device = coordinator.get_device(rx_device_name)
        tx_device = coordinator.get_device(tx_device_name)

        if not rx_device or not tx_device:
            LOGGER.error(
                "Device not found: rx=%s tx=%s", rx_device_name, tx_device_name
            )
            return

        rx_ch = rx_device.rx_channels.get(rx_channel_num)
        tx_ch = tx_device.tx_channels.get(tx_channel_num)

        if not rx_ch or not tx_ch:
            LOGGER.error(
                "Channel not found: rx=%s tx=%s", rx_channel_num, tx_channel_num
            )
            return

        try:
            await rx_device.add_subscription(rx_ch, tx_ch, tx_device)
            await coordinator.async_request_refresh()
        except Exception as err:
            LOGGER.error("Failed to add subscription: %s", err)

    async def handle_remove_subscription(call: ServiceCall) -> None:
        """Handle remove_subscription service call."""
        rx_device_name = call.data["rx_device"]
        rx_channel_num = call.data["rx_channel"]

        coordinator = _get_coordinator(hass)
        if not coordinator:
            LOGGER.error("No Dante coordinator available")
            return

        rx_device = coordinator.get_device(rx_device_name)
        if not rx_device:
            LOGGER.error("Device not found: %s", rx_device_name)
            return

        rx_ch = rx_device.rx_channels.get(rx_channel_num)
        if not rx_ch:
            LOGGER.error("Channel not found: %s", rx_channel_num)
            return

        try:
            await rx_device.remove_subscription(rx_ch)
            await coordinator.async_request_refresh()
        except Exception as err:
            LOGGER.error("Failed to remove subscription: %s", err)

    async def handle_identify(call: ServiceCall) -> None:
        """Handle identify service call."""
        device_name = call.data["device_name"]

        coordinator = _get_coordinator(hass)
        if not coordinator:
            LOGGER.error("No Dante coordinator available")
            return

        device = coordinator.get_device(device_name)
        if not device:
            LOGGER.error("Device not found: %s", device_name)
            return

        try:
            await device.identify()
        except Exception as err:
            LOGGER.error("Failed to identify device: %s", err)

    if not hass.services.has_service(DOMAIN, "add_subscription"):
        hass.services.async_register(
            DOMAIN,
            "add_subscription",
            handle_add_subscription,
            schema=vol.Schema(
                {
                    vol.Required("rx_device"): cv.string,
                    vol.Required("rx_channel"): vol.Coerce(int),
                    vol.Required("tx_device"): cv.string,
                    vol.Required("tx_channel"): vol.Coerce(int),
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, "remove_subscription"):
        hass.services.async_register(
            DOMAIN,
            "remove_subscription",
            handle_remove_subscription,
            schema=vol.Schema(
                {
                    vol.Required("rx_device"): cv.string,
                    vol.Required("rx_channel"): vol.Coerce(int),
                }
            ),
        )

    if not hass.services.has_service(DOMAIN, "identify"):
        hass.services.async_register(
            DOMAIN,
            "identify",
            handle_identify,
            schema=vol.Schema(
                {
                    vol.Required("device_name"): cv.string,
                }
            ),
        )
