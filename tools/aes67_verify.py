#!/usr/bin/env python3
"""Verify an AES67 subscription on a Dante device WITHOUT trusting the subscribe ACK.

Queries native opcode 0x3000 (rx channels, 0x27ff request family) and reads the
per-channel status pair. Layout and meaning derived 2026-08-28 from a packet
capture that straddled a manual Dante Controller subscribe on
danterbr20-villa-office (10.11.7.95), audio confirmed by ear:

  reply: 27ff <len> <seq> 3000 0001 0202 | <20-byte rx record> * n | 0000 bb80 ... labels
  record: ch_num, ?, ?, ch_off, dev_off, name_off, rx_status, sub_status, ?, ?

  rx_status/sub_status  0x0000/0x0000 -> no AES67 flow
                        0x0101/0x000e -> AES67 flow subscribed and running
"""
import socket, struct, sys

ACTIVE = (0x0101, 0x000e)

def q3000(ip, seq=0x7a01):
    req = struct.pack(">HHHHHHHH", 0x27ff, 0x0010, seq, 0x3000, 0x0000, 0x0001, 0x0001, 0x0000)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2.0)
    try:
        s.sendto(req, (ip, 4440)); r, _ = s.recvfrom(4096); return r
    except socket.timeout: return None
    finally: s.close()

def parse(resp):
    if not resp or len(resp) < 32: return []
    out, off = [], 12                      # magic,len,seq,opcode,0001,0202
    while off + 20 <= len(resp):
        f = struct.unpack_from(">10H", resp, off)
        if f[0] == 0 or f[0] > 64: break   # channel numbers are 1..n
        out.append({"ch": f[0], "name_off": f[5], "rx": f[6], "sub": f[7]})
        off += 20
    return out

def label(resp, off):
    if not off or off*2 >= len(resp): return ""
    end = resp.find(b"\x00", off*2)
    return resp[off*2:end].decode("ascii", "ignore") if end > 0 else ""

rc = 0
for name, ip in (("danterbr20-villa-office", "10.11.7.95"), ("danterbr17-villa-club-room", "10.11.7.89")):
    r = q3000(ip)
    chans = parse(r)
    if not chans:
        print(f"{name:28s} no/short reply"); rc = 1; continue
    for c in chans:
        active = (c["rx"], c["sub"]) == ACTIVE
        print(f"{name:28s} ch{c['ch']} {label(r, c['name_off']):14s} "
              f"rx=0x{c['rx']:04x} sub=0x{c['sub']:04x}  "
              f"{'AES67 FLOW ACTIVE' if active else 'no flow'}")
sys.exit(rc)
