"""BlueZ pairing agent that answers PIN/passkey requests itself.

bleak's `client.pair()` hands the request to whichever agent BlueZ has as
default; on a desktop that is the shell's dialog, which waits for a person to
type the PIN. This registers a KeyboardOnly agent for the duration of a pairing
and answers with the PIN it was given. Linux only; elsewhere it is a no-op.
"""

import logging
import sys

logger = logging.getLogger("meshcore")

AGENT_PATH = "/org/meshcore/pin_agent"

try:
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, method
    DBUS_AVAILABLE = sys.platform.startswith("linux")
except ImportError:  # dbus-fast comes with bleak on Linux only
    DBUS_AVAILABLE = False


if DBUS_AVAILABLE:

    class _Agent(ServiceInterface):
        def __init__(self, pin: str):
            super().__init__("org.bluez.Agent1")
            self.pin = str(pin)
            self.answered = 0

        def pin_code(self) -> str:
            self.answered += 1
            return self.pin

        def passkey(self) -> int:
            self.answered += 1
            return int(self.pin)

        def accept(self) -> None:
            self.answered += 1

        @method()
        def Release(self):
            pass

        @method()
        def RequestPinCode(self, device: "o") -> "s":
            return self.pin_code()

        @method()
        def RequestPasskey(self, device: "o") -> "u":
            return self.passkey()

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
            pass

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):
            pass

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):
            self.accept()

        @method()
        def RequestAuthorization(self, device: "o"):
            self.accept()

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):
            pass

        @method()
        def Cancel(self):
            pass


class PinAgent:
    """`async with PinAgent(pin) as agent:` then `await agent.pair(device_path)`.

    BlueZ asks the agent registered by the connection that calls Pair() before
    it falls back to the default one (the desktop's dialog), so the pairing is
    requested from the same connection that holds this agent, not through
    bleak's own."""

    def __init__(self, pin):
        self.pin = pin
        self._bus = None
        self._agent = None

    async def __aenter__(self):
        if not DBUS_AVAILABLE or self.pin is None:
            return self
        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            self._agent = _Agent(self.pin)
            self._bus.export(AGENT_PATH, self._agent)
            intro = await self._bus.introspect("org.bluez", "/org/bluez")
            manager = self._bus.get_proxy_object(
                "org.bluez", "/org/bluez", intro).get_interface("org.bluez.AgentManager1")
            await manager.call_register_agent(AGENT_PATH, "KeyboardOnly")
            await manager.call_request_default_agent(AGENT_PATH)
        except Exception as exc:  # noqa: BLE001 - fall back to the system agent
            logger.warning(f"BlueZ PIN agent not available ({exc}); pairing may prompt")
            await self._close()
        return self

    async def __aexit__(self, *exc):
        await self._close()

    async def _close(self):
        bus, self._bus = self._bus, None
        if bus is None:
            return
        try:
            intro = await bus.introspect("org.bluez", "/org/bluez")
            manager = bus.get_proxy_object(
                "org.bluez", "/org/bluez", intro).get_interface("org.bluez.AgentManager1")
            await manager.call_unregister_agent(AGENT_PATH)
        except Exception:  # noqa: BLE001
            pass
        bus.disconnect()

    async def pair(self, device_path: str) -> None:
        """Device1.Pair() from this agent's own connection."""
        if self._bus is None:
            raise RuntimeError("PIN agent is not registered")
        intro = await self._bus.introspect("org.bluez", device_path)
        device = self._bus.get_proxy_object("org.bluez", device_path, intro)
        await device.get_interface("org.bluez.Device1").call_pair()

    @property
    def registered(self) -> bool:
        return self._bus is not None

    @property
    def answered(self) -> int:
        return self._agent.answered if self._agent else 0
