# dante-hass — architecture, protocol notes and war stories

Home Assistant custom component for Audinate **Dante** audio networks. Discovers
devices by mDNS, then polls each one directly by IP, and exposes routing +
device control as HA entities.

**Where it runs:** `/srv/docker/hassio/git/dante-hass`, symlinked into the HA
config as `custom_components/dante`. HA itself is the `home-assistant` container
on **harbr2**. The Dante fabric is **VLAN 1007 / 10.11.7.0/24**; harbr2 reaches it
on `eth5` (10.11.7.81).

**Versioned by `manifest.json`**, not a `VERSION` file — it is a HACS component,
not a built service. Bump `version` there, tag `vX.Y.Z`, push both remotes.

## Layout

```
custom_components/dante/
  coordinator.py     discovery, polling, SAP/AES67 stream table, the AES67 frames
  entity.py          DanteEntity base (CoordinatorEntity)
  select.py          RX subscription selects  <- the AES67 subscribe/teardown path
  sensor.py number.py switch.py button.py
  netaudio/          vendored Dante protocol library (device.py does the parsing)
tools/aes67_verify.py   read a device's REAL flow state
tests/test_aes67_frames.py  pins the wire frames to Controller captures
```

## The protocol, as actually observed (UDP/4440)

Two request families share the port. They are **not** distinguished by the start
code — see the war stories.

| Opcode | What |
|---|---|
| `0x1002` | device name |
| `0x1000` | device info |
| `0x2000` / `0x2010` | TX channels |
| `0x3000` | RX channels **+ subscription status** |
| `0x3200` | AES67 flow liveness (binary: subscribed ≈100B reply, not 16B) |
| `0x3201` | **AES67 subscribe** — 104 bytes |
| `0x3010` | **AES67 teardown** — 52 bytes, **one per channel** |

Unknown opcode returns a 10-byte `<start> 000a <seq> <opcode> 0030` from every
device, so offset 8 carries a status/error code and `0x0030` = unknown command.

### Verifying a subscription — never trust the ACK

`0x3000` records are 20 bytes (10 x uint16) from byte 12:
`[0]=ch_num [5]=name_off [6]=rx_status [7]=sub_status`.

| `rx_status`/`sub_status` | meaning |
|---|---|
| `0x0000`/`0x0000` | no flow |
| `0x0100`/`0x000e` | transient, establishing — **re-poll**, do not call it failed |
| `0x0101`/`0x000e` | flow up |

`tools/aes67_verify.py <ip>` does this. Use `0x3200` for a cheap binary check.

## War stories — read before touching the AES67 path

**1. The start code is ECHOED. It means nothing.** The same `0x3000` read sent
under `0x2729`, `0x2809` and `0x2801` returns byte-identical payloads per device;
some firmware normalises it in the reply, other firmware echoes verbatim. Hours
were lost building a theory on `0x2801` vs `0x2809` being a protocol family
marker, then "fixing" it by accepting a `0x2809` reply as success — which was
worse, because it turned a loud failure into a silent one while Dante Controller
showed no subscription had ever been established. **Judge success on the echoed
sequence number, the echoed opcode, and the status word at offset 8. Never on the
start code.**

**2. `0x000e` means a flow IS PRESENT, not "idle".** This was guessed wrong twice,
in opposite directions, before a capture settled it. An AES67 flow never populates
`tx_device_name`, so it never builds a normal subscription object and HA shows
nothing for it — that absence is not evidence of no flow.

**3. The native Dante unsubscribe does not remove an AES67 flow.** A flow cleared
with `remove_subscription` alone was still running four hours later while HA
reported the channel clear. If the teardown fails, **do not report the channel
clear** — reporting "off" while audio keeps playing is what hid this for hours.

**4. The lifecycle is PER CHANNEL.** Controller sends one `0x3201` per channel and
one `0x3010` per channel. Tearing down only the channel the UI targeted leaves the
other holding the flow up.

**5. Frames were reverse-engineered against ONE device and did not generalise.**
The old 112-byte subscribe drove older hardware but was ACKed-and-ignored by a
newer unit. If a device recognises the opcode (no `0030` error) but nothing
happens, suspect the frame layout, not the device. `tests/test_aes67_frames.py`
exists so this cannot silently regress — it pins every frame to a real capture.

**6. You cannot capture Controller's traffic from harbr2.** Controller talks to
the device over **unicast**, so it never reaches another host's port. Capture on
the Controller machine itself. IGMP is no help either: snooping keeps membership
reports on router ports, so harbr2 sees zero IGMP for a device that is actively
receiving a flow.

**7. Not every device exposes gain.** The `gain_ch_*` entities come from the Dante
protocol (Audinate-style 1..5 step). A device that does not report those fields
has no software gain at all — attenuate upstream or at the sink instead.

## Gotchas

- **`update_before_add=True` breaks setup at scale** — it forces a full coordinator
  refresh per entity and blows the config-entry setup budget. Removed; duplicates
  are prevented by `_attr_unique_id` + the coordinator's known-devices set.
- **Renaming a device in Dante Controller is a CREATE, not a rename.** Everything
  keys off the Dante network name (`('dante', name)` identifiers, `dante_{name}_{key}`
  unique ids), so a rename mints a whole new HA device and orphans the old one.
  Every consumer must be repointed by hand.
- **Code changes need an HA restart**, not a config-entry reload — Python modules
  are already imported.
