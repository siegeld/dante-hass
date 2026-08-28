# Changelog

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
