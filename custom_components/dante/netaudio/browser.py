import logging
import time
import traceback

import zeroconf as _zc_mod
from zeroconf import DNSService, IPVersion, ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf

from .device import DanteDevice
from .const import SERVICE_CMC, SERVICES

logger = logging.getLogger("netaudio")

# HA monkey-patches Zeroconf.__new__ and __init__.
# Use the original from zeroconf._core which HA doesn't patch.
from zeroconf._core import Zeroconf as _CoreZeroconf
_real_init = _CoreZeroconf.__init__


class _RealZeroconf(Zeroconf):
    """Zeroconf subclass that bypasses HA's constructor patch."""

    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self, **kwargs):
        _real_init(self, **kwargs)


class DanteBrowser:
    """Discover Dante devices via mDNS using sync Zeroconf."""

    def __init__(self, mdns_timeout: float) -> None:
        self._devices = {}
        self._raw_services = []
        self._mdns_timeout: float = mdns_timeout

    @property
    def devices(self):
        return self._devices

    @property
    def services(self):
        return self._raw_services

    def get_devices(self) -> dict:
        """Discover Dante devices (blocking, run in a thread)."""
        zc = _RealZeroconf(ip_version=IPVersion.V4Only)
        try:
            return self._browse(zc)
        finally:
            # Use real close, not HA's no-op
            if hasattr(zc, 'ha_close'):
                zc.ha_close()
            else:
                zc.close()

    def _browse(self, zc: Zeroconf) -> dict:
        discovered = []

        def on_change(**kwargs):
            state_change = kwargs.get("state_change")
            name = kwargs.get("name")
            service_type = kwargs.get("service_type")
            if state_change is ServiceStateChange.Added:
                discovered.append((service_type, name))

        browser = ServiceBrowser(zc, SERVICES, handlers=[on_change])
        time.sleep(self._mdns_timeout)
        browser.cancel()

        self._raw_services = discovered
        logger.debug("Found %d raw services", len(discovered))

        # Resolve each service and group by host
        device_hosts = {}

        for service_type, name in discovered:
            try:
                info = ServiceInfo(service_type, name)
                if not info.request(zc, 3000):
                    logger.warning("Could not resolve service %s", name)
                    continue

                addresses = info.parsed_addresses()
                if not addresses:
                    continue

                ipv4 = addresses[0]

                service_properties = {}
                for key, value in info.properties.items():
                    if isinstance(key, bytes):
                        key = key.decode("utf-8")
                    if isinstance(value, bytes):
                        value = value.decode("utf-8")
                    service_properties[key] = value

                # Get server_name from cache DNS records
                server_name = None
                cache_entries = zc.cache.entries_with_name(name)
                for record in cache_entries:
                    if isinstance(record, DNSService):
                        server_name = record.server
                        break

                if not server_name:
                    # Fallback: derive from service name
                    server_name = name.split(".")[0] if "." in name else name

                # Normalize: strip trailing dot and .local suffix
                server_name = server_name.rstrip(".")
                if server_name.endswith(".local"):
                    server_name = server_name[:-6]

                service_data = {
                    "ipv4": ipv4,
                    "name": name,
                    "port": info.port,
                    "properties": service_properties,
                    "server_name": server_name,
                    "type": service_type,
                }

                if server_name not in device_hosts:
                    device_hosts[server_name] = {}
                device_hosts[server_name][name] = service_data

            except Exception as e:
                logger.warning("Error resolving service %s: %s", name, e)

        logger.debug("Found %d device host(s)", len(device_hosts))

        # Build DanteDevice objects
        for hostname, device_services in device_hosts.items():
            device = DanteDevice(server_name=hostname)

            try:
                for service_name, service in device_services.items():
                    device.services[service_name] = service
                    service_properties = service["properties"]

                    if not device.ipv4:
                        device.ipv4 = service["ipv4"]

                    if "id" in service_properties and service["type"] == SERVICE_CMC:
                        device.mac_address = service_properties["id"]

                    if "model" in service_properties:
                        device.model_id = service_properties["model"]

                    if "rate" in service_properties:
                        device.sample_rate = int(service_properties["rate"])

                    if (
                        "router_info" in service_properties
                        and service_properties["router_info"] == '"Dante Via"'
                    ):
                        device.software = "Dante Via"

                    if "latency_ns" in service_properties:
                        device.latency = int(service_properties["latency_ns"])

            except Exception as e:
                logger.warning("Error building device %s: %s", hostname, e)
                traceback.print_exc()

            self._devices[hostname] = device

        return self._devices
