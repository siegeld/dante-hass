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
- **Our unsubscribe does not tear down an AES67 flow.** `select.py`'s `SUBSCRIPTION_NONE` path calls `device.remove_subscription(rx_ch)`, which is a *native Dante* unsubscribe. Confirmed 2026-08-28: a test AES67 flow was set on `danterbr17-villa-club-room` ch1, then cleared to "None" (HA reported `verified_state: "None"`) — and four hours later both `0x3000` (`0101/000e`) and `0x3200` (100-byte reply) still showed the flow running. The consequence is that `off` is as broken as `on` for any AES67-routed room: `crestron-nax`'s `room_off` unsubscribes, reports success, and the audio keeps flowing. Needs Controller's teardown bytes, same as the subscribe.

- **`_build_aes67_subscribe_command` does not drive every Dante model.** It was reverse-engineered from captures of one device. `danterbr20-villa-office` recognises opcode `0x3201` (it does not return the `... 0030` unknown-command error) but answers with a 14-byte frame instead of the 108-byte record, returns a byte-identical reply even for a nonsense `rx_channel`, and never establishes the flow. The same device subscribes and plays fine from Dante Controller, so the device is not at fault.

  ⚠ The **reply start code is not a protocol marker** — the device *echoes* it. The same `0x3000` read sent under `0x2729`, `0x2809` and `0x2801` returns byte-identical payloads per device; `.89` normalises `0x2809` to `0x2801` in its reply while `.95` echoes verbatim. That is a cosmetic firmware difference with no functional meaning, and an earlier attempt to treat the `0x2809` reply as the bug (and then as success) was wrong on both counts. Dante Controller itself uses `0x2729`.

### How to verify an AES67 subscription
The subscribe ACK cannot be trusted. Use the device's own state:

- **`0x3200`** — cheap binary liveness check. Subscribed → ~100-byte reply; unsubscribed → 16-byte reply (`02 00 00 00 00 00`). Read-only and safe on a device playing audio (Dante Controller polls it ~9x per 12s).
- **`0x3000`** — per-channel detail. Records are 20 bytes (10 × uint16) starting at byte 12 of the reply; `[0]=ch_num [5]=name_off [6]=rx_status [7]=sub_status`. See the status pairs documented in `netaudio/device.py`. Require `0x0101` and re-poll: `0x0100/0x000e` is a real transient mid-establish state and a single poll can misread it as failure.
