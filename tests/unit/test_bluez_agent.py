"""The BlueZ pairing agent answers with the PIN it was given."""

import pytest

from meshcore import bluez_agent

pytestmark = pytest.mark.skipif(not bluez_agent.DBUS_AVAILABLE, reason="needs dbus-fast on Linux")


def test_agent_answers_pin_and_passkey_requests():
    agent = bluez_agent._Agent("123456")
    assert agent.pin_code() == "123456"
    assert agent.passkey() == 123456
    assert agent.answered == 2


def test_agent_accepts_confirmation():
    agent = bluez_agent._Agent("000001")
    agent.accept()
    assert agent.answered == 1


def test_pin_agent_without_a_pin_is_a_no_op():
    import asyncio

    async def go():
        async with bluez_agent.PinAgent(None) as a:
            return a.answered
    assert asyncio.run(go()) == 0
