# Changelog

## [0.2.0] - 2026-02-15

### Added
- AES67/SAP stream discovery via SAP multicast (239.255.255.255:9875)
- AES67 streams appear as selectable sources in RX subscription dropdowns
- AES67 subscription routing using reverse-engineered Dante command 0x3201 on port 4440
- Per-channel AES67 selection (individual L/R channels from stereo flows)
- SAP stream caching across poll cycles (merge, not replace)
- SDP parsing for session name, origin IP, multicast address, port, codec, channel names

### Changed
- RX subscription dropdowns now show both Dante TX channels and AES67 streams
- AES67 streams prefixed with `[AES67]` in dropdown options

## [0.1.0] - 2026-02-15

### Added
- Initial release
- mDNS/Zeroconf device discovery using HA's shared AsyncZeroconf instance
- Single config entry manages the entire Dante audio network
- Sensor entities: IP address, sample rate, latency, RX/TX channel counts, model, manufacturer, software version
- Select entities: sample rate, encoding, per-RX-channel subscription routing
- Number entities: device latency, per-channel gain (AVIO DAI/DAO models)
- Switch entity: AES67 mode toggle
- Button entity: identify (flash device LED)
- Services: `dante.add_subscription`, `dante.remove_subscription`, `dante.identify`
- Vendored netaudio library with HA-compatible imports
- Blocking UDP control protocol runs in executor to avoid event loop blocking
- 30-second polling interval for device state updates
