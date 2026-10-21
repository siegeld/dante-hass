"""Recover routing endpoints missing from otherwise discovered Dante devices."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from zeroconf import IPVersion, Zeroconf
from zeroconf.asyncio import AsyncServiceInfo

from .netaudio.const import SERVICE_ARC

LOGGER = logging.getLogger(__name__)
ARC_RECOVERY_INTERVAL = 60.0
ARC_RECOVERY_TIMEOUT_MS = 3000
ARC_RECOVERY_BATCH_SIZE = 4
# A record that stays ARC-less this long is dropped from the known-device
# registry. Without a cap, a decommissioned or renamed device whose record
# lacks ARC would be retried every minute forever, since it never consumes
# the unicast miss budget. A live device comes back through normal discovery.
ARC_RECOVERY_GIVE_UP = 3600.0


def has_routing_service(device: dict[str, Any]) -> bool:
    """A name/IP or CMC/DBC service alone cannot support ARC control queries."""
    return any(
        service.get("type") == SERVICE_ARC
        and isinstance(service.get("port"), int)
        and 0 < service["port"] <= 65535
        for service in device.get("services", {}).values()
    )


class RoutingDiscoveryRecovery:
    """Resolve missing ARC records without depending on a browse Added event."""

    def __init__(self) -> None:
        self._last_attempt: dict[str, float] = {}
        self._missing_since: dict[str, float] = {}

    def has_expired(self, name: str) -> bool:
        """Whether recovery has failed for longer than ARC_RECOVERY_GIVE_UP."""
        since = self._missing_since.get(name)
        return since is not None and time.monotonic() - since >= ARC_RECOVERY_GIVE_UP

    async def async_recover(
        self,
        zeroconf: Zeroconf,
        devices: dict[str, dict[str, Any]],
        discovered_services: dict[str, str],
    ) -> bool:
        """Repair up to four devices concurrently; return whether records changed.

        A direct SRV/TXT/address resolution can recover a missing ARC browse
        result even when CMC or DBC discovery already supplied the device IP.
        Never guess the ARC port: software Dante endpoints can use other ports.
        """
        missing = {
            name: info
            for name, info in devices.items()
            if not has_routing_service(info)
        }
        now = time.monotonic()
        self._last_attempt = {
            name: attempted
            for name, attempted in self._last_attempt.items()
            if name in missing
        }
        self._missing_since = {
            name: self._missing_since.get(name, now) for name in missing
        }
        candidates = sorted(
            (
                name
                for name in missing
                if name not in self._last_attempt
                or now - self._last_attempt[name] >= ARC_RECOVERY_INTERVAL
            ),
            key=lambda name: self._last_attempt.get(name, float("-inf")),
        )[:ARC_RECOVERY_BATCH_SIZE]

        async def resolve(name: str) -> bool:
            first_attempt = name not in self._last_attempt
            self._last_attempt[name] = now
            log = LOGGER.warning if first_attempt else LOGGER.debug
            log(
                "Dante device %s has incomplete discovery (missing ARC routing "
                "service); resolving its endpoint, not counting a device timeout",
                name,
            )
            service_name = f"{name}.{SERVICE_ARC}"
            try:
                info = AsyncServiceInfo(SERVICE_ARC, service_name)
                if not await info.async_request(zeroconf, ARC_RECOVERY_TIMEOUT_MS):
                    return False
                addresses = info.parsed_addresses(IPVersion.V4Only)
                if not addresses or not info.port or not 0 < info.port <= 65535:
                    return False
                props = {
                    k.decode("utf-8") if isinstance(k, bytes) else k: v.decode("utf-8")
                    if isinstance(v, bytes)
                    else v
                    for k, v in info.properties.items()
                }
            except Exception as err:  # noqa: BLE001 - one bad record must not abort the poll
                LOGGER.debug("ARC discovery recovery failed for %s: %s", name, err)
                return False

            device = devices[name]
            device["ipv4"] = addresses[0]
            device.setdefault("services", {})[service_name] = {
                "type": SERVICE_ARC,
                "port": info.port,
                "properties": props,
            }
            device.setdefault("props", {}).update(props)
            discovered_services[service_name] = SERVICE_ARC
            self._last_attempt.pop(name, None)
            self._missing_since.pop(name, None)
            LOGGER.info(
                "Recovered Dante routing endpoint for %s at %s:%s",
                name,
                addresses[0],
                info.port,
            )
            return True

        return any(await asyncio.gather(*(resolve(name) for name in candidates)))
