# Changelog

## [1.3.3] - 2026-10-21

### Fixed
- **Late replies no longer mint junk RX channels.** `dante_command` sent a
  request and took the next datagram on the socket. A reply that missed its 1s
  timeout stayed queued and was read as the answer to the following request, so
  an RX-channel page could be parsed from a device-name reply. Each such restart
  left permanent orphan entities -- `danterbr15-villa-mics` `rx_0` on
  2026-10-18; `danterbr3-villa-sonos` `rx_0` and `rx_25888` (`0x6520`, the
  bytes `"e "`) on 2026-10-21. ARC-family replies must now echo the request's
  sequence id and opcode (bytes 4:8); anything else is discarded within the
  same timeout. Verified beforehand that all 21 devices on the fabric echo
  both. The orphaned entities were removed from the registry.

## [1.3.2] - 2026-10-21

### Fixed
- **The first poll no longer runs SAP discovery.** 1.3.1 removed the browse
  wait, but the entities still appeared 116s after setup began: the first poll
  itself runs inside config-entry setup during HA's bootstrap, and it included
  the SAP listen -- a fixed `SAP_TIMEOUT` (10s) executor job, which is 10.2s of
  a normal 10.45s poll (all 21 device queries take ~0.2s). Under bootstrap's
  executor and event-loop contention that stretches much further. The SAP
  stream table only adds AES67 sources to the selects, so the next poll (30s
  later) fills it.
- The first poll logs one INFO line with its duration and the device-query
  share, so a slow startup is measurable without enabling debug logging.

## [1.3.1] - 2026-10-21

### Fixed
- **Entities no longer stay stale for ~100s after a restart.** 1.2.1 restored
  the known-device registry so the first poll could reach every device by
  unicast, but setup still awaited `async_start_browser`, which slept
  `MDNS_TIMEOUT * 3` (15s) before the first refresh. That sleep runs during HA's
  bootstrap, when the event loop is starved (14-22s gaps between MainThread log
  lines, websocket clients "unable to keep up"), and it measured 97-99s on two
  harbr2 restarts. With a restored registry the browser now starts without
  waiting; mDNS results fill in on later polls. A first install with no registry
  still waits, since discovery is its only source of devices.

## [1.3.0] - 2026-10-21

Merges community PRs #4 and #5 (thanks @salanki), plus the two fixes that make
them safe together.

### Added
- **Recovery of incomplete ARC discovery** (PR #4). When mDNS reports a device
  through CMC/DBC but misses its ARC routing service, the integration resolves
  the ARC record explicitly (advertised port, IPv4 only, at most four devices
  per poll, 3s timeout, once per device per minute) instead of polling a device
  it cannot address. Until recovery succeeds the device's entities are
  unavailable and no unicast miss is counted. mDNS `Updated` events are now
  accepted alongside `Added`.
- **Unit tests run in CI** (PRs #4/#5): a `tests` job in `validate.yml` with
  `tests/requirements.txt`.

### Fixed
- **Known devices whose ARC record never recovers are expired after an hour.**
  PR #4 stops ARC-less records from consuming the miss budget, which left
  nothing to ever remove them: a decommissioned or renamed device would be
  retried every minute forever. `ARC_RECOVERY_GIVE_UP` (3600s) now drops it from
  the registry; a live device comes back through normal discovery.
- **Stored channel aliases are dropped when the registry loads.** A store
  written by 1.2.1 can already hold `Left@Kitchen`-style aliases. Without this,
  they were restored and polled until the miss limit expired them -- and with
  PR #4's no-miss rule for ARC-less records, they would never have expired.
- `tests/test_aes67_frames.py` imports the coordinator as a package again; on
  1.2.2 the whole suite failed at collection with a relative-import error.
- Filter friendly per-channel mDNS names such as `Left@Kitchen` and
  `Right@Office` alongside numeric names such as `01@Kitchen`. These aliases
  are not controllable devices; resolving and polling them caused repeated
  eleven-cycle unreachable warnings and known-device registry churn.

## [1.2.2] - 2026-10-17

### Fixed
- **Home Assistant no longer exits dirty because of this integration.** Every HA
  shutdown ended in `Found 5 non-daemonic threads` with a `SystemExit`
  traceback out of `_discover_sap_streams`, and the container wrapper turned
  that into `exit code 100` -- which s6 reads as "restart me". So a single
  restart request became a slow, messy restart, and the dirty exit was
  indistinguishable in the logs from a crash.
- Root cause: two blocking executor jobs with no way to be interrupted.
  `select()` cannot be cancelled, so the SAP listen loop held a worker thread
  for up to `SAP_TIMEOUT` (10s) after HA asked to stop; and the PHASE 3 poll
  loop kept queueing a fresh blocking unicast query per known device (~20 here)
  while shutdown was already underway.
- The coordinator now owns a `threading.Event` set on `EVENT_HOMEASSISTANT_STOP`
  (and on unload, via `async_stop_browser`), checked in four places: the SAP
  loop condition, before queueing SAP discovery at all, and at the top of the
  per-device poll loop. The SAP `select()` wait is also capped by the new
  `SAP_SELECT_SLICE` (0.5s) instead of 1.0s -- since `select()` is
  uninterruptible, that slice IS the worst-case shutdown delay, so it is now
  0.5s rather than 10s.

### Changed
- A SAP packet that fails to parse is logged at debug instead of being swallowed
  by a bare `except Exception: pass`. One bad packet still must not abort the
  listen window, but the silence was hiding real faults.

### Verified
Measured on harbr2 across two `homeassistant.restart` cycles (the first loads
this code, the second exercises it -- custom components are only imported at
startup, so the restart that installs a fix can never also test it):

| shutdown signature | before | after |
| --- | --- | --- |
| `Thread[...] is still running at shutdown` | 1 | 0 |
| `SystemExit` | 1 | 0 |
| `_discover_sap_streams` frames | 2 | 0 |
| `custom_components/dante` frames | 3 | 0 |

Worth recording: the dirty shutdown on the *first* cycle was held by
`SyncWorker_19` running `netaudio ... subscription status` -- the PHASE 3 device
poll, NOT the SAP loop that the original traceback named. Fixing only the
function in the traceback would have left the bug in place.

Not fixed by this release, and not ours: HA still logs `Found 5 non-daemonic
threads` and shutdown still takes ~40s. Those belong to `pyhik.hikvision`
(a long-lived non-daemon `alert_stream` thread) and to three ESPHome
`Timeout waiting for DisconnectResponse after 10.0s` handshakes, which now
dominate the shutdown. Also note `exit code 100` is the container wrapper's
*correct* "restart me" signal, not a fault -- the defect was the thread state
and traceback accompanying it.

## [1.2.1] - 2026-08-31

### Fixed
- **A restart no longer leaves the integration with no entities until mDNS
  catches up.** The known-device registry is now persisted (`helpers.storage`)
  and restored *before* the first refresh, so the first poll reaches every
  previously-known device by direct unicast. This matches the coordinator's own
  design -- mDNS is only how a device is *discovered*; once known it is polled by
  unicast -- and makes startup independent of discovery timing.

  Root cause: the mDNS browse is unreliable **during Home Assistant's startup
  window**, not because of interface binding. The integration sets up early in
  the bootstrap and browses for 15s while hundreds of integrations initialize.
  Observed on a host whose bootstrap takes 402s: one boot's browse reported
  `0 services found initially` and produced no discovery callbacks for 2m14s;
  another found 35 of 142. A config-entry reload against the *same* shared
  zeroconf instance, once HA had settled, found 142 instantly.

  Interface binding was investigated and ruled out: browsing the four
  `_netaudio._udp` types on the adapters HA is configured to use returns all 142
  services in under 2s. (An earlier draft of this note blamed multicast interface
  selection. That was wrong -- it came from a test using python-zeroconf's bare
  `Zeroconf()`, whose default is the default interface only, which is not what
  Home Assistant uses.)

  With the registry restored, a boot whose browse found only 35 services still
  had all 106 RX entities available 58s after start, while HA was still six
  minutes from finishing its own bootstrap.

  The store is written at most once per poll cycle and only when the registry
  actually changed (device added, IP changed, display name resolved, or purged).
  A stale entry is self-correcting: the existing `DEVICE_MISS_LIMIT` logic purges
  a device that has gone away, and mDNS still fixes a changed IP. A corrupt or
  unreadable store is logged and ignored, never blocking setup.

## [1.2.0] - 2026-08-31

Contributed fixes for RX subscription routing (thanks @salanki), plus a
follow-up so the two compose correctly.

### Fixed
- **RX selects no longer go "unknown" when a TX device's friendly channel names
  differ from its canonical ones** (#1). Autonomic media servers report a
  subscription's channel as `Left`/`Right` while a direct query names the same
  channels `01`/`02`, so the state string matched no option. `current_option`
  now resolves the friendly name back to the canonical one.
- **Routing an RX channel no longer requires the TX source to be reachable
  over control** (#3). The on-wire subscribe command is built purely from the
  RX channel number and the TX device/channel name strings, so only the
  *receiver* must be live. A persistent `_tx_catalog` keeps a source's options
  available across a transient control-poll outage (previously the options
  vanished after `DEVICE_MISS_LIMIT` misses and HA rejected a still-valid
  option), and `add_subscription_by_name()` routes without a live TX object.
- **Friendly-name resolution now survives that same outage.** As merged, #1
  resolved friendly names from live coordinator data — the very data #3 exists
  because it disappears — so a purged Autonomic source went back to "unknown".
  Resolution now reads the persistent catalog instead, which also carries each
  channel's `friendly_name`. New `DanteDataUpdateCoordinator.resolve_tx_option()`.

- **Setting an RX subscription no longer blocks on a full coordinator refresh.**
  `async_select_option` awaited `async_request_refresh()`, which re-polls every
  device on the Dante network -- measured at **10.3s** cold. An automation
  setting several RX channels in sequence paid that per call. The refresh now
  runs as a background task; the optimistic `_pending_option` already reflects
  the new state immediately. The sample-rate select deliberately keeps its
  awaited refresh: it has no optimistic state, so backgrounding it would leave
  the UI showing a stale rate. (from #2)

### Added
- `dante.add_subscription` accepts an optional `tx_channel_name` to route to a
  transmitter that has not been seen yet.

### Notes
- TX options are now de-duplicated, and persist for the coordinator's lifetime:
  a source removed from the network keeps its options until the integration is
  reloaded. This matches how AES67 stream options already behave.

## [1.1.1] - 2026-08-28

Verification method challenged and **confirmed by experiment**; second teardown
opcode documented. No behaviour change.

### Confirmed
- **The `0x3000` status pair is a stored subscription, not a liveness signal.** It
  was argued that `0101/000e` might mean "multicast is arriving right now", which
  would have made the verification method useless — a `0000` could not then
  distinguish a rejected command from a stream that simply was not flowing.
  Disproved directly: a channel subscribed to a NAX player that is **stopped and
  transmitting nothing** still reports `0101/000e`. Nothing is arriving, so the
  field cannot be reporting arrival. `0x3200`'s reply length also grows per
  subscription (16B / 100B / 184B for 0 / 1 / 2 channels) — a record count.
- **`0x3010` is a correct teardown.** Tested head to head against Controller's
  `0x3410` with two channels subscribed on one device: `0x3010` cleared ch1,
  `0x3410` cleared ch2, each confirmed `0101/000e` → `0000/0000`. Both work.

### Documented
- **`0x3410`** — Controller's own teardown (36 bytes, channel uint16 at offset 20).
  We continue to send `0x3010`; `0x3410` is recorded so Controller's different
  choice is not later mistaken for evidence that ours is wrong.
- `0x3400` / `0x3600` noted as unexamined read-shaped opcodes.
- The "apparent uncommanded state changes" that prompted the challenge were writes
  from another host: a capture on the Controller machine cannot see unicast
  traffic between a HA host and a device. **"No writes in my capture" only ever
  means "none from my vantage point."**

## [1.1.0] - 2026-08-28

AES67 subscribe/unsubscribe rebuilt from Dante Controller packet captures. Before
this release, AES67 routing worked only on the hardware it was reverse-engineered
against, and could never be torn down on any hardware.

### Fixed

- **The AES67 subscribe frame was malformed, so newer devices ACKed it and did
  nothing.** The old frame was 112 bytes with the channel selector at offset 96
  and the codec/port/group tail 8 bytes late. Controller's real frame is **104
  bytes** and carries a **per-rx-channel uint16 map at offset 42** (`entry[i]` =
  the flow channel feeding rx channel *i+1*, `0` = leave alone). Rebuilt on that
  layout; `tests/test_aes67_frames.py` pins it byte-for-byte against the capture.

  Symptom: a room could not be tuned at all. The device replied, the integration
  reported success, and no audio ever arrived.

- **Unsubscribe never tore down an AES67 flow — on any device.** The
  `SUBSCRIPTION_NONE` path called the *native Dante* `remove_subscription`, which
  does not touch an AES67 flow. Controller does not use `0x3201` to unsubscribe at
  all; it sends **`0x3010`, 52 bytes, one per channel**. A flow cleared the old
  way was still running four hours later while HA reported the channel clear —
  silence in the UI, sound in the room. Now sends the real teardown, and **refuses
  to report a channel clear if the teardown fails** rather than lying about it.

  Because the lifecycle is per-channel, a stereo flow needs a teardown per
  channel; clearing one leaves the other holding the flow up.

- **Reply validation judged success on the wrong field.** The start code is
  **echoed** by the device and carries no meaning — the same request under
  `0x2729`/`0x2809`/`0x2801` returns byte-identical payloads, and some firmware
  normalises it while other firmware echoes verbatim. Validation now checks the
  echoed sequence number (so a stale datagram cannot read as success), the echoed
  opcode, and the status word at offset 8.

- **The per-channel subscription status code was parsed and then discarded.**
  `device.py` set `channel_status_text = None` unconditionally, making the branch
  below it dead code, so the device's own account of why a subscription was or
  was not flowing never reached a log or an entity.

### Added

- **`_build_aes67_unsubscribe_command`** — the real `0x3010` teardown.
- **`tools/aes67_verify.py`** — query a device's actual flow state. The subscribe
  reply cannot be trusted; this reads the device instead.
- **`tests/test_aes67_frames.py`** — pins subscribe (ch1/ch2) and teardown
  (ch1/ch2) to the canonical Controller captures so the frames cannot silently
  drift again.

### How to verify an AES67 subscription

Never trust the subscribe reply. Read the device:

- **`0x3200`** — binary liveness. Subscribed → ~100-byte reply; unsubscribed →
  16-byte reply. Read-only and safe on a device playing audio (Controller polls
  it constantly).
- **`0x3000`** — per-channel detail. Records are 20 bytes (10 x uint16) from byte
  12; `[0]=ch_num [5]=name_off [6]=rx_status [7]=sub_status`.

  | `rx_status`/`sub_status` | meaning |
  |---|---|
  | `0x0000`/`0x0000` | no flow |
  | `0x0100`/`0x000e` | **transient**, establishing — re-poll, do not call it failed |
  | `0x0101`/`0x000e` | flow up |

  `0x000e` means a flow **is present**. An AES67 flow never populates
  `tx_device_name`, so it never appears as a normal Dante subscription — which is
  why HA showed nothing while audio played.

### Verified

End to end on real hardware: subscribe → device reports `0101/000e` on both
channels, audio confirmed by ear; teardown → both channels `0000/0000`, flow
gone. The teardown was exercised on a live flow, not just a no-op.



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
- **Our unsubscribe does not tear down an AES67 flow.** `select.py`'s `SUBSCRIPTION_NONE` path calls `device.remove_subscription(rx_ch)`, which is a *native Dante* unsubscribe. Confirmed 2026-08-28: a test AES67 flow was set on `danterbr17-villa-club-room` ch1, then cleared to "None" (HA reported `verified_state: "None"`) — and four hours later both `0x3000` (`0101/000e`) and `0x3200` (100-byte reply) still showed the flow running. The consequence is that `off` is as broken as `on` for any AES67-routed room: `crestron-nax`'s `room_off` unsubscribes, reports success, and the audio keeps flowing. Needs Controller's teardown bytes, same as the subscribe.

- **`_build_aes67_subscribe_command` does not drive every Dante model.** It was reverse-engineered from captures of one device. `danterbr20-villa-office` recognises opcode `0x3201` (it does not return the `... 0030` unknown-command error) but answers with a 14-byte frame instead of the 108-byte record, returns a byte-identical reply even for a nonsense `rx_channel`, and never establishes the flow. The same device subscribes and plays fine from Dante Controller, so the device is not at fault.

  ⚠ The **reply start code is not a protocol marker** — the device *echoes* it. The same `0x3000` read sent under `0x2729`, `0x2809` and `0x2801` returns byte-identical payloads per device; `.89` normalises `0x2809` to `0x2801` in its reply while `.95` echoes verbatim. That is a cosmetic firmware difference with no functional meaning, and an earlier attempt to treat the `0x2809` reply as the bug (and then as success) was wrong on both counts. Dante Controller itself uses `0x2729`.

### How to verify an AES67 subscription
The subscribe ACK cannot be trusted. Use the device's own state:

- **`0x3200`** — cheap binary liveness check. Subscribed → ~100-byte reply; unsubscribed → 16-byte reply (`02 00 00 00 00 00`). Read-only and safe on a device playing audio (Dante Controller polls it ~9x per 12s).
- **`0x3000`** — per-channel detail. Records are 20 bytes (10 × uint16) starting at byte 12 of the reply; `[0]=ch_num [5]=name_off [6]=rx_status [7]=sub_status`. See the status pairs documented in `netaudio/device.py`. Require `0x0101` and re-poll: `0x0100/0x000e` is a real transient mid-establish state and a single poll can misread it as failure.
