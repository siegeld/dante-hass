# Changelog

## 2026-08-22

### Fixed
- **Integration failed to set up entirely (`setup_error`) once the network reached 21 devices.** All five platforms passed `update_before_add=True` to `async_add_entities`. `DanteEntity` is a `CoordinatorEntity`, whose `async_update()` is `await self.coordinator.async_request_refresh()` — documented upstream as "Only used by the generic entity update service". So setup demanded a full coordinator refresh for each of 391 entities, blew through the config-entry setup budget, and Home Assistant cancelled the task (`CancelledError` out of `_async_add_and_update_entities`).

  The `update_before_add=True` added on 2026-03-01 was described as "a safety net against duplicate entity objects", but it has no deduplication semantics. Duplicates are prevented by `_attr_unique_id` (set on every entity in all five platforms) together with the coordinator-level `_platform_known_devices` set — which was the actual fix in that same change. Removing it is therefore safe and restores setup.

- **mDNS discovery resolved services one at a time.** Phase 1 of `_async_update_data` looped over every announced service awaiting a 3-second-timeout `AsyncServiceInfo.async_request` in series. With 142–144 announced services a single poll could run for minutes — longer than `SCAN_INTERVAL` (30 s), and long enough on its own to threaten the setup budget. Resolves now run concurrently via `asyncio.gather`, so wall time is the slowest single resolve rather than the sum. Platform setup went from being killed after >10 s to completing in ~0.2 s.

### Changed
- Per-service mDNS resolve timeout is now the named constant `MDNS_RESOLVE_TIMEOUT_MS` (3000) instead of a literal.

## 2026-03-13

### Fixed
- Fix device merge bug — use Dante device name (from mDNS service name) as device identifier instead of hardware hostname, which could be generic/short and cause multiple devices to merge into one HA device entry

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
