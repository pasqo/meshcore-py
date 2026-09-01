"""
mccli.py : CLI interface to MeschCore BLE companion app
"""

import asyncio
import logging
import serial_asyncio_fast as serial_asyncio

# Get logger
logger = logging.getLogger("meshcore")


class SerialConnection:
    # Inbound frame-size cap. A class/instance attribute (not a hardcoded
    # literal in handle_rx) so a caller that knows its own protocol's real
    # frame-size ceiling -- which meshcore-py has no way to know on its own,
    # since it can change on the firmware side independently -- can raise it
    # via MeshCore.create_serial(..., max_frame_size=...) instead of a fixed
    # guess baked in here going stale. Matches TCPConnection's own
    # max_frame_size mechanism.
    max_frame_size = 300

    def __init__(self, port, baudrate, cx_dly=0.2):
        self.port = port
        self.baudrate = baudrate
        self.transport = None
        self.header = b""
        self.reader = None
        self._disconnect_callback = None
        self.cx_dly = cx_dly
        self._connected_event = asyncio.Event()
        self._background_tasks: set[asyncio.Task] = set()

        self.frame_expected_size = 0
        self.inframe = b""
        self.header = b""
        self._raw_byte_callback = None

    def set_raw_byte_callback(self, callback):
        """Register CALLBACK(bytes) to receive any bytes handle_rx() would
        otherwise silently discard because they aren't part of a companion
        frame envelope (0x3E + 2-byte length + payload) -- e.g. console/
        debug text a radio interleaves on the same UART (see handle_rx()'s
        own comment). None (the default) restores the original discard-
        silently behavior. Never called from more than one place at a time
        (handle_rx runs synchronously), so no locking needed."""
        self._raw_byte_callback = callback

    def _spawn_background(self, coro) -> asyncio.Task:
        """Create a tracked background task (prevents GC of fire-and-forget tasks)."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    class MCSerialClientProtocol(asyncio.Protocol):
        def __init__(self, cx):
            self.cx = cx

        def connection_made(self, transport):
            self.cx.transport = transport
            logger.debug('port opened')
            if isinstance(transport, serial_asyncio.SerialTransport) and transport.serial:
                transport.serial.rts = False  # You can manipulate Serial object via transport
                # A fresh connect can still receive bytes the OS's USB CDC
                # driver had queued from the *previous*, already-closed
                # session before this session's own traffic starts -- the
                # kernel-level CDC ACM input queue isn't guaranteed to be
                # discarded just because the previous process closed its fd.
                # Flush it so those stray bytes can't desync handle_rx's
                # frame parser before the real handshake even starts.
                try:
                    transport.serial.reset_input_buffer()
                except Exception:
                    logger.debug('reset_input_buffer failed', exc_info=True)
            # Defensive: reset our own frame-parser state too, in case any
            # data slipped through before this callback ran.
            self.cx.header = b""
            self.cx.inframe = b""
            self.cx.frame_expected_size = 0
            self.cx._connected_event.set()

        def data_received(self, data):
            self.cx.handle_rx(data)

        def connection_lost(self, exc):
            logger.debug('Serial port closed')
            self.cx._connected_event.clear()

            if self.cx._disconnect_callback:
                self.cx._spawn_background(self.cx._disconnect_callback("serial_disconnect"))

        def pause_writing(self):
            logger.debug("pause writing")

        def resume_writing(self):
            logger.debug("resume writing")

    async def connect(self, timeout: float = 10.0):
        """
        Connects to the device.

        Args:
            timeout: Maximum seconds to wait for connection_made callback.
                     Defaults to 10.0. Raises asyncio.TimeoutError on expiry.
        """
        self._connected_event.clear()

        loop = asyncio.get_running_loop()
        await serial_asyncio.create_serial_connection(
            loop,
            lambda: self.MCSerialClientProtocol(self),
            self.port,
            baudrate=self.baudrate,
        )

        await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)
        logger.info("Serial Connection started")
        return self.port

    def set_reader(self, reader):
        self.reader = reader

    def handle_rx(self, data: bytearray):
        if len(self.header) == 0: # did not find start of frame yet
            # search start of frame (0x3e) in data
            idx = data.find(b"\x3e")
            if idx < 0: # no start of frame
                if self._raw_byte_callback:
                    self._raw_byte_callback(bytes(data))
                return
            # Discard any leading junk bytes before the actual frame marker.
            # Some radios interleave console/debug text on the same UART, so
            # valid companion frames may begin at an offset inside the chunk.
            # (Handed to _raw_byte_callback first if one is registered --
            # see set_raw_byte_callback's own comment -- default behavior is
            # unchanged, still a silent discard.)
            if idx > 0 and self._raw_byte_callback:
                self._raw_byte_callback(bytes(data[:idx]))
            data = data[idx:]
            self.header = data[0:1]
            data = data[1:]

        if len(self.header) < 3: # header not complete yet
            while len(self.header) < 3 and len(data) > 0:
                self.header = self.header + data[0:1]
                data = data[1:]
            if len(self.header) < 3: # still not complete
                return

            # get size and check
            self.frame_expected_size = int.from_bytes(self.header[1:], "little", signed=False)
            if self.frame_expected_size > self.max_frame_size : # invalid size
                # The 3 header bytes (a false 0x3E plus 2 garbage length
                # bytes) turned out not to be a real frame after all --
                # hand them to the callback too before discarding, so a raw
                # text reconstruction doesn't have a 3-byte gap where a
                # literal '>' happened to appear in console output.
                if self._raw_byte_callback:
                    self._raw_byte_callback(self.header)
                # reset inframe
                self.header = b""
                self.inframe = b""
                self.frame_expected_size = 0
                if len(data) > 0: # rerun handle_rx on remaining data
                    self.handle_rx(data)
                return  # nothing left to process after reset

        upbound = self.frame_expected_size - len(self.inframe)
        if len(data) < upbound:
            self.inframe = self.inframe + data
            # frame not complete, wait for next rx
            return

        self.inframe = self.inframe + data[0:upbound]
        data = data[upbound:]
        if self.reader is not None:
            # feed meshcore reader
            self._spawn_background(self.reader.handle_rx(self.inframe))
        # reset inframe
        self.inframe = b""
        self.header = b""
        self.frame_expected_size = 0
        if len(data) > 0: # rerun handle_rx on remaining data
            self.handle_rx(data)

    async def send(self, data):
        if not self.transport:
            logger.error("Transport not connected, cannot send data")
            if self._disconnect_callback:
                await self._disconnect_callback("serial_transport_lost")
            return
        size = len(data)
        pkt = b"\x3c" + size.to_bytes(2, byteorder="little") + data
        logger.debug(f"sending pkt : {pkt}")
        try:
            self.transport.write(pkt)
        except OSError as exc:
            logger.warning(f"Serial write failed: {exc}")
            if self._disconnect_callback:
                await self._disconnect_callback(f"serial_write_failed: {exc}")

    async def disconnect(self):
        """Close the serial connection."""
        if self.transport:
            self.transport.close()
            self.transport = None
            self._connected_event.clear()
            logger.debug("Serial Connection closed")

    def set_disconnect_callback(self, callback):
        """Set callback to handle disconnections."""
        self._disconnect_callback = callback
