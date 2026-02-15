"""Config flow for Dante Audio Network."""
from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.data_entry_flow import FlowResult

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

from .const import DOMAIN, LOGGER, MDNS_TIMEOUT
from .netaudio.const import SERVICES


class DanteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dante Audio Network."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_devices: dict[str, dict] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step — scan and confirm."""
        errors: dict[str, str] = {}

        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title="Dante Audio Network",
                data={},
            )

        try:
            aiozc = await zeroconf.async_get_async_instance(self.hass)
            found_services: list[tuple[str, str]] = []

            def on_state_change(
                zeroconf: object,
                service_type: str,
                name: str,
                state_change: ServiceStateChange,
            ) -> None:
                if state_change is ServiceStateChange.Added:
                    found_services.append((service_type, name))

            browser = AsyncServiceBrowser(
                aiozc.zeroconf,
                SERVICES,
                handlers=[on_state_change],
            )

            await asyncio.sleep(MDNS_TIMEOUT)
            await browser.async_cancel()

            LOGGER.warning("Dante: browse found %d services", len(found_services))

            # Resolve each service
            device_hosts: dict[str, dict] = {}
            for service_type, name in found_services:
                try:
                    info = AsyncServiceInfo(service_type, name)
                    if not await info.async_request(aiozc.zeroconf, 3000):
                        continue

                    addresses = info.parsed_addresses()
                    if not addresses:
                        continue

                    ipv4 = addresses[0]
                    props = {}
                    for k, v in info.properties.items():
                        k = k.decode("utf-8") if isinstance(k, bytes) else k
                        v = v.decode("utf-8") if isinstance(v, bytes) else v
                        props[k] = v

                    server_name = info.server or name.split(".")[0]

                    if server_name not in device_hosts:
                        device_hosts[server_name] = {
                            "ipv4": ipv4,
                            "model": "Unknown",
                        }

                    if "model" in props:
                        device_hosts[server_name]["model"] = props["model"]

                except Exception as err:
                    LOGGER.debug("Error resolving %s: %s", name, err)

            LOGGER.warning("Dante: found %d devices", len(device_hosts))

            self._discovered_devices = {
                name: {
                    "name": name,
                    "model": data["model"],
                    "ipv4": data["ipv4"],
                }
                for name, data in device_hosts.items()
            }
        except Exception as err:
            LOGGER.error("Failed to discover Dante devices: %s", err)
            errors["base"] = "cannot_connect"

        if not self._discovered_devices and "base" not in errors:
            errors["base"] = "no_devices_found"

        device_list = "\n".join(
            f"  {d['name']} ({d['model']}) - {d['ipv4']}"
            for d in self._discovered_devices.values()
        )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "device_count": str(len(self._discovered_devices)),
                "device_list": device_list or "No devices found",
            },
        )
