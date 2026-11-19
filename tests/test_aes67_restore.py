"""A persisted AES67 selection comes back only if the device still carries it.

The bug this pins (2026-11-18): after an HA restart or a reload of the entry, an
AES67-fed RX select read "None" while the receiver kept playing the flow --
danterbr5 read 0101/000e on both channels. The native subscription list never
contains an AES67 flow, so the old reconcile could not see it.
"""
from custom_components.dante.coordinator import restore_aes67_selections

OPT_L = "[AES67] MediaStreamNax1Player1 - Tx Left"
OPT_R = "[AES67] MediaStreamNax1Player1 - Tx Right"
DEV = "danterbr5-david-study-denon"


def _poll(ch1, ch2, subs=()):
    return {DEV: {
        "rx_channels": {
            1: {"name": "CH1", "rx_status": ch1[0], "sub_status": ch1[1]},
            2: {"name": "CH2", "rx_status": ch2[0], "sub_status": ch2[1]},
        },
        "subscriptions": list(subs),
    }}


CANDS = {(DEV, 1): OPT_L, (DEV, 2): OPT_R}
KNOWN = {OPT_L, OPT_R}


def test_live_flow_is_adopted():
    adopt, drop = restore_aes67_selections(
        CANDS, _poll((0x0101, 0x000E), (0x0100, 0x000E)), KNOWN)
    assert adopt == CANDS and not drop


def test_torn_down_flow_is_dropped():
    adopt, drop = restore_aes67_selections(
        CANDS, _poll((0, 0), (0, 0)), KNOWN)
    assert not adopt and drop == set(CANDS)


def test_native_subscription_wins():
    subs = [{"rx_channel_name": "CH1", "tx_device_name": "danterbr3-villa-sonos"}]
    adopt, drop = restore_aes67_selections(
        CANDS, _poll((0x0101, 0x0009), (0x0101, 0x000E), subs), KNOWN)
    assert drop == {(DEV, 1)} and adopt == {(DEV, 2): OPT_R}


def test_waits_for_sap_and_for_a_silent_device():
    # SAP not filled yet: the option would read "unknown", so keep waiting.
    adopt, drop = restore_aes67_selections(
        CANDS, _poll((0x0101, 0x000E), (0x0101, 0x000E)), set())
    assert not adopt and not drop
    # The device did not answer this poll: no verdict either way.
    adopt, drop = restore_aes67_selections(CANDS, {}, KNOWN)
    assert not adopt and not drop
