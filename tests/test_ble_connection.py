import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from meshcore.ble_cx import (
    BLEConnection,
    UART_TX_CHAR_UUID,
    UART_RX_CHAR_UUID,
)

class TestBLEConnection(unittest.TestCase):
    @patch("meshcore.ble_cx.BleakClient")

    def test_ble_connection_and_disconnection(self, mock_bleak_client):
        """
        Tests the BLEConnection class for connecting and disconnecting from a BLE device.
        """
        # Arrange
        mock_client_instance = self._get_mock_bleak_client()
        mock_bleak_client.return_value = mock_client_instance

        address = "00:11:22:33:44:55"
        ble_conn = BLEConnection(address=address)

        # Act
        asyncio.run(ble_conn.connect())
        asyncio.run(ble_conn.disconnect())

        # Assert
        mock_client_instance.connect.assert_called_once()
        mock_client_instance.start_notify.assert_called_once_with(
            UART_TX_CHAR_UUID, ble_conn.handle_rx
        )
        mock_client_instance.disconnect.assert_called_once()

    @patch("meshcore.ble_cx.BleakClient")

    def test_send_data(self, mock_bleak_client):
        """
        Tests the send method of the BLEConnection class.
        """
        # Arrange
        mock_client_instance = self._get_mock_bleak_client()
        mock_bleak_client.return_value = mock_client_instance

        address = "00:11:22:33:44:55"
        ble_conn = BLEConnection(address=address)
        asyncio.run(ble_conn.connect())

        # Act
        data_to_send = b"Hello, BLE"
        asyncio.run(ble_conn.send(data_to_send))

        # Assert
        ble_conn.rx_char.write_gatt_char.assert_called_once_with(
            ble_conn.rx_char, data_to_send, response=True
        )

    def _get_mock_bleak_client(self):
        """
        Creates a mock BleakClient instance with all the necessary async methods and attributes.
        """
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.start_notify = AsyncMock()
        mock_client.write_gatt_char = AsyncMock()
        mock_client.is_connected = True
        mock_service = MagicMock()
        mock_char = MagicMock()
        mock_char.uuid = UART_RX_CHAR_UUID
        mock_char.write_gatt_char = mock_client.write_gatt_char 
        mock_service.get_characteristic.return_value = mock_char
        mock_client.services.get_service.return_value = mock_service

        return mock_client

if __name__ == '__main__':

    unittest.main()


class TestBLEDisconnectDuringConnect(unittest.TestCase):
    """bleak retries inside client.connect() when BlueZ aborts the first try
    (le-connection-abort-by-local); the aborted try's disconnect callback
    fires while that call is still running. It must not clear the client the
    successful retry is about to use, nor report a lost connection."""

    @patch("meshcore.ble_cx.BleakClient")
    def test_disconnect_callback_during_connect_is_ignored(self, mock_bleak_client):
        callbacks = []

        def make_client(address, disconnected_callback=None):
            client = MagicMock()
            client.address = address
            client.is_connected = True

            async def connect():
                # the aborted first try disconnects, then the retry connects
                disconnected_callback(client)

            client.connect = connect
            client.disconnect = AsyncMock()
            client.start_notify = AsyncMock()
            service = MagicMock()
            service.get_characteristic.return_value = MagicMock()
            client.services.get_service.return_value = service
            return client

        mock_bleak_client.side_effect = make_client
        ble_conn = BLEConnection(address="00:11:22:33:44:55")
        ble_conn.set_disconnect_callback(AsyncMock(side_effect=lambda r: callbacks.append(r)))

        async def run():
            result = await ble_conn.connect()
            await asyncio.sleep(0)   # let any background task run
            return result

        result = asyncio.run(run())

        self.assertEqual("00:11:22:33:44:55", result)
        ble_conn.client.start_notify.assert_called_once()
        self.assertEqual([], callbacks)

    @patch("meshcore.ble_cx.BleakClient")
    def test_disconnect_after_connect_is_reported(self, mock_bleak_client):
        client = MagicMock()
        client.address = "00:11:22:33:44:55"
        client.connect = AsyncMock()
        client.start_notify = AsyncMock()
        client.services.get_service.return_value = MagicMock()
        mock_bleak_client.return_value = client
        callbacks = []
        ble_conn = BLEConnection(address="00:11:22:33:44:55")
        ble_conn.set_disconnect_callback(AsyncMock(side_effect=lambda r: callbacks.append(r)))

        async def run():
            await ble_conn.connect()
            ble_conn.handle_disconnect(client)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        asyncio.run(run())

        self.assertEqual(["ble_disconnect"], callbacks)
        self.assertIsNone(ble_conn.client)   # reset for the next connect
