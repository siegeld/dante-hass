"""DataUpdateCoordinator for Dante Audio Network."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

from .const import DOMAIN, LOGGER, MDNS_TIMEOUT, SCAN_INTERVAL
from .netaudio.const import SERVICE_CMC, SERVICES
from .netaudio.device import DanteDevice


class DanteDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage Dante device discovery and data."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self._devices: dict = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the Dante network."""
        try:
            aiozc = await zeroconf.async_get_async_instance(self.hass)

            # Browse for services
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

            # Resolve services and build device objects
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
                            "device": DanteDevice(server_name=server_name),
                            "services": {},
                        }

                    device = device_hosts[server_name]["device"]
                    service_data = {
                        "type": service_type,
                        "port": info.port,
                        "properties": props,
                    }
                    device_hosts[server_name]["services"][name] = service_data
                    device.services[name] = service_data

                    if not device.ipv4:
                        device.ipv4 = ipv4
                    if "id" in props and SERVICE_CMC in service_type:
                        device.mac_address = props["id"]
                    if "model" in props:
                        device.model_id = props["model"]
                    if "rate" in props:
                        device.sample_rate = int(props["rate"])
                    if "latency_ns" in props:
                        device.latency = int(props["latency_ns"])
                    if (
                        "router_info" in props
                        and props["router_info"] == '"Dante Via"'
                    ):
                        device.software = "Dante Via"

                except Exception as err:
                    LOGGER.debug("Error resolving %s: %s", name, err)

            # Get controls for each device and build result
            result: dict[str, Any] = {}

            for server_name, host_data in device_hosts.items():
                device = host_data["device"]

                try:
                    await self.hass.async_add_executor_job(
                        lambda d=device: asyncio.run(d.get_controls())
                    )
                except Exception as err:
                    LOGGER.warning(
                        "Failed to get controls for %s: %s",
                        device.name or server_name,
                        err,
                    )

                dev_name = device.name or server_name

                dev_data: dict[str, Any] = {
                    "server_name": server_name,
                    "name": dev_name,
                    "ipv4": str(device.ipv4) if device.ipv4 else None,
                    "mac_address": getattr(device, "mac_address", None),
                    "manufacturer": getattr(device, "manufacturer", None),
                    "model": getattr(device, "model", None),
                    "model_id": getattr(device, "model_id", None),
                    "software": getattr(device, "software", None),
                    "sample_rate": getattr(device, "sample_rate", None),
                    "latency": getattr(device, "latency", None),
                    "rx_count": getattr(device, "rx_count", 0) or 0,
                    "tx_count": getattr(device, "tx_count", 0) or 0,
                    "rx_channels": {},
                    "tx_channels": {},
                    "subscriptions": [],
                }

                if device.rx_channels:
                    for num, ch in device.rx_channels.items():
                        dev_data["rx_channels"][num] = {
                            "name": ch.name,
                            "number": ch.number,
                        }

                if device.tx_channels:
                    for num, ch in device.tx_channels.items():
                        dev_data["tx_channels"][num] = {
                            "name": ch.name,
                            "number": ch.number,
                        }

                if device.subscriptions:
                    for sub in device.subscriptions:
                        dev_data["subscriptions"].append(
                            {
                                "rx_channel_name": getattr(
                                    sub, "rx_channel_name", None
                                ),
                                "tx_channel_name": getattr(
                                    sub, "tx_channel_name", None
                                ),
                                "tx_device_name": getattr(
                                    sub, "tx_device_name", None
                                ),
                                "status_code": getattr(sub, "status_code", None),
                            }
                        )

                result[dev_name] = dev_data
                self._devices[dev_name] = device

            return result

        except Exception as err:
            raise UpdateFailed(
                f"Error communicating with Dante network: {err}"
            ) from err

    def get_device(self, device_name: str):
        """Get a live DanteDevice object by name."""
        return self._devices.get(device_name)

    def get_all_tx_channels(self) -> list[str]:
        """Get all TX channels across all devices as 'DeviceName - ChannelName'."""
        options = []
        if self.data:
            for dev_name, dev_data in self.data.items():
                for _num, ch_data in dev_data.get("tx_channels", {}).items():
                    options.append(f"{dev_name} - {ch_data['name']}")
        return sorted(options)
