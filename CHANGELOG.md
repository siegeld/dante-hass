# Changelog

## 2026-03-01

### Fixed
- Fix duplicate entities on HA restart — moved per-platform `known_devices` tracking from local variables (reset on every restart) to coordinator-level dict that persists across platform reloads
- Added `update_before_add=True` to all `async_add_entities` calls as a safety net against duplicate entity objects

## 2026-02-28

### Fixed
- Optimistic state update for Dante subscription selects — UI reflects changes immediately instead of waiting for next poll cycle

### Fixed
- Fix duplicate entities caused by null bytes in device names and unstable device name keying

## 2026-02-27

### Changed
- Stop depending on mDNS for known device availability — devices are queried directly by unicast UDP every poll cycle once discovered
- Persistent mDNS browser for continuous background discovery instead of per-poll scans
- Dynamic entity registration — new devices discovered mid-session get entities created automatically

### Fixed
- Improve discovery reliability with device caching and miss-count eviction (DEVICE_MISS_LIMIT consecutive failures before removal)
- Fix entity stability, socket timeouts, and AES67 state reconciliation after restart

## 2026-02-22

### Added
- AES67/SAP stream discovery — automatically discovers AES67 multicast streams and presents them as selectable RX sources
- AES67 subscription routing — subscribe Dante RX channels to AES67 multicast flows directly from the HA UI

## 2026-02-15

### Added
- Initial release — Dante Audio Network integration for Home Assistant
- mDNS device discovery, audio subscription routing, device monitoring and control
- Per-channel gain control for AVIO adapters
- AES67 mode toggle, device identify button
- HA services for programmatic subscription management
