# Changelog

## [1.0.0] - 2026-02-15

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
