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


class C2CleanBoostTests(unittest.TestCase):
    """Deterministic, zero-hang branch coverage tests for C2/C2.py."""

    def setUp(self):
        self.manager = c2_mod.ClientManager()

    def test_client_manager_add_remove_reconnect(self):
        cm = self.manager
        mock_sock1 = MagicMock()
        mock_sock2 = MagicMock()

        # Recv password PhantomLink + username User1
        cm._recv_message = MagicMock(side_effect=[b"PhantomLink", b"User1"])
        with patch("C2.C2.discord_logger"), patch("C2.C2.Notify"):
            cid1 = cm.add_client(mock_sock1, ("192.168.1.10", 1111))
            self.assertIsNotNone(cid1)

        # Duplicate IP connect -> remove old
        cm._recv_message = MagicMock(side_effect=[b"PhantomLink", b"User1"])
        with patch("C2.C2.discord_logger"), patch("C2.C2.Notify"):
            cid2 = cm.add_client(mock_sock2, ("192.168.1.10", 2222))
            self.assertIsNotNone(cid2)
            self.assertNotEqual(cid1, cid2)

        self.assertTrue(cm.is_client_connected(cid2))
        cm.remove_client(cid2)
        self.assertFalse(cm.is_client_connected(cid2))

    def test_c2_api_handler_do_get_and_post_clean(self):
        cm = self.manager
        handler = c2_mod.C2APIHandler.__new__(c2_mod.C2APIHandler)
        handler.client_manager = cm
        handler._set_headers = MagicMock()
        handler.wfile = io.BytesIO()

        # GET /api/status
        handler.headers = {"X-API-Key": "PhantomLink-API-2026"}
        handler.path = "/api/status"
        handler.do_GET()
        res = json.loads(handler.wfile.getvalue().decode())
        self.assertEqual(res.get("status"), "ok")

        # GET /api/clients
        handler.path = "/api/clients"
        handler.wfile = io.BytesIO()
        handler.do_GET()
        res = json.loads(handler.wfile.getvalue().decode())
        self.assertIn("clients", res)

        # POST /api/command with non-existent target
        data = json.dumps({"command": "dir", "target": "999"}).encode()
        handler.path = "/api/command"
        handler.headers = {"Content-Length": str(len(data)), "X-API-Key": "PhantomLink-API-2026"}
        handler.rfile = io.BytesIO(data)
        handler.wfile = io.BytesIO()
        handler.do_POST()
        res = json.loads(handler.wfile.getvalue().decode())
        self.assertEqual(res["results"][0]["status"], "not_found")


if __name__ == "__main__":
    unittest.main()
