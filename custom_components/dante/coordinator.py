"""DataUpdateCoordinator for Dante Audio Network."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import re
import select as sel
import socket
import struct
import time
from typing import Any

# Matches per-channel virtual sub-device mDNS names like "01@danterbr11-villa-tascom"
# or "32@danterbr7-theater-mixer". These are Dante channel announcements, not
# real devices, and never respond to unicast control queries.
_SUBCHANNEL_NAME_RE = re.compile(r"^\d+@")

from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

from .const import DOMAIN, LOGGER, MDNS_RESOLVE_TIMEOUT_MS, MDNS_TIMEOUT, DEVICE_MISS_LIMIT, SAP_MULTICAST, SAP_PORT, SAP_TIMEOUT, SCAN_INTERVAL
from .netaudio.const import SERVICE_CMC, SERVICES
from .netaudio.device import DanteDevice


class DanteDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage Dante device discovery and data.

    Architecture: mDNS is used ONLY for discovering new devices and updating
    IPs. Once a device is known, it is queried directly by unicast UDP every
    poll cycle. This avoids the fundamental unreliability of mDNS multicast
    on busy networks.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self._devices: dict = {}
        self._aes67_streams: dict[str, Any] = {}
        # Local AES67 selections keyed by (device_name, rx_channel_num)
        self._aes67_selections: dict[tuple[str, int], str] = {}
        # Track consecutive failed direct-query cycles per device (keyed by server_name)
        self._miss_count: dict[str, int] = {}
        # Cache last-known coordinator result data (keyed by dev_name)
        self._cached_data: dict[str, Any] = {}
        # Registry of all known devices with connection info (keyed by server_name)
        # Each entry: {ipv4, services, props, dev_name}
        self._known_devices: dict[str, dict[str, Any]] = {}
        # Persistent mDNS browser state
        self._browser: AsyncServiceBrowser | None = None
        self._discovered_services: dict[str, str] = {}  # name -> service_type
        self._browser_ready = asyncio.Event()
        # Per-platform known-devices tracking (survives coordinator refreshes)
        self._platform_known_devices: dict[str, set[str]] = {}

    def setdefault_known_devices(self, platform: str) -> None:
        """Ensure a known-devices set exists for a platform."""
        self._platform_known_devices.setdefault(platform, set())

    def _on_service_state_change(
        self,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
        **kwargs,
    ) -> None:
        """Handle mDNS service state changes from the persistent browser."""
        if state_change is ServiceStateChange.Added:
            self._discovered_services[name] = service_type
        elif state_change is ServiceStateChange.Removed:
            self._discovered_services.pop(name, None)

    async def async_start_browser(self) -> None:
        """Start the persistent mDNS browser."""
        if self._browser is not None:
            return
        aiozc = await zeroconf.async_get_async_instance(self.hass)
        self._browser = AsyncServiceBrowser(
            aiozc.zeroconf,
            SERVICES,
            handlers=[self._on_service_state_change],
        )
        # Give devices time to respond on initial startup
        await asyncio.sleep(MDNS_TIMEOUT * 3)
        self._browser_ready.set()
        LOGGER.info(
            "Persistent mDNS browser started, %d services found initially",
            len(self._discovered_services),
        )

    async def async_stop_browser(self) -> None:
        """Stop the persistent mDNS browser."""
        if self._browser is not None:
            await self._browser.async_cancel()
            self._browser = None

    def _build_device_data(
        self, device: DanteDevice, server_name: str
    ) -> dict[str, Any]:
        """Build the coordinator result dict for a single device."""
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
                    "friendly_name": ch.friendly_name,
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

        return dev_data

    def _resolve_server_name(self, info: AsyncServiceInfo, fallback_name: str) -> str:
        """Extract the Dante device name from the mDNS service name.

        Always uses the service name prefix (the Dante device name set in
        Dante Controller) rather than info.server (hardware hostname).
        Dante device names are guaranteed unique on the network and are
        what users expect to see. Hardware hostnames can be generic or
        short (e.g. "2") which caused device merges in the HA registry.
        """
        return fallback_name.split(".")[0]

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the Dante network.

        Three-phase approach:
        1. Resolve mDNS services to discover NEW devices and update IPs
        2. Merge mDNS results into the known-devices registry
        3. Query ALL known devices directly by IP (unicast UDP)

        This ensures known devices are never lost due to mDNS unreliability.
        A device is only removed after DEVICE_MISS_LIMIT consecutive direct
        query failures.
        """
        try:
            # Wait for browser to be ready (only blocks on first poll)
            await asyncio.wait_for(self._browser_ready.wait(), timeout=MDNS_TIMEOUT * 4)

            aiozc = await zeroconf.async_get_async_instance(self.hass)

            # --- PHASE 1: Resolve mDNS for new devices / IP updates ---
            found_services = list(self._discovered_services.items())
            mdns_hosts: dict[str, dict] = {}

            async def _resolve(name: str, service_type: str):
                """Resolve one mDNS service. Returns (name, info) or None."""
                try:
                    info = AsyncServiceInfo(service_type, name)
                    if not await info.async_request(aiozc.zeroconf, MDNS_RESOLVE_TIMEOUT_MS):
                        return None
                    return (name, service_type, info)
                except Exception as err:
                    LOGGER.debug("Error resolving %s: %s", name, err)
                    return None

            # Skip per-channel virtual sub-device announcements (e.g.
            # "01@danterbr11-villa-tascom"). These are mDNS-only artifacts
            # that never respond to unicast Dante control queries — adding
            # them just churns the known-devices registry and spams the
            # log with "unreachable" warnings every DEVICE_MISS_LIMIT cycles.
            to_resolve = [
                (name, service_type)
                for name, service_type in found_services
                if not _SUBCHANNEL_NAME_RE.match(name)
            ]

            # Resolve CONCURRENTLY. This used to be a sequential `for` loop with
            # an awaited 3s-timeout request per service. With 144 announced
            # services a single poll could run for minutes — longer than
            # SCAN_INTERVAL, and long enough to blow the config-entry setup
            # budget. Wall time is now the slowest single resolve, not the sum.
            resolved = await asyncio.gather(
                *(_resolve(name, st) for name, st in to_resolve)
            )

            for item in resolved:
                if item is None:
                    continue
                name, service_type, info = item

                addresses = info.parsed_addresses()
                if not addresses:
                    continue

                ipv4 = addresses[0]
                props = {}
                for k, v in info.properties.items():
                    k = k.decode("utf-8") if isinstance(k, bytes) else k
                    v = v.decode("utf-8") if isinstance(v, bytes) else v
                    props[k] = v

                server_name = self._resolve_server_name(info, name)

                if server_name not in mdns_hosts:
                    mdns_hosts[server_name] = {
                        "ipv4": ipv4,
                        "services": {},
                        "props": {},
                    }

                mdns_hosts[server_name]["ipv4"] = ipv4
                mdns_hosts[server_name]["services"][name] = {
                    "type": service_type,
                    "port": info.port,
                    "properties": props,
                }
                mdns_hosts[server_name]["props"].update(props)

            # --- PHASE 2: Merge mDNS into known-devices registry ---
            for server_name, mdns_info in mdns_hosts.items():
                if server_name not in self._known_devices:
                    LOGGER.info(
                        "New Dante device discovered: %s at %s",
                        server_name, mdns_info["ipv4"],
                    )
                    self._known_devices[server_name] = {
                        "ipv4": mdns_info["ipv4"],
                        "services": dict(mdns_info["services"]),
                        "props": dict(mdns_info["props"]),
                    }
                else:
                    existing = self._known_devices[server_name]
                    old_ip = existing.get("ipv4")
                    new_ip = mdns_info["ipv4"]
                    if old_ip != new_ip:
                        LOGGER.info(
                            "Device %s IP changed: %s -> %s",
                            server_name, old_ip, new_ip,
                        )
                    existing["ipv4"] = new_ip
                    existing.setdefault("services", {}).update(mdns_info["services"])
                    existing.setdefault("props", {}).update(mdns_info["props"])

            LOGGER.debug(
                "Dante poll: %d from mDNS, %d known devices total",
                len(mdns_hosts), len(self._known_devices),
            )

            # --- PHASE 3: Query ALL known devices by direct unicast ---
            result: dict[str, Any] = {}

            for server_name, known_info in list(self._known_devices.items()):
                device = DanteDevice(server_name=server_name)
                device.ipv4 = known_info["ipv4"]

                # Attach cached mDNS services so device opens proper sockets
                for svc_name, svc_data in known_info.get("services", {}).items():
                    device.services[svc_name] = svc_data

                # Apply cached mDNS properties
                props = known_info.get("props", {})
                if "id" in props:
                    device.mac_address = props["id"]
                if "model" in props:
                    device.model_id = props["model"]
                if "rate" in props:
                    try:
                        device.sample_rate = int(props["rate"])
                    except (ValueError, TypeError):
                        pass
                if "latency_ns" in props:
                    try:
                        device.latency = int(props["latency_ns"])
                    except (ValueError, TypeError):
                        pass
                if (
                    "router_info" in props
                    and props["router_info"] == '"Dante Via"'
                ):
                    device.software = "Dante Via"

                # Direct unicast query
                query_ok = False
                try:
                    await self.hass.async_add_executor_job(
                        lambda d=device: asyncio.run(d.get_controls())
                    )
                    query_ok = True
                except Exception as err:
                    LOGGER.debug(
                        "Direct query failed for %s (%s): %s",
                        server_name, known_info["ipv4"], err,
                    )

                # Check if we got meaningful data back
                has_data = query_ok and (
                    device.name or device.rx_channels or device.tx_channels
                )

                if has_data:
                    # Device is alive — build fresh data
                    self._miss_count.pop(server_name, None)
                    dev_name = device.name or known_info.get("dev_name") or server_name
                    # Cache the resolved dev_name so failed queries reuse it
                    if device.name:
                        known_info["dev_name"] = device.name
                    dev_data = self._build_device_data(device, server_name)
                    result[dev_name] = dev_data
                    self._devices[dev_name] = device
                else:
                    # Device unreachable — use cached data with miss tracking
                    misses = self._miss_count.get(server_name, 0) + 1
                    self._miss_count[server_name] = misses
                    dev_name = known_info.get("dev_name", server_name)

                    if misses <= DEVICE_MISS_LIMIT:
                        LOGGER.debug(
                            "Device %s unreachable (%d/%d), using cached data",
                            server_name, misses, DEVICE_MISS_LIMIT,
                        )
                        if dev_name in self._cached_data:
                            result[dev_name] = self._cached_data[dev_name]
                    else:
                        LOGGER.warning(
                            "Device %s unreachable for %d consecutive cycles, removing",
                            server_name, misses,
                        )
                        self._miss_count.pop(server_name, None)
                        self._known_devices.pop(server_name, None)
                        self._cached_data.pop(dev_name, None)
                        self._devices.pop(dev_name, None)

            # Update cache with current results
            self._cached_data.update(result)

            # Discover AES67/SAP streams
            bind_ip = self._find_bind_ip(result)
            LOGGER.debug("SAP: bind_ip=%s from %d devices", bind_ip, len(result))
            if bind_ip:
                try:
                    new_streams = await self.hass.async_add_executor_job(
                        self._discover_sap_streams, bind_ip
                    )
                    # Merge new discoveries into cache (SAP announcements are
                    # periodic so we won't see all streams every poll cycle)
                    if new_streams:
                        self._aes67_streams.update(new_streams)
                    LOGGER.debug(
                        "SAP: found %d new, %d total AES67 streams",
                        len(new_streams),
                        len(self._aes67_streams),
                    )
                except Exception as err:
                    LOGGER.debug("SAP discovery failed: %s", err)
            else:
                LOGGER.debug("No Dante device IPs found, skipping SAP discovery")

            # Reconcile AES67 subscriptions from device state + SAP streams
            if self._aes67_streams:
                self._reconcile_aes67_subscriptions(result)

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

    def get_all_aes67_sources(self) -> list[str]:
        """Get all AES67 streams as individual channel options."""
        options = []
        for name, info in sorted(self._aes67_streams.items()):
            ch_names = self._get_channel_names(info)
            for ch_name in ch_names:
                options.append(f"[AES67] {name} - {ch_name}")
        return options

    @staticmethod
    def _get_channel_names(info: dict[str, Any]) -> list[str]:
        """Extract individual channel names from stream info."""
        ch_count = info.get("channels", 1)
        channel_info = info.get("channel_info", "")

        # Try to parse from i= line, e.g. "2 channels: Tx Left, Tx Right"
        if channel_info and ":" in channel_info:
            _, _, names_part = channel_info.partition(":")
            names = [n.strip() for n in names_part.split(",") if n.strip()]
            if len(names) == ch_count:
                return names

        # Fallback: generate generic names
        if ch_count == 1:
            return ["Mono"]
        if ch_count == 2:
            return ["Left", "Right"]
        return [f"Ch{i+1}" for i in range(ch_count)]

    @staticmethod
    def _find_bind_ip(result: dict[str, Any]) -> str | None:
        """Determine the local IP on the same subnet as discovered Dante devices."""
        for dev_data in result.values():
            if not isinstance(dev_data, dict):
                continue
            ipv4 = dev_data.get("ipv4")
            if not ipv4:
                continue
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect((ipv4, 1))
                local_ip = s.getsockname()[0]
                s.close()
                return local_ip
            except Exception:
                continue
        return None

    def _discover_sap_streams(self, bind_ip: str) -> dict[str, Any]:
        """Discover AES67 streams via SAP multicast (blocking I/O)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", SAP_PORT))

            # Join SAP multicast group on the Dante network interface
            mreq = struct.pack(
                "4s4s",
                socket.inet_aton(SAP_MULTICAST),
                socket.inet_aton(bind_ip),
            )
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

            streams: dict[str, Any] = {}
            deadline = time.monotonic() + SAP_TIMEOUT

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                ready, _, _ = sel.select([sock], [], [], min(remaining, 1.0))
                if not ready:
                    continue
                try:
                    data, _addr = sock.recvfrom(4096)
                    stream = self._parse_sap_packet(data)
                    if stream:
                        streams[stream["session_name"]] = stream
                except Exception:
                    pass

            return streams
        finally:
            sock.close()

    @staticmethod
    def _parse_sap_packet(data: bytes) -> dict[str, Any] | None:
        """Parse a SAP packet and extract SDP stream info."""
        if len(data) < 8:
            return None

        header = data[0]
        version = (header >> 5) & 0x07
        addr_type = (header >> 4) & 0x01
        msg_type = (header >> 2) & 0x01  # 0=announcement, 1=deletion

        if version != 1 or msg_type != 0:
            return None

        auth_len = data[1]  # in 32-bit words
        origin_len = 4 if addr_type == 0 else 16
        payload_start = 4 + origin_len + (auth_len * 4)

        if payload_start >= len(data):
            return None

        payload = data[payload_start:]

        # Skip optional MIME type (null-terminated string before SDP)
        if not payload.startswith(b"v="):
            null_idx = payload.find(b"\0")
            if null_idx == -1:
                return None
            payload = payload[null_idx + 1:]

        try:
            sdp_text = payload.decode("utf-8", errors="replace")
        except Exception:
            return None

        return DanteDataUpdateCoordinator._parse_sdp(sdp_text)

    # Encoding byte used in the 0x3201 AES67 subscription command.
    # Derived from a single capture of an L24 stream; extend as needed.
    _AES67_ENCODING_MAP = {"L24": 0x08, "L16": 0x06, "L32": 0x0A}
    _AES67_COMMAND_PORT = 4440

    @staticmethod
    def _parse_sdp(sdp: str) -> dict[str, Any] | None:
        """Parse SDP text and extract AES67 stream info."""
        session_name = None
        session_id = None
        origin_ip = None
        multicast_addr = None
        port = None
        codec = None
        channels = 1
        channel_info = None

        for line in sdp.strip().splitlines():
            line = line.strip()
            if line.startswith("s="):
                session_name = line[2:]
            elif line.startswith("o="):
                # o=nax 821074694 127 IN IP4 10.11.7.71
                parts = line[2:].split()
                if len(parts) >= 6:
                    origin_ip = parts[5]
                    try:
                        session_id = int(parts[1])
                    except ValueError:
                        pass
            elif line.startswith("c="):
                # c=IN IP4 239.69.85.220/32
                parts = line[2:].split()
                if len(parts) >= 3:
                    multicast_addr = parts[2].split("/")[0]
            elif line.startswith("m="):
                # m=audio 5004 RTP/AVP 97
                parts = line[2:].split()
                if len(parts) >= 2:
                    try:
                        port = int(parts[1])
                    except ValueError:
                        pass
            elif line.startswith("a=rtpmap:"):
                # a=rtpmap:97 L24/48000/2
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    codec = parts[1]
                    codec_parts = codec.split("/")
                    if len(codec_parts) >= 3:
                        try:
                            channels = int(codec_parts[2])
                        except ValueError:
                            pass
            elif line.startswith("i="):
                channel_info = line[2:]

        if not session_name:
            return None

        return {
            "session_name": session_name,
            "session_id": session_id,
            "origin_ip": origin_ip,
            "multicast_addr": multicast_addr,
            "port": port,
            "codec": codec,
            "channels": channels,
            "channel_info": channel_info,
        }

    @staticmethod
    def _build_aes67_subscribe_command(
        rx_channel: int,
        flow_channel: int,
        stream_info: dict[str, Any],
        seq: int,
        n_rx: int = 2,
        start_code: int = 0x2729,
        token: int = 0x0000,
    ) -> bytes:
        """Build a 104-byte AES67 subscription command (0x3201).

        Templated on real Dante Controller captures (2026-08-28) rather than
        guessed. tests/test_aes67_frames.py pins this byte-for-byte against a
        capture of Controller subscribing danterbr20-villa-office, where the
        flow demonstrably came up (audio confirmed, 0x3000 read 0101/000e).

        The PREVIOUS version of this function was 112 bytes: it put the channel
        selector at offset 96 and pushed the codec/port/group tail 8 bytes late.
        Controller sends no such field there. That builder drove older hardware
        but silently failed on newer units, which ACKed and did nothing -- the
        Villa Office room could not be tuned at all.

        Layout (offsets are into the whole frame):
             0  start code (ECHOED by the device, not a protocol marker -- the
                same request under 0x2729/0x2809/0x2801 returns byte-identical
                payloads; some firmware normalises it in the reply)
             2  total length (104)
             4  sequence
             6  opcode 0x3201
            14  transient UI token; Controller varies it, zero is accepted
            42  per-rx-channel map, one uint16 per rx channel:
                entry[i] = flow channel feeding rx channel i+1, 0 = leave alone.
                THE LIFECYCLE IS PER-CHANNEL -- Controller sends one subscribe
                per channel, and one 0x3010 per channel to tear down.
            68  flow source IPv4
            76  flow/session id
            96  encoding byte
            97  channel count
            98  RTP port
           100  multicast group IPv4
        """
        p = bytearray(104)
        struct.pack_into(">HHHHH", p, 0, start_code, 104, seq, 0x3201, 0x0000)
        struct.pack_into(">HH", p, 10, 0x0101, 0x0010)
        struct.pack_into(">H", p, 14, token)
        struct.pack_into(">H", p, 18, 0x0202)
        struct.pack_into(">H", p, 28, 0x0001)
        struct.pack_into(">H", p, 30, 0x0002)
        struct.pack_into(">H", p, 32, 0x0001)
        struct.pack_into(">H", p, 34, 0x0060)
        struct.pack_into(">H", p, 36, 0x002A)
        struct.pack_into(">H", p, 38, 0x002C)
        struct.pack_into(">H", p, 40, 0x0030)
        for i in range(n_rx):
            struct.pack_into(
                ">H", p, 42 + i * 2,
                flow_channel if (i + 1) == rx_channel else 0,
            )
        struct.pack_into(">H", p, 52, 0x0800)
        struct.pack_into(">H", p, 60, 0x0003)
        struct.pack_into(">H", p, 62, 0x0040)
        struct.pack_into(">H", p, 64, 0x1000)
        struct.pack_into(">H", p, 66, 0x000B)
        p[68:72] = socket.inet_aton(stream_info["origin_ip"])
        struct.pack_into(">I", p, 76, stream_info["session_id"] & 0xFFFFFFFF)
        codec = stream_info.get("codec", "")
        enc_name = codec.split("/")[0] if codec else "L24"
        p[96] = DanteDataUpdateCoordinator._AES67_ENCODING_MAP.get(enc_name, 0x08)
        p[97] = stream_info["channels"]
        struct.pack_into(">H", p, 98, stream_info["port"])
        p[100:104] = socket.inet_aton(stream_info["multicast_addr"])
        return bytes(p)

    @staticmethod
    def _build_aes67_unsubscribe_command(
        rx_channel: int,
        seq: int,
        start_code: int = 0x2729,
    ) -> bytes:
        """Build the 52-byte AES67 teardown (0x3010) for ONE rx channel.

        Dante Controller does NOT use 0x3201 to unsubscribe -- 0x3201 does not
        appear anywhere in a teardown capture. It sends 0x3010, one per channel.

        This is why `off` was as broken as `on`: select.py's SUBSCRIPTION_NONE
        path called the NATIVE Dante remove_subscription, which does not touch an
        AES67 flow. A test flow cleared that way was still running four hours
        later on both 0x3000 and 0x3200, while HA reported the channel clear.

        Because the lifecycle is per-channel, tearing down a stereo flow means
        sending this for EVERY channel -- clearing only one leaves the other
        holding the flow up.

        There are TWO working teardowns. Dante Controller itself uses 0x3410 (a
        36-byte frame, channel as a uint16 at offset 20). Both were tested head to
        head on one device on 2026-08-28 with two channels subscribed: 0x3010
        cleared ch1 and 0x3410 cleared ch2, each confirmed by the subscription
        state going 0101/000e -> 0000/0000. We send 0x3010 because it is the one
        this code has always built and it is proven; 0x3410 is documented here so
        nobody assumes Controller's choice means ours is wrong.
        """
        p = bytearray(52)
        struct.pack_into(">HHHHH", p, 0, start_code, 0x0034, seq, 0x3010, 0x0000)
        struct.pack_into(">HH", p, 10, 0x0201, rx_channel)
        return bytes(p)

    _AES67_START_CODE = 0x2729

    def _aes67_command(
        self, device_ip: str, pkt: bytes, opcode: int, seq: int, what: str
    ) -> bool:
        """Send one AES67 control frame and decide success from the reply.

        The reply ECHOES the start code we sent, so the start code carries no
        information and must NOT be used to judge success -- an earlier version
        of this checked for 0x2801 and rejected genuine successes from any device
        that echoes verbatim. Judge on: the echoed sequence number (so we are not
        reading a stale or unrelated datagram), the echoed opcode, and the status
        word at offset 8.

        Verified live 2026-08-28 against danterbr17-villa-club-room:
          subscribe 0x3201 -> 100-byte reply, status 0x0001, flow came up
          teardown  0x3010 ->  10-byte reply, status 0x0001, flow went away
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)
        try:
            sock.sendto(pkt, (device_ip, self._AES67_COMMAND_PORT))
            resp, _ = sock.recvfrom(2048)
            if len(resp) < 10:
                LOGGER.warning(
                    "AES67 %s: short reply from %s: len=%d hex=%s",
                    what, device_ip, len(resp), resp.hex(),
                )
                return False
            r_seq, r_op, status = struct.unpack_from(">HHH", resp, 4)
            if r_seq != seq or r_op != opcode:
                LOGGER.warning(
                    "AES67 %s: reply mismatch from %s (seq %04x/%04x op %04x/%04x) hex=%s",
                    what, device_ip, r_seq, seq, r_op, opcode, resp[:32].hex(),
                )
                return False
            if status != 1:
                LOGGER.warning(
                    "AES67 %s: %s returned status 0x%04x hex=%s",
                    what, device_ip, status, resp[:32].hex(),
                )
                return False
            return True
        except socket.timeout:
            LOGGER.warning("AES67 %s: timeout from %s", what, device_ip)
            return False
        finally:
            sock.close()

    def _send_aes67_subscribe(
        self,
        device_ip: str,
        rx_channel: int,
        flow_channel: int,
        stream_info: dict[str, Any],
        n_rx: int = 2,
    ) -> bool:
        """Subscribe one rx channel to an AES67 flow (blocking I/O)."""
        import random

        seq = random.randint(0, 65535)
        pkt = self._build_aes67_subscribe_command(
            rx_channel, flow_channel, stream_info, seq,
            n_rx=n_rx, start_code=self._AES67_START_CODE,
        )
        return self._aes67_command(
            device_ip, pkt, 0x3201, seq, f"subscribe ch{rx_channel}"
        )

    def _send_aes67_unsubscribe(self, device_ip: str, rx_channel: int) -> bool:
        """Tear down the AES67 flow on one rx channel (blocking I/O).

        The native Dante remove_subscription does NOT do this -- a flow cleared
        that way keeps running while HA reports the channel clear. The lifecycle
        is per-channel, so a stereo flow needs this for EVERY channel; clearing
        one leaves the other holding the flow up.
        """
        import random

        seq = random.randint(0, 65535)
        pkt = self._build_aes67_unsubscribe_command(
            rx_channel, seq, start_code=self._AES67_START_CODE
        )
        return self._aes67_command(
            device_ip, pkt, 0x3010, seq, f"teardown ch{rx_channel}"
        )

    def get_aes67_stream_info(self, option: str) -> tuple[dict[str, Any], int] | None:
        """Parse an AES67 option string and return (stream_info, flow_channel_index).

        Option format: '[AES67] StreamName - ChannelName'
        """
        # Strip prefix
        rest = option[8:]  # after "[AES67] "
        if " - " not in rest:
            return None
        stream_name, ch_name = rest.rsplit(" - ", 1)

        stream_info = self._aes67_streams.get(stream_name)
        if not stream_info:
            return None

        # Find the flow channel index (1-based)
        ch_names = self._get_channel_names(stream_info)
        for idx, name in enumerate(ch_names, 1):
            if name == ch_name:
                return (stream_info, idx)

        return None

    def _reconcile_aes67_subscriptions(self, result: dict[str, Any]) -> None:
        """Restore _aes67_selections from device subscriptions + SAP streams.

        AES67 subscriptions survive restart at the device level, but the
        display-string mapping (_aes67_selections) is runtime-only. After SAP
        discovery populates _aes67_streams, cross-reference each device's
        subscription data against known AES67 streams to rebuild the mapping.
        """
        # Build lookups: origin_ip -> stream_info, multicast_addr -> stream_info
        ip_to_stream: dict[str, tuple[str, dict]] = {}
        mcast_to_stream: dict[str, tuple[str, dict]] = {}
        for stream_name, info in self._aes67_streams.items():
            if info.get("origin_ip"):
                ip_to_stream[info["origin_ip"]] = (stream_name, info)
            if info.get("multicast_addr"):
                mcast_to_stream[info["multicast_addr"]] = (stream_name, info)

        reconciled = 0
        for dev_name, dev_data in result.items():
            for sub in dev_data.get("subscriptions", []):
                tx_dev = sub.get("tx_device_name", "")
                tx_ch = sub.get("tx_channel_name", "")
                rx_ch_name = sub.get("rx_channel_name", "")

                # Match tx_device_name against AES67 stream origin/multicast IPs
                match = ip_to_stream.get(tx_dev) or mcast_to_stream.get(tx_dev)
                if not match:
                    continue

                stream_name, stream_info = match

                # Find the RX channel number from its name
                rx_num = None
                for num, ch in dev_data.get("rx_channels", {}).items():
                    if ch.get("name") == rx_ch_name:
                        rx_num = num
                        break
                if rx_num is None:
                    continue

                # Skip if already set (runtime selection takes precedence)
                key = (dev_name, rx_num)
                if key in self._aes67_selections:
                    continue

                # Determine channel display name
                ch_names = self._get_channel_names(stream_info)
                ch_display = None
                # Try matching tx_channel_name against known channel names
                if tx_ch in ch_names:
                    ch_display = tx_ch
                else:
                    # Try interpreting as 1-based index
                    try:
                        ch_idx = int(tx_ch) - 1
                        if 0 <= ch_idx < len(ch_names):
                            ch_display = ch_names[ch_idx]
                    except (ValueError, IndexError):
                        pass
                if ch_display is None and ch_names:
                    ch_display = ch_names[0]

                display_str = f"[AES67] {stream_name} - {ch_display}"
                self._aes67_selections[key] = display_str
                reconciled += 1
                LOGGER.debug(
                    "Reconciled AES67 subscription: %s ch%d -> %s",
                    dev_name, rx_num, display_str,
                )

        if reconciled:
            LOGGER.warning(
                "Reconciled %d AES67 subscription(s) from device state",
                reconciled,
            )
