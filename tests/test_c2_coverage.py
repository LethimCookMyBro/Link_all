import io
import json
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from C2.C2 import C2APIHandler, ClientManager, ConnectionHealth, handle_client_connection, keepalive_handler, interact_with_client, start_api_server, discord_logger, discord_send_file


class C2DeepCoverageTests(unittest.TestCase):
    """Deep unit & branch coverage tests for C2/C2.py."""

    def test_connection_health_metrics_and_stats(self):
        ch = ConnectionHealth()
        # Initially empty
        stats = ch.get_stats()
        self.assertEqual(stats["total_commands"], 0)

        # Record success and failure commands
        ch.record_command(True, 0.5)
        ch.record_command(False, 2.0)
        ch.record_command(True, 0.1)

        self.assertEqual(ch.successful_commands, 2)
        self.assertEqual(ch.failed_commands, 1)
        self.assertGreater(ch.get_avg_latency(), 0)

        stats = ch.get_stats()
        self.assertEqual(stats["total_commands"], 3)
        self.assertIn("%", stats["quality"])

    def test_client_manager_add_remove_and_credentials(self):
        cm = ClientManager()
        mock_conn = MagicMock()

        # Test invalid password rejection
        cm._recv_message = MagicMock(return_value=b"WrongPassword")
        cid = cm.add_client(mock_conn, ("10.0.0.1", 1111))
        self.assertIsNone(cid)

        # Test credential extraction failure
        cm._recv_message = MagicMock(side_effect=Exception("Socket drop"))
        cid = cm.add_client(mock_conn, ("10.0.0.1", 1111))
        self.assertIsNone(cid)

        # Test valid connection & username
        cm._recv_message = MagicMock(side_effect=[b"PhantomLink", b"AdminUser"])
        with patch("C2.C2.discord_logger") as mock_log, patch("C2.C2.Notify") as mock_notify:
            cid1 = cm.add_client(mock_conn, ("10.0.0.1", 1111))
            self.assertIsNotNone(cid1)
            self.assertTrue(cm.is_client_connected(cid1))
            self.assertEqual(cm.clients[cid1]["username"], "AdminUser")

            # Test duplicate connection auto-replacement
            mock_conn_new = MagicMock()
            cm._recv_message = MagicMock(side_effect=[b"PhantomLink", b"AdminUser"])
            cid2 = cm.add_client(mock_conn_new, ("10.0.0.1", 2222))
            self.assertIsNotNone(cid2)
            self.assertNotEqual(cid1, cid2)
            self.assertFalse(cm.is_client_connected(cid1))

            # Test update_last_seen and increment failure
            cm.update_last_seen(cid2)
            self.assertEqual(cm.increment_keepalive_failure(cid2), 1)

            # Test remove_client
            cm.remove_client(cid2)
            self.assertFalse(cm.is_client_connected(cid2))

    def test_client_manager_framing_and_socket_receiving(self):
        cm = ClientManager()
        mock_conn = MagicMock()

        # Send string
        self.assertTrue(cm._send_message(mock_conn, "Hello"))

        # Recv exactly timeout / empty
        mock_conn.recv.return_value = b""
        self.assertIsNone(cm._recv_exactly(mock_conn, 4))

        # Recv HTTP request detection (GET / POST)
        mock_conn.recv.side_effect = [b"GET ", b"index.html"]
        self.assertIsNone(cm._recv_message(mock_conn))

        # Recv payload struct error
        mock_conn.recv.side_effect = [b"AB"]
        self.assertIsNone(cm._recv_message(mock_conn))

    def test_c2_api_handler_do_get_and_do_post_branches(self):
        cm = ClientManager()
        handler = C2APIHandler.__new__(C2APIHandler)
        handler.client_manager = cm
        handler._set_headers = MagicMock()
        handler.wfile = io.BytesIO()

        # Auth check fail
        handler.headers = {"X-API-Key": "INVALID"}
        handler.do_GET()
        handler._set_headers.assert_called_with(401)

        # 404 GET
        handler.headers = {"X-API-Key": "PhantomLink-API-2026"}
        handler.path = "/api/unknown"
        handler.do_GET()
        handler._set_headers.assert_called_with(404)

        # 404 POST
        handler.path = "/api/unknown"
        handler.do_POST()
        handler._set_headers.assert_called_with(404)

        # /api/command with target client not found
        payload = json.dumps({"command": "whoami", "target": "999"}).encode()
        handler.path = "/api/command"
        handler.headers = {"Content-Length": str(len(payload)), "X-API-Key": "PhantomLink-API-2026"}
        handler.rfile = io.BytesIO(payload)
        handler.wfile = io.BytesIO()
        handler.do_POST()

        res = json.loads(handler.wfile.getvalue().decode())
        self.assertEqual(res["results"][0]["status"], "not_found")

    def test_keepalive_handler_loop_and_failures(self):
        cm = ClientManager()
        mock_conn = MagicMock()
        cm._recv_message = MagicMock(side_effect=[b"PhantomLink", b"User1"])
        cid = cm.add_client(mock_conn, ("127.0.0.1", 3333))

        stop_event = threading.Event()

        # Mock PING send success and PONG receive
        cm._send_message = MagicMock(return_value=True)
        cm._recv_message = MagicMock(return_value=b"PONG")

        # Run keepalive thread briefly then stop
        t = threading.Thread(target=keepalive_handler, args=(cm, cid, stop_event))
        t.start()
        time.sleep(0.1)
        stop_event.set()
        t.join(timeout=2)

    def test_discord_helpers(self):
        with patch("requests.post") as mock_post:
            discord_logger("Test log message" * 200)
            self.assertTrue(mock_post.called)

        with patch("requests.post") as mock_post, patch("builtins.open", unittest.mock.mock_open(read_data=b"filedata")):
            discord_send_file("test.png", "msg")
            self.assertTrue(mock_post.called)


if __name__ == "__main__":
    unittest.main()
