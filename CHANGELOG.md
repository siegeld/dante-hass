# Changelog

## 2026-08-28

### Fixed
- **A successful AES67 subscription was reported as a failure on any device that answers with the short-form ACK.** `_send_aes67_subscribe` accepted only reply magic `0x2801`. Verified on the wire by sending one identical subscribe packet to two devices seconds apart:

  ```
  danterbr20 (10.11.7.95)  len=14   2809 000e 1234 3201 0001 0100 0000
  danterbr17 (10.11.7.89)  len=108  2801 006c 1234 3201 0001 ...
  ```

  Both echo our sequence number (`0x1234`) and the `0x3201` command, and **both carry `status = 0x0001` at bytes 8-9, i.e. success.** They differ only in form: `0x2801` is a long reply echoing the full record, `0x2809` is a 14-byte ACK reusing the *request* magic. Matching `resp[1] == 0x01` alone threw away a successful subscribe on every short-form device.

  Downstream this was silent and expensive: `select.py` reset `_pending_option = None`, so the RX select snapped back to `None` within ~15 ms; `crestron-nax` then computed `routed = False` for the room and reported the zone **off** while its own `tune` had logged "ready". The Villa Office Sonos sat on Line-in with nothing arriving. Now accepts both reply forms and decides purely on the status word.

- **The per-channel subscription status code was parsed and then discarded.** `device.py` set `channel_status_text = None` unconditionally, which made the `if channel_status_text:` branch below it dead code. The device tells us *why* a subscription is or is not flowing and none of it reached a log or an entity — so a channel that was subscribed-but-unresolved was indistinguishable from a healthy one. It now resolves through `subscription_status.labels` and warns on any non-connected, non-idle state.

- **An unrecognised subscribe reply was discarded without evidence.** The warning now carries `len=` and the first 32 bytes as `hex=`, so the next protocol mismatch is a one-line diagnosis instead of a packet-capture session.

### Known gaps
- `subscription_status.labels` has no entry for **`0x0e` (14)**, which is what every *idle* rx channel on this fabric reports (the table documents `NONE = 0`). It is treated as idle via `_IDLE_STATUS_CODES` in `device.py` rather than warned about, and logged at debug. If the true meaning of 14 is confirmed, give it a label in `subscription_status.py` and drop it from that tuple.


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
