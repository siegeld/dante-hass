"""DataUpdateCoordinator for Dante Audio Network."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import select as sel
import socket
import struct
import time
from typing import Any

from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

from .const import DOMAIN, LOGGER, MDNS_TIMEOUT, SAP_MULTICAST, SAP_PORT, SAP_TIMEOUT, SCAN_INTERVAL
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
        self._aes67_streams: dict[str, Any] = {}
        # Local AES67 selections keyed by (device_name, rx_channel_num)
        self._aes67_selections: dict[tuple[str, int], str] = {}

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
                    # Normalize: strip trailing dot and .local suffix
                    server_name = server_name.rstrip(".")
                    if server_name.endswith(".local"):
                        server_name = server_name[:-6]

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

            # Discover AES67/SAP streams
            bind_ip = self._find_bind_ip(result)
            LOGGER.warning("SAP: bind_ip=%s from %d devices", bind_ip, len(result))
            if bind_ip:
                try:
                    new_streams = await self.hass.async_add_executor_job(
                        self._discover_sap_streams, bind_ip
                    )
                    # Merge new discoveries into cache (SAP announcements are
                    # periodic so we won't see all streams every poll cycle)
                    if new_streams:
                        self._aes67_streams.update(new_streams)
                    LOGGER.warning(
                        "SAP: found %d new, %d total AES67 streams: %s",
                        len(new_streams),
                        len(self._aes67_streams),
                        list(self._aes67_streams.keys()),
                    )
                except Exception as err:
                    LOGGER.warning("SAP discovery failed: %s", err)
            else:
                LOGGER.warning("No Dante device IPs found, skipping SAP discovery")

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
    ) -> bytes:
        """Build a 112-byte AES67 subscription command (0x3201).

        Protocol reverse-engineered from Dante Controller captures.
        """
        source_ip = socket.inet_aton(stream_info["origin_ip"])
        mcast_ip = socket.inet_aton(stream_info["multicast_addr"])
        flow_id = stream_info["session_id"] & 0xFFFFFFFF
        rtp_port = stream_info["port"]
        ch_count = stream_info["channels"]

        # Derive encoding byte from codec string (e.g. "L24/48000/2")
        codec = stream_info.get("codec", "")
        enc_name = codec.split("/")[0] if codec else "L24"
        enc_byte = DanteDataUpdateCoordinator._AES67_ENCODING_MAP.get(enc_name, 0x08)

        pkt = bytearray(112)

        # Header
        struct.pack_into(">2sHH2s", pkt, 0, b"\x28\x09", 112, seq, b"\x32\x01")
        # Flags/version
        pkt[10] = 0x01; pkt[11] = 0x01
        pkt[12] = 0x00; pkt[13] = 0x10
        # Record type
        struct.pack_into(">H", pkt, 18, 0x4202)
        # Record count
        struct.pack_into(">H", pkt, 28, 0x0001)
        # Offset
        struct.pack_into(">H", pkt, 34, 0x0068)
        # Sub-record structure
        struct.pack_into(">H", pkt, 44, 0x0003)
        struct.pack_into(">H", pkt, 46, 0x0040)
        struct.pack_into(">H", pkt, 52, 0x0002)
        struct.pack_into(">H", pkt, 54, 0x0060)

        # Flow source info (offset 64)
        struct.pack_into(">HH", pkt, 64, 0x1000, 0x000B)
        pkt[68:72] = source_ip
        struct.pack_into(">I", pkt, 76, flow_id)

        # Channel mapping (offset 96)
        struct.pack_into(">H", pkt, 96, rx_channel)
        struct.pack_into(">H", pkt, 98, ch_count)
        pkt[102] = flow_channel
        pkt[104] = enc_byte
        pkt[105] = ch_count
        struct.pack_into(">H", pkt, 106, rtp_port)
        pkt[108:112] = mcast_ip

        return bytes(pkt)

    def _send_aes67_subscribe(
        self,
        device_ip: str,
        rx_channel: int,
        flow_channel: int,
        stream_info: dict[str, Any],
    ) -> bool:
        """Send an AES67 subscription command to a Dante device (blocking I/O)."""
        import random

        seq = random.randint(0, 65535)
        pkt = self._build_aes67_subscribe_command(
            rx_channel, flow_channel, stream_info, seq
        )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)
        try:
            sock.sendto(pkt, (device_ip, self._AES67_COMMAND_PORT))
            resp, _ = sock.recvfrom(2048)
            # Check response: magic 0x2801, status at byte 8-9 == 0x0001 = success
            if len(resp) >= 10 and resp[0] == 0x28 and resp[1] == 0x01:
                status = struct.unpack_from(">H", resp, 8)[0]
                if status == 1:
                    return True
                LOGGER.warning(
                    "AES67 subscribe returned status %d for %s ch %d",
                    status, device_ip, rx_channel,
                )
                return False
            LOGGER.warning("AES67 subscribe unexpected response from %s", device_ip)
            return False
        except socket.timeout:
            LOGGER.warning("AES67 subscribe timeout from %s", device_ip)
            return False
        finally:
            sock.close()

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
