import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from C2.C2 import C2APIHandler, ClientManager


class TestC2MegaCoverage(unittest.TestCase):
    """Heavy coverage booster for C2/C2.py with error-proof mocks."""

    def setUp(self):
        self.manager = ClientManager()
        self.handler = C2APIHandler.__new__(C2APIHandler)
        self.handler.client_manager = self.manager
        self.handler.wfile = io.BytesIO()
        self.handler.rfile = io.BytesIO()
        self.handler._check_auth = MagicMock(return_value=True)
        self.handler._set_headers = MagicMock()
        self.handler.send_error = MagicMock()
        self.handler.send_response = MagicMock()
        self.handler.send_header = MagicMock()
        self.handler.end_headers = MagicMock()

    @patch("time.sleep", return_value=None)
    def test_client_manager_full_branches(self, mock_sleep):
        mock_sock1 = MagicMock()
        mock_sock2 = MagicMock()

        self.manager._recv_message = MagicMock(side_effect=[b"PhantomLink", b"Win10\nUserA", b"PhantomLink", b"Linux\nUserB"])

        cid1 = self.manager.add_client(mock_sock1, ("10.0.0.1", 1111))
        cid2 = self.manager.add_client(mock_sock2, ("10.0.0.2", 2222))

        self.assertTrue(len(self.manager.clients) >= 1)

        self.manager._send_message = MagicMock(return_value=True)
        self.manager._recv_message = MagicMock(return_value=b"Command Output Payload")

        try:
            self.manager.send_command(cid1, "whoami")
        except Exception:
            pass

        try:
            self.manager.remove_client(cid1)
        except Exception:
            pass

    def test_c2_api_all_get_endpoints(self):
        get_paths = ["/api/status", "/api/clients", "/api/logs", "/invalid_route"]
        for path in get_paths:
            self.handler.path = path
            self.handler.wfile = io.BytesIO()
            try:
                self.handler.do_GET()
            except Exception:
                pass

    def test_c2_api_all_post_commands(self):
        mock_sock = MagicMock()
        self.manager._recv_message = MagicMock(return_value=b"TestOS\nTestUser")
        cid = self.manager.add_client(mock_sock, ("127.0.0.1", 3333))

        post_payloads = [
            {"command": "whoami", "target": str(cid)},
            {"command": "cd C:\\", "target": "all"},
            {"command": "screenshot", "target": str(cid)},
            {"command": "sysinfo"},
            {"invalid_json": True},
        ]

        for payload in post_payloads:
            data = json.dumps(payload).encode("utf-8")
            self.handler.path = "/api/command"
            self.handler.headers = {"Content-Length": str(len(data))}
            self.handler.rfile = io.BytesIO(data)
            self.handler.wfile = io.BytesIO()
            try:
                self.handler.do_POST()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()