import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from meshcore.ble_cx import (
    BLEConnection,
    UART_SERVICE_UUID,
    UART_TX_CHAR_UUID,
    UART_RX_CHAR_UUID,
)


class _NoAgent:
    """Stands in for the BlueZ agent so unit tests never touch the system bus."""

    registered = False
    answered = 0

    def __init__(self, pin):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TestBLEPinPairing(unittest.TestCase):
    """Test BLE PIN pairing functionality"""

    @patch("meshcore.ble_cx.PinAgent", _NoAgent)
    @patch("meshcore.ble_cx.BleakClient")
    def test_ble_connection_with_pin_successful_pairing(self, mock_bleak_client):
        """Test BLE connection with PIN when pairing succeeds"""
        # Arrange
        mock_client_instance = self._get_mock_bleak_client()
        mock_bleak_client.return_value = mock_client_instance

        address = "00:11:22:33:44:55"
        pin = "123456"
        ble_conn = BLEConnection(address=address, pin=pin)

        # Act
        result = asyncio.run(ble_conn.connect())

        # Assert
        mock_client_instance.connect.assert_called_once()
        mock_client_instance.pair.assert_called_once()
        mock_client_instance.start_notify.assert_called_once_with(
            UART_TX_CHAR_UUID, ble_conn.handle_rx
        )
        self.assertEqual(result, address)

    @patch("meshcore.ble_cx.PinAgent", _NoAgent)
    @patch("meshcore.ble_cx.BleakClient")
    def test_ble_connection_with_pin_failed_pairing(self, mock_bleak_client):
        """Test BLE connection with PIN when pairing fails — re-raises (F17)."""
        # Arrange
        mock_client_instance = self._get_mock_bleak_client()
        mock_client_instance.pair = AsyncMock(side_effect=Exception("Pairing failed"))
        mock_bleak_client.return_value = mock_client_instance

        address = "00:11:22:33:44:55"
        pin = "123456"
        ble_conn = BLEConnection(address=address, pin=pin)

        # Act & Assert — pairing failure now re-raises instead of being
        # swallowed, because a half-usable transport is worse than a clean
        # failure (forensics finding F17).
        with self.assertRaises(Exception) as ctx:
            asyncio.run(ble_conn.connect())
        self.assertIn("Pairing failed", str(ctx.exception))
        mock_client_instance.connect.assert_called_once()
        mock_client_instance.pair.assert_called_once()
        # disconnect should be called to clean up the failed connection
        mock_client_instance.disconnect.assert_called_once()

    @patch("meshcore.ble_cx.BleakClient")
    def test_ble_connection_without_pin_no_pairing(self, mock_bleak_client):
        """Test BLE connection without PIN - no pairing should be attempted"""
        # Arrange
        mock_client_instance = self._get_mock_bleak_client()
        mock_bleak_client.return_value = mock_client_instance

        address = "00:11:22:33:44:55"
        ble_conn = BLEConnection(address=address)

        # Act
        result = asyncio.run(ble_conn.connect())

        # Assert
        mock_client_instance.connect.assert_called_once()
        mock_client_instance.pair.assert_not_called()
        mock_client_instance.start_notify.assert_called_once_with(
            UART_TX_CHAR_UUID, ble_conn.handle_rx
        )
        self.assertEqual(result, address)

    @patch("meshcore.ble_cx.BleakClient")
    def test_ble_connection_pin_constructor_parameter(self, mock_bleak_client):
        """Test that PIN parameter is properly stored in constructor"""
        # Arrange
        address = "00:11:22:33:44:55"
        pin = "654321"

        # Act
        ble_conn = BLEConnection(address=address, pin=pin)

        # Assert
        self.assertEqual(ble_conn.pin, pin)
        self.assertEqual(ble_conn.address, address)

    def _get_mock_bleak_client(self):
        """
        Creates a mock BleakClient instance with all the necessary async methods and attributes.
        """
        mock_client = MagicMock()
        mock_client.address = "00:11:22:33:44:55"
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.start_notify = AsyncMock()
        mock_client.write_gatt_char = AsyncMock()
        mock_client.pair = AsyncMock()
        mock_client.is_connected = True

        mock_service = MagicMock()
        mock_char = MagicMock()
        mock_char.uuid = UART_RX_CHAR_UUID
        mock_char.write_gatt_char = mock_client.write_gatt_char
        mock_service.get_characteristic.return_value = mock_char
        mock_client.services.get_service.return_value = mock_service

        return mock_client


if __name__ == "__main__":
    unittest.main()

class _RegisteredAgent(_NoAgent):
    registered = True
    paired = []

    async def pair(self, device_path):
        _RegisteredAgent.paired.append(device_path)


class TestBLEPinAgentPairing(TestBLEPinPairing):
    @patch("meshcore.ble_cx.PinAgent", _RegisteredAgent)
    @patch("meshcore.ble_cx.BleakClient")
    def test_pairing_goes_through_the_agents_own_connection(self, mock_bleak_client):
        _RegisteredAgent.paired.clear()
        client = self._get_mock_bleak_client()
        client._backend._device_path = "/org/bluez/hci0/dev_00_11_22_33_44_55"
        mock_bleak_client.return_value = client
        ble_conn = BLEConnection(address="00:11:22:33:44:55", pin="123456")

        asyncio.run(ble_conn.connect())

        self.assertEqual(_RegisteredAgent.paired, ["/org/bluez/hci0/dev_00_11_22_33_44_55"])
        client.pair.assert_not_called()
