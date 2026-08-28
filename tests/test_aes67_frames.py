"""Pin the AES67 subscribe/unsubscribe frames to real Dante Controller captures.

Ground truth captured 2026-08-28 on David's workstation while Dante Controller
drove danterbr20-villa-office (10.11.7.95) through a full unsubscribe ->
subscribe-ch1 -> subscribe-ch2 sequence. Both subscribes verifiably established
the flow (audio confirmed by ear; 0x3000 read 0101/000e afterwards).

These tests exist because the PREVIOUS builder was 112 bytes with the channel
selector at offset 96 and the tail 8 bytes late, and it silently failed on
newer hardware while appearing to succeed. Do not change the builders without
re-running this.
"""
import struct, socket, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "dante"))
from coordinator import DanteDataUpdateCoordinator as C

CTRL_SUB_CH1 = bytes.fromhex(
    "272900680698320100000101001082040000020200000000000000000001000200010060002a002c0030"
    "000100000063000000000800000000000000000300401000000b0a0b07470000000030f09b0600000000"
    "0000000000000000000000000802138cef4555c8")
CTRL_SUB_CH2 = bytes.fromhex(
    "27290068069d320100000101001000000000020200000000000000000001000200010060002a002c0030"
    "000000020000000000000800000000000000000300401000000b0a0b07470000000030f09b0600000000"
    "0000000000000000000000000802138cef4555c8")
CTRL_UNSUB_CH2 = bytes.fromhex(
    "2729003406a73010000002010002" + "00" * 38)

SI = {"origin_ip": "10.11.7.71", "multicast_addr": "239.69.85.200",
      "session_id": 821074694, "port": 5004, "codec": "L24/48000/2", "channels": 2}

def test_subscribe_ch2_matches_controller_exactly():
    got = C._build_aes67_subscribe_command(2, 2, SI, 0x069d, n_rx=2)
    assert got == CTRL_SUB_CH2, f"\n got  {got.hex()}\n want {CTRL_SUB_CH2.hex()}"

def test_subscribe_ch1_matches_controller_but_for_transient_token():
    got = C._build_aes67_subscribe_command(1, 1, SI, 0x0698, n_rx=2, token=0x8204)
    diff = [i for i in range(len(CTRL_SUB_CH1)) if got[i] != CTRL_SUB_CH1[i]]
    # byte 47 is the 3rd slot of a 2-channel map; Controller varies it run to run
    assert diff == [47], f"unexpected diffs at {diff}"

def test_unsubscribe_matches_controller():
    got = C._build_aes67_unsubscribe_command(2, 0x06a7)
    assert got == CTRL_UNSUB_CH2, f"\n got  {got.hex()}\n want {CTRL_UNSUB_CH2.hex()}"

def test_frame_sizes():
    assert len(C._build_aes67_subscribe_command(1, 1, SI, 1)) == 104
    assert len(C._build_aes67_unsubscribe_command(1, 1)) == 52

if __name__ == "__main__":
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v(); print(f"  PASS {k}"); n += 1
    print(f"{n} passed")
