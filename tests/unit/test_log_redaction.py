"""The node's private key never reaches the debug log: not when exported
(PRIVATE_KEY response), not when imported (IMPORT_PRIVATE_KEY command), not
in the dispatched event."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from meshcore.commands import CommandHandler
from meshcore.events import Event, EventDispatcher, EventType
from meshcore.reader import MessageReader
from meshcore.serial_cx import SerialConnection

pytestmark = pytest.mark.asyncio

KEY = bytes(range(0xA0, 0xE0))   # 64 recognizable bytes


async def test_private_key_response_is_not_logged(caplog):
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock()
    reader = MessageReader(dispatcher)
    with caplog.at_level(logging.DEBUG, logger="meshcore"):
        await reader.handle_rx(bytearray(bytes([14]) + KEY))
    assert KEY.hex() not in caplog.text
    assert dispatcher.dispatch.await_args[0][0].payload["private_key"] == KEY


async def test_private_key_event_payload_is_not_logged(caplog):
    dispatcher = EventDispatcher()
    await dispatcher.start()
    try:
        with caplog.at_level(logging.DEBUG, logger="meshcore"):
            await dispatcher.dispatch(Event(EventType.PRIVATE_KEY, {"private_key": KEY}))
            await asyncio.sleep(0.05)
    finally:
        await dispatcher.stop()
    assert "Dispatching event" in caplog.text
    assert repr(KEY) not in caplog.text and KEY.hex() not in caplog.text


async def test_private_key_import_command_is_not_logged(caplog):
    handler = CommandHandler()
    sent = []

    async def sender(data):
        sent.append(data)
    handler._sender_func = sender
    handler.dispatcher = MagicMock()
    with caplog.at_level(logging.DEBUG, logger="meshcore"):
        await handler.send(b"\x18" + KEY)
    assert sent == [b"\x18" + KEY]
    assert KEY.hex() not in caplog.text


async def test_private_key_import_frame_is_not_logged_by_the_serial_link(caplog):
    cx = SerialConnection("/dev/null", 115200)
    cx.transport = MagicMock()
    with caplog.at_level(logging.DEBUG, logger="meshcore"):
        await cx.send(b"\x18" + KEY)
    frame = cx.transport.write.call_args[0][0]
    assert frame.endswith(KEY)
    assert str(KEY) not in caplog.text and repr(frame) not in caplog.text
