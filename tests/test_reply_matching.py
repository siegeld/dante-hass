"""A late reply must never be taken as the answer to the next request."""

import socket
import threading

import pytest

from custom_components.dante.netaudio.device import DanteDevice

NAME_REPLY = bytes.fromhex("27ff0016aaaa10020000") + b"Kitchen-e \x00"


def pair():
    device_side = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    device_side.bind(("127.0.0.1", 0))
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.bind(("127.0.0.1", 0))
    client.settimeout(0.3)
    client.connect(device_side.getsockname())
    device_side.connect(client.getsockname())
    device = DanteDevice(server_name="Kitchen")
    port = device_side.getsockname()[1]
    device.sockets[port] = client
    return device, device_side, client, port


def reply_for(request: bytes, body: bytes = b"\x00\x00ok") -> bytes:
    return request[:2] + bytes([0, 8 + len(body)])[:2] + request[4:8] + body


@pytest.mark.asyncio
async def test_stale_reply_is_skipped_and_matching_reply_returned():
    device, device_side, client, port = pair()
    request = "27ff0010" + "1234" + "3000" + "0000000100010000"
    # A reply to an earlier, timed-out request is already queued.
    device_side.send(NAME_REPLY)

    def answer():
        sent = device_side.recv(2048)
        device_side.send(reply_for(sent))

    t = threading.Thread(target=answer)
    t.start()
    response = await device.dante_command(request, port=port)
    t.join()

    assert response[4:8] == bytes.fromhex("12343000")
    assert client.gettimeout() == 0.3


@pytest.mark.asyncio
async def test_only_stale_replies_returns_none():
    device, device_side, client, port = pair()
    device_side.send(NAME_REPLY)
    response = await device.dante_command(
        "27ff0010" + "1234" + "3000" + "0000000100010000", port=port
    )
    assert response is None
    assert client.gettimeout() == 0.3


@pytest.mark.asyncio
async def test_non_arc_frames_take_the_next_datagram():
    device, device_side, client, port = pair()
    device_side.send(b"\xff\xff\x00\x08anything")
    response = await device.dante_command("ffff000c" + "0000" + "0000", port=port)
    assert response == b"\xff\xff\x00\x08anything"
