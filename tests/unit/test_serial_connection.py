import asyncio

import pytest

from meshcore.serial_cx import AttachedSerialConnection, SerialConnection


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


# --------------------------------------------------------------------------
# AttachedSerialConnection -- see beebo's debug_link.py for the real owner
# --------------------------------------------------------------------------

class RecordingOwner:
    def __init__(self):
        self.attached = None
        self.detached = None
        self.written = []

    def attach(self, sink):
        self.attached = sink

    def detach(self, sink):
        self.detached = sink

    def write_raw(self, data):
        self.written.append(data)


@pytest.mark.asyncio
async def test_attached_connect_registers_as_owners_sink():
    owner = RecordingOwner()
    conn = AttachedSerialConnection(owner, "/dev/ttyACM0")

    result = await conn.connect()

    assert result == "/dev/ttyACM0"
    assert owner.attached is conn


@pytest.mark.asyncio
async def test_attached_disconnect_detaches_from_owner():
    owner = RecordingOwner()
    conn = AttachedSerialConnection(owner, "/dev/ttyACM0")
    await conn.connect()

    await conn.disconnect()

    assert owner.detached is conn


@pytest.mark.asyncio
async def test_attached_send_writes_framed_packet_via_owner():
    owner = RecordingOwner()
    conn = AttachedSerialConnection(owner, "/dev/ttyACM0")

    await conn.send(b"\x01\x02\x03")

    assert owner.written == [b"\x3c\x03\x00\x01\x02\x03"]


@pytest.mark.asyncio
async def test_attached_handle_rx_forwards_to_reader():
    owner = RecordingOwner()
    conn = AttachedSerialConnection(owner, "/dev/ttyACM0")
    reader = RecordingReader()
    conn.set_reader(reader)

    conn.handle_rx(b"\x00\x01\x02\x53")
    await asyncio.sleep(0)

    assert reader.frames == [b"\x00\x01\x02\x53"]


@pytest.mark.asyncio
async def test_attached_handle_rx_no_reader_does_not_raise():
    owner = RecordingOwner()
    conn = AttachedSerialConnection(owner, "/dev/ttyACM0")

    conn.handle_rx(b"\x00\x01")  # must not raise


@pytest.mark.asyncio
async def test_attached_notify_disconnected_invokes_callback():
    owner = RecordingOwner()
    conn = AttachedSerialConnection(owner, "/dev/ttyACM0")
    seen = []

    async def _cb(reason):
        seen.append(reason)

    conn.set_disconnect_callback(_cb)
    conn.notify_disconnected("debug_link_lost")
    await asyncio.sleep(0)

    assert seen == ["debug_link_lost"]


@pytest.mark.asyncio
async def test_attached_notify_disconnected_no_callback_does_not_raise():
    owner = RecordingOwner()
    conn = AttachedSerialConnection(owner, "/dev/ttyACM0")

    conn.notify_disconnected("debug_link_lost")  # must not raise
