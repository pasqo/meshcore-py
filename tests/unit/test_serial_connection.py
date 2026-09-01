import asyncio

import pytest

from meshcore.serial_cx import SerialConnection


class RecordingReader:
    def __init__(self):
        self.frames = []

    async def handle_rx(self, data):
        self.frames.append(bytes(data))


@pytest.mark.asyncio
async def test_handle_rx_discards_leading_junk_before_frame_start():
    conn = SerialConnection("/dev/null", 115200)
    reader = RecordingReader()
    conn.set_reader(reader)

    payload = b"\x00\x01\x02\x53"
    frame = b"\x3e" + len(payload).to_bytes(2, "little") + payload

    conn.handle_rx(b"junk bytes\r\n" + frame)
    await asyncio.sleep(0)

    assert reader.frames == [payload]
    assert conn.header == b""
    assert conn.inframe == b""
    assert conn.frame_expected_size == 0


@pytest.mark.asyncio
async def test_handle_rx_forwards_junk_bytes_to_raw_byte_callback():
    """set_raw_byte_callback() lets a caller (beebo's --dbglog) capture the
    bytes handle_rx() would otherwise silently discard -- console/debug
    text interleaved on the same UART as the companion protocol."""
    conn = SerialConnection("/dev/null", 115200)
    reader = RecordingReader()
    conn.set_reader(reader)
    captured = bytearray()
    conn.set_raw_byte_callback(captured.extend)

    payload = b"\x00\x01\x02\x53"
    frame = b"\x3e" + len(payload).to_bytes(2, "little") + payload

    conn.handle_rx(b"junk bytes\r\n" + frame)
    await asyncio.sleep(0)

    assert reader.frames == [payload]
    assert bytes(captured) == b"junk bytes\r\n"


@pytest.mark.asyncio
async def test_handle_rx_no_callback_registered_still_discards_silently():
    """Default behavior (no callback) is unchanged from before this hook
    existed -- every other meshcore-py caller is unaffected."""
    conn = SerialConnection("/dev/null", 115200)
    reader = RecordingReader()
    conn.set_reader(reader)

    payload = b"\x00\x01\x02\x53"
    frame = b"\x3e" + len(payload).to_bytes(2, "little") + payload

    conn.handle_rx(b"junk bytes\r\n" + frame)
    await asyncio.sleep(0)

    assert reader.frames == [payload]


@pytest.mark.asyncio
async def test_handle_rx_forwards_chunk_with_no_frame_marker_at_all():
    """When a chunk contains no 0x3E at all (pure text, no frame following
    yet in this read), the whole chunk goes to the callback instead of
    being dropped outright."""
    conn = SerialConnection("/dev/null", 115200)
    captured = bytearray()
    conn.set_raw_byte_callback(captured.extend)

    conn.handle_rx(b"MESH_DEBUG: setup() starting\r\n")

    assert bytes(captured) == b"MESH_DEBUG: setup() starting\r\n"


@pytest.mark.asyncio
async def test_handle_rx_forwards_invalid_size_header_bytes_to_callback():
    """A '>' followed by a declared size over max_frame_size isn't a real
    frame header -- just a literal '>' inside text. Those 3 bytes are
    handed to the callback too before being discarded, so a raw-text
    reconstruction doesn't have a gap where this happened."""
    conn = SerialConnection("/dev/null", 115200)
    reader = RecordingReader()
    conn.set_reader(reader)
    captured = bytearray()
    conn.set_raw_byte_callback(captured.extend)

    # declared size (little-endian) far exceeds max_frame_size (300)
    bogus_header = b"\x3e\xff\xff"
    conn.handle_rx(bogus_header + b"more text\n")
    await asyncio.sleep(0)

    assert reader.frames == []
    assert bytes(captured) == bogus_header + b"more text\n"
