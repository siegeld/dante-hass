# Dante Audio Network Integration for Home Assistant

A custom component that discovers and controls [Dante](https://www.audinate.com/meet-dante/what-is-dante) audio network devices from Home Assistant.

## Features

- **Automatic device discovery** via mDNS/Zeroconf with resilient unicast polling (mDNS discovers new devices; once known, devices are queried directly by IP every poll cycle so they're never lost due to mDNS unreliability)
- **Audio subscription routing** -- route any TX channel to any RX channel across devices using Select entities
- **AES67/SAP stream discovery** -- automatically discovers AES67 multicast streams via SAP (Session Announcement Protocol) and presents them as selectable sources in RX subscription dropdowns
- **AES67 subscription routing** -- subscribe Dante RX channels to AES67 multicast flows (e.g. Crestron NAX streams) directly from the HA UI, no Dante Controller required. Subscribe and teardown frames are pinned byte-for-byte to real Dante Controller captures (`tests/test_aes67_frames.py`)
- **AES67 teardown** -- unsubscribing actually removes the flow. The native Dante unsubscribe does not touch an AES67 flow, so this sends the real per-channel teardown and refuses to report a channel clear if it fails
- **Device monitoring** -- sample rate, latency, channel counts, IP address, model info
- **Device control** -- sample rate, encoding, latency, per-channel gain (AVIO adapters)
- **AES67 mode** toggle
- **Identify** button (flash device LED)
- **HA services** for programmatic subscription management

## Requirements

- Home Assistant 2025.7+
- Dante devices reachable on the network (mDNS multicast must work)
- If Dante devices are on a separate VLAN, configure HA's network integration (Settings > System > Network) to include that interface

## Installation

Copy `custom_components/dante/` into your HA config directory:

```
<config>/custom_components/dante/
```

Restart Home Assistant, then add the integration via Settings > Devices & Services > Add Integration > "Dante Audio Network".

The config flow scans for Dante devices and shows what it finds. Confirm to create a single entry that manages the entire Dante network.

## Entities

Per discovered Dante device:

| Platform | Entities | Description |
|----------|----------|-------------|
| sensor | ip_address, sample_rate, latency, rx_channels, tx_channels, model, manufacturer, software_version | Read-only device info |
| select | sample_rate, encoding | Device-wide settings |
| select | rx_{n}_{name} | One per RX channel -- pick the TX source from any device |
| number | latency | Device latency in ms |
| number | gain_ch_{n} | Per-channel gain (AVIO DAI/DAO models only) |
| switch | aes67_mode | AES67 interoperability toggle |
| button | identify | Flash the device LED |

## Services

### `dante.add_subscription`
Route a TX channel to an RX channel.

| Parameter | Description |
|-----------|-------------|
| rx_device | Name of the receiving device |
| rx_channel | RX channel number |
| tx_device | Name of the transmitting device |
| tx_channel | TX channel name |

### `dante.remove_subscription`
Remove a subscription from an RX channel.

| Parameter | Description |
|-----------|-------------|
| rx_device | Name of the receiving device |
| rx_channel | RX channel number |

### `dante.identify`
Flash the LED on a device.

| Parameter | Description |
|-----------|-------------|
| device_name | Name of the device to identify |

## Network Notes

Dante uses mDNS (multicast DNS) for device discovery. If your Dante devices are on a dedicated audio VLAN:

1. Ensure the HA host has an interface on that VLAN
2. Go to Settings > System > Network (requires Advanced Mode)
3. Enable the VLAN interface so HA's zeroconf listens on it

## Credits

Built on the [netaudio](https://github.com/chris-ritsen/network-audio-controller) library by Chris Ritsen (vendored and adapted for HA compatibility).


## Verifying an AES67 subscription

**Never trust the subscribe reply** -- a device can acknowledge a command it does
not act on. Read the device's own state instead:

```sh
python3 tools/aes67_verify.py          # per-channel flow state for a device
```

| `rx_status`/`sub_status` (opcode `0x3000`) | meaning |
|---|---|
| `0x0000`/`0x0000` | no flow |
| `0x0100`/`0x000e` | transient, establishing -- re-poll before calling it failed |
| `0x0101`/`0x000e` | flow up |

Opcode `0x3200` is a cheaper binary check (subscribed -> ~100-byte reply,
unsubscribed -> 16-byte). Both are read-only and safe on a device playing audio.

Note that an AES67 flow never appears as an ordinary Dante subscription -- it does
not populate `tx_device_name` -- so its absence from the subscription list is not
evidence that no flow is running.

See `CLAUDE.md` for the protocol notes and the war stories behind these.
