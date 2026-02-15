# Dante Integration Architecture

## Overview

Single config entry manages the entire Dante audio network. Discovery, polling, and control are centralized in one `DataUpdateCoordinator`. Each discovered Dante device becomes an HA Device with child entities across multiple platforms.

## Component Structure

```
dante/
  __init__.py          # Entry setup, service registration, platform forwarding
  config_flow.py       # mDNS scan -> show devices -> confirm
  coordinator.py       # DataUpdateCoordinator: discover + poll every 30s
  entity.py            # Base DanteEntity (CoordinatorEntity subclass)
  const.py             # Domain, logger, constants
  sensor.py            # Read-only device info (8 sensors per device)
  select.py            # Sample rate, encoding, RX subscription routing
  number.py            # Latency, per-channel gain
  switch.py            # AES67 mode
  button.py            # Identify (flash LED)
  strings.json         # Config flow UI text
  services.yaml        # Service definitions
  netaudio/            # Vendored netaudio library
    browser.py         # DanteBrowser (sync mDNS discovery, not used by coordinator)
    device.py          # DanteDevice (UDP control protocol)
    channel.py         # DanteChannel
    subscription.py    # DanteSubscription
    control.py         # Low-level Dante control commands
    const.py           # Service types, ports
    multicast.py       # Multicast helpers
```

## Data Flow

```
                    HA Zeroconf (shared)
                          |
              AsyncServiceBrowser (3s scan)
                          |
                  AsyncServiceInfo.async_request()
                          |
              +-- resolve IPs, ports, properties --+
              |                                     |
         DanteDevice()                        device.services = {...}
              |
    hass.async_add_executor_job(device.get_controls())
              |
         UDP commands to device control port
              |
    +-- device name, channel counts, channels, subscriptions --+
              |
      coordinator.data[device_name] = { structured dict }
              |
      entities read from coordinator.data
```

## Key Design Decisions

### Single config entry for the whole network
Dante routing spans devices (device A's RX subscribes to device B's TX). A per-device config entry would make cross-device routing awkward. One entry = one coordinator = one view of the whole network.

### Subscription routing via Select entities
Each RX channel gets a Select entity. Options are populated from ALL discovered TX channels across all devices, formatted as `"DeviceName - ChannelName"`. Selecting a value calls `device.add_subscription()`. Selecting "None" calls `device.remove_subscription()`. This gives full matrix routing control from the HA UI.

### Vendored netaudio library
The upstream `netaudio` PyPI package has dependency conflicts with HA (old pinned zeroconf, pulls redis/fastapi). The core device control code is vendored under `dante/netaudio/` with imports rewritten from `netaudio.dante.X` to relative `.X`.

### Blocking control protocol in executor
`DanteDevice.get_controls()` uses blocking UDP sockets (`socket.send/recv`). Despite being `async def`, the underlying I/O is synchronous. The coordinator wraps it in `hass.async_add_executor_job(asyncio.run(device.get_controls()))` to avoid blocking the event loop.

### HA shared zeroconf for discovery
Discovery uses HA's shared `AsyncZeroconf` instance via `zeroconf.async_get_async_instance()`. This avoids creating duplicate zeroconf instances (which HA actively prevents via monkey-patching). The `AsyncServiceBrowser` registers new service types on the existing socket.

**Important**: HA's zeroconf only listens on interfaces enabled in Settings > System > Network. If Dante devices are on a separate VLAN, that interface must be enabled there.

### Handler signature for zeroconf 0.148+
Zeroconf's `Signal.fire()` passes all arguments as **kwargs**:
```python
handler(zeroconf=..., service_type=..., name=..., state_change=...)
```
Handler parameter names must match exactly (e.g., `zeroconf` not `zeroconf_obj`).

## Entity Hierarchy

```
Config Entry: "Dante Audio Network"
  └── Coordinator (polls every 30s)
        └── Device: "danterbr7-theater-mixer" (10.11.7.61)
              ├── sensor.danterbr7_theater_mixer_ip_address
              ├── sensor.danterbr7_theater_mixer_sample_rate
              ├── sensor.danterbr7_theater_mixer_rx_channels (32)
              ├── sensor.danterbr7_theater_mixer_tx_channels (32)
              ├── select.danterbr7_theater_mixer_sample_rate
              ├── select.danterbr7_theater_mixer_rx_1_01 = "villa-sonos - Output 1"
              ├── select.danterbr7_theater_mixer_rx_2_02 = "villa-sonos - Output 2"
              ├── ...
              ├── number.danterbr7_theater_mixer_latency
              ├── switch.danterbr7_theater_mixer_aes67_mode
              └── button.danterbr7_theater_mixer_identify
```

## AES67/SAP Stream Discovery

### Overview
Crestron NAX and other AES67-capable devices advertise multicast audio streams via SAP (Session Announcement Protocol). The coordinator discovers these streams and presents them as selectable sources alongside native Dante TX channels.

### SAP Discovery Flow
```
SAP multicast (239.255.255.255:9875)
          |
    UDP socket joins multicast group
    (binds to Dante VLAN interface)
          |
    Collect packets for SAP_TIMEOUT (10s)
          |
    Parse SAP header (v1, IPv4/IPv6, optional MIME type)
          |
    Parse SDP payload:
      s= session name
      o= origin IP, session_id (used as flow ID)
      c= multicast address
      m= port, codec
      a=rtpmap: encoding details
      i= channel names
          |
    Merge into self._aes67_streams cache
          |
    Dropdown options: "[AES67] SessionName - Tx Left", "[AES67] SessionName - Tx Right"
```

### AES67 Subscription Protocol (command 0x3201)
Native Dante subscriptions use command 0x3010 on port 8800 via the netaudio library. AES67 subscriptions use a different command and port, reverse-engineered from Dante Controller packet captures:

- **Port**: 4440 (not in netaudio's PORTS list)
- **Magic**: 0x2809 (vs netaudio's 0x27ff)
- **Command**: 0x3201
- **Packet size**: 112 bytes

Key fields in the 112-byte packet:
```
Bytes 0-1:     28 09       Magic
Bytes 2-3:     00 70       Length (112)
Bytes 4-5:     XX XX       Sequence number
Bytes 6-7:     32 01       Command (AES67 subscribe)
Bytes 18-19:   42 02       Record type
Bytes 44-47:   source IP   (AES67 origin, e.g., NAX device)
Bytes 76-79:   flow ID     (SDP session_id from o= line)
Bytes 96-97:   RX channel  (1-based)
Bytes 98-99:   total flow channels
Byte 102:      flow ch idx (1=L, 2=R for stereo)
Byte 104:      encoding    (0x06=L16, 0x08=L24, 0x0A=L32)
Byte 105:      channels    (in flow)
Bytes 106-107: RTP port    (typically 5004)
Bytes 108-111: multicast   (address of the flow)
```

The coordinator sends this command directly via UDP (bypassing netaudio) and checks the response for a matching command echo to confirm success.

### AES67 Selection Persistence
AES67 subscriptions are tracked in `coordinator._aes67_selections` (a dict keyed by `(device_name, rx_channel_num)`). This is needed because AES67 subscriptions don't appear in the device's native subscription list returned by netaudio. The `current_option` property checks this dict first before falling back to native subscription data.

## Coordinator Data Schema

```python
coordinator.data = {
    "danterbr7-theater-mixer": {
        "server_name": "X-DANTE-1f74c8.local.",
        "name": "danterbr7-theater-mixer",
        "ipv4": "10.11.7.61",
        "mac_address": "...",
        "manufacturer": "...",
        "model": "...",
        "model_id": "X-DANTE",
        "software": None,
        "sample_rate": 48000,
        "latency": 1000000,
        "rx_count": 32,
        "tx_count": 32,
        "rx_channels": {
            1: {"name": "01", "number": 1},
            2: {"name": "02", "number": 2},
            ...
        },
        "tx_channels": { ... },
        "subscriptions": [
            {
                "rx_channel_name": "01",
                "tx_channel_name": "Dante Output 1",
                "tx_device_name": "danterbr3-villa-sonos",
                "status_code": 0,
            },
            ...
        ],
    },
    ...

    # AES67 streams (keyed by session name)
    "__aes67_streams__": {
        "MediaStreamNax1Player1": {
            "session_name": "MediaStreamNax1Player1",
            "session_id": "821074694",
            "origin_ip": "10.11.7.75",
            "multicast_addr": "239.69.85.220",
            "port": 5004,
            "codec": "L24/48000/2",
            "channels": 2,
            "channel_names": ["Tx Left", "Tx Right"],
        },
        ...
    },
}
```
